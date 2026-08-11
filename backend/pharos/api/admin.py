"""Administrator console — user administration and provider visibility.

Two deliberate limits shape this module.

**It never returns an API key.** The console reports which providers are
configured, which model each will use, and whether the key *works* — never the
secret itself, not even masked beyond a last-four fingerprint. Keys live in the
server's environment (see :mod:`pharos.config`); an operator changes one by
editing ``.env`` and restarting, which keeps the secret out of the database, out
of every HTTP response, and out of any backup of either.

**It cannot lock the operator out.** Every destructive action on an account
refuses when the target is the caller or the last remaining administrator, so
there is no sequence of clicks that leaves an instance with nobody able to
administer it. Recovering from that would need database surgery.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pharos.api.deps import get_library, get_session, get_settings, require_admin
from pharos.config import Settings
from pharos.db.models import Paper, User
from pharos.services.library import LibraryService

router = APIRouter(prefix="/api/admin", tags=["admin"])

#: How long a provider connectivity probe may take. Short enough that a hung
#: vendor cannot hold an admin request open, long enough for a cold TLS
#: handshake to a distant relay.
_PROBE_TIMEOUT = 10.0

#: Cap on a page of users. The console is for looking at people, not for bulk
#: export, and an unbounded page is a cheap way to make the server do work.
_MAX_PAGE = 200


# --------------------------------------------------------------------------- #
# response models
# --------------------------------------------------------------------------- #


class AdminUserOut(BaseModel):
    """One account as the console shows it.

    Built field by field rather than from the ORM row so that adding a column to
    ``User`` — a password hash, a token, a recovery code — cannot silently start
    serialising it to an admin screen.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    display_name: str | None
    is_admin: bool
    is_active: bool
    pdf_translation: bool
    created_at: datetime
    last_login_at: datetime | None


class AdminUserPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[AdminUserOut]
    total: int
    limit: int
    offset: int


class AdminStats(BaseModel):
    """Account administration totals, never research-library activity."""

    model_config = ConfigDict(extra="forbid")

    users: int
    admins: int
    inactive_users: int
    #: Whether strangers can currently register (``PHAROS_ALLOW_REGISTRATION``).
    allow_registration: bool


class ProviderOut(BaseModel):
    """A model provider as configured on the server.

    ``key_hint`` is the last four characters of the key and nothing else: enough
    to tell two keys apart when rotating one, useless to anybody who intercepts
    the response.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    base_url: str | None
    model: str
    configured: bool
    key_hint: str | None
    #: Which jobs this provider currently serves ("translate", "chat").
    roles: list[str]


class ProvidersOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[ProviderOut]
    #: Provider names selected for each task, whether or not they are usable.
    translator: str
    chat_provider: str
    #: The engine actually in force for translation, after the fallback in
    #: ``Settings.translator_config`` — "bing" here while ``translator`` says
    #: "deepseek" means the key is missing and translation silently degraded.
    effective_translator: str


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ok: bool
    #: Round-trip time in milliseconds when the probe succeeded.
    latency_ms: int | None
    #: A short human-readable reason when it did not.
    detail: str | None


class UserPatch(BaseModel):
    """Fields an administrator may change on someone else's account.

    Notably absent: ``email`` and ``password``. Changing another person's login
    identifier or credential from a console is an account takeover with an audit
    trail, and Pharos has no re-verification flow to make it legitimate.
    """

    model_config = ConfigDict(extra="forbid")

    is_admin: bool | None = None
    is_active: bool | None = None
    pdf_translation: bool | None = None
    display_name: Annotated[str, Field(max_length=128)] | None = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _admin_count(session: Session) -> int:
    return int(
        session.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True))) or 0
    )


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=bool(user.is_admin),
        is_active=bool(user.is_active),
        pdf_translation=bool(user.pdf_translation),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _probe_provider(base_url: str | None, api_key: str, model: str) -> ProbeResult:
    """Ask a provider to answer one trivial prompt, and report what happened.

    A real (tiny) completion rather than a bare TCP connect: a reachable host
    proves nothing about whether the key is accepted or the model name exists,
    which are the two things that actually break. Blocking on purpose — the
    caller runs it in a worker thread.
    """
    root = (base_url or "").rstrip("/")
    if not root:
        return ProbeResult(name="", ok=False, latency_ms=None, detail="缺少 base_url")
    # Both first-party and relay endpoints speak this path; some base URLs
    # already carry the /v1 segment, so only add it when it is absent.
    url = f"{root}/chat/completions" if root.endswith("/v1") else f"{root}/v1/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = datetime.now()
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT) as response:
            response.read(2048)
        elapsed = int((datetime.now() - started).total_seconds() * 1000)
        return ProbeResult(name="", ok=True, latency_ms=elapsed, detail=None)
    except urllib.error.HTTPError as error:
        # The vendor answered, so the endpoint and network are fine — the status
        # tells the operator whether it was the key or the model name.
        reason = {
            401: "密钥被拒绝（401）",
            403: "密钥无权限（403）",
            404: "模型或路径不存在（404）",
            429: "触发限流（429）",
        }.get(error.code, f"HTTP {error.code}")
        return ProbeResult(name="", ok=False, latency_ms=None, detail=reason)
    except urllib.error.URLError as error:
        return ProbeResult(name="", ok=False, latency_ms=None, detail=f"无法连接：{error.reason}")
    except Exception as error:  # noqa: BLE001 - a probe must never 500 the console
        return ProbeResult(name="", ok=False, latency_ms=None, detail=f"探测失败：{error}")


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #


@router.get("/stats", response_model=AdminStats)
def instance_stats(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminStats:
    """Account totals and registration state.

    The administrator console governs accounts and model providers. It does not
    inventory papers, projects, searches, annotations, or any other research
    activity -- those belong to the researcher, not to instance operations.
    """
    count = lambda stmt: int(session.scalar(stmt) or 0)  # noqa: E731 - local shorthand
    return AdminStats(
        users=count(select(func.count()).select_from(User)),
        admins=_admin_count(session),
        inactive_users=count(
            select(func.count()).select_from(User).where(User.is_active.is_(False))
        ),
        allow_registration=bool(settings.allow_registration),
    )


@router.get("/users", response_model=AdminUserPage)
def list_users(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUserPage:
    """Every account, newest first, with account metadata only."""
    where = []
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        where.append(
            func.lower(User.email).like(needle)
            | func.lower(func.coalesce(User.display_name, "")).like(needle)
        )

    total = int(session.scalar(select(func.count()).select_from(User).where(*where)) or 0)
    users = list(
        session.scalars(
            select(User).where(*where).order_by(User.created_at.desc()).limit(limit).offset(offset)
        ).all()
    )

    return AdminUserPage(
        users=[_user_out(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: str,
    payload: Annotated[UserPatch, Body()],
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> AdminUserOut:
    """Change another account's role, status, or translation setting."""
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    provided = payload.model_dump(exclude_unset=True)
    if not provided:
        raise HTTPException(status_code=400, detail="No fields to update")

    # --- the two lockout guards -------------------------------------------
    # Demoting or deactivating yourself is refused outright rather than merely
    # warned about: the click that does it is also the click that removes your
    # ability to undo it.
    if target.id == admin.id:
        if provided.get("is_admin") is False:
            raise HTTPException(
                status_code=409, detail="不能撤销自己的管理员权限，请由另一位管理员操作"
            )
        if provided.get("is_active") is False:
            raise HTTPException(status_code=409, detail="不能停用自己的账户")
    # Even acting on somebody else, the last administrator must survive.
    removing_admin = target.is_admin and (
        provided.get("is_admin") is False or provided.get("is_active") is False
    )
    if removing_admin and _admin_count(session) <= 1:
        raise HTTPException(status_code=409, detail="这是最后一位管理员，撤销后将无人可管理此实例")

    if "is_admin" in provided:
        target.is_admin = bool(provided["is_admin"])
    if "pdf_translation" in provided:
        target.pdf_translation = bool(provided["pdf_translation"])
    if "display_name" in provided:
        name = provided["display_name"]
        target.display_name = name.strip() or None if isinstance(name, str) else None
    if "is_active" in provided:
        active = bool(provided["is_active"])
        if active != bool(target.is_active):
            target.is_active = active
            # Deactivation must take effect now, not whenever the token happens
            # to expire, so every outstanding session for that account dies.
            target.token_epoch = int(target.token_epoch or 0) + 1
    session.flush()

    return _user_out(target)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
    library: Annotated[LibraryService, Depends(get_library)],
    confirm_email: Annotated[str, Query(max_length=320)],
) -> None:
    """Permanently delete an account and its server-side Pharos data.

    ``confirm_email`` must equal the target's address. The console already asks
    the operator to type it, but the check belongs here too: this is the one
    endpoint in Pharos that destroys another person's server-side account data,
    and a mistyped id in a script should fail rather than silently erase the
    wrong account.

    **Papers are purged one by one rather than left to the database cascade.**
    ``papers.user_id`` is ``ON DELETE CASCADE``, so a bare ``DELETE FROM users``
    would remove every row and leave every PDF behind as an orphaned directory —
    the blobs are content-addressed and owned by no row in particular. Worse,
    naively deleting the files instead would destroy PDFs that a *different*
    user's paper still points at, because two researchers who upload the same
    paper share one blob. ``LibraryService.purge`` already resolves exactly this:
    it reference-counts across all users and defers the unlink until after the
    commit. Reusing it is what makes deletion both complete and safe.

    Everything else the account owns on the server — projects, searches,
    highlights, notes, conversations and credentials — is metadata with no
    files of its own, so the FK cascade is the right tool for those. Local
    Zotero and Pharos libraries are never read by this endpoint and are not
    affected.
    """
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="不能删除自己的账户，请由另一位管理员操作")
    if target.is_admin and _admin_count(session) <= 1:
        raise HTTPException(status_code=409, detail="这是最后一位管理员，删除后将无人可管理此实例")
    if confirm_email.strip().casefold() != target.email:
        raise HTTPException(status_code=400, detail="确认邮箱与该账户不一致，未执行删除")

    # Purge the owned papers first, while the rows still exist to be counted.
    papers = list(session.scalars(select(Paper).where(Paper.user_id == target.id)).all())
    for paper in papers:
        library.purge(session, paper)

    session.delete(target)
    session.flush()


@router.get("/providers", response_model=ProvidersOut)
def list_providers(
    _admin: Annotated[User, Depends(require_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProvidersOut:
    """Which model providers this server is configured with.

    Read-only by design: keys come from the environment, so this reports the
    running configuration rather than offering to edit it. The secret never
    appears — only whether one is present and its last four characters.
    """
    labels = {
        "deepseek": "DeepSeek",
        "openai": "OpenAI / 兼容中转",
        "custom": "自定义端点",
    }
    translator = settings.translator_type.lower()
    chat = settings.chat_provider.lower()

    providers: list[ProviderOut] = []
    for name, provider in settings.providers().items():
        roles = []
        if translator == name:
            roles.append("translate")
        if chat == name:
            roles.append("chat")
        key = provider.api_key or ""
        providers.append(
            ProviderOut(
                name=name,
                label=labels.get(name, name),
                base_url=provider.base_url,
                model=provider.model,
                configured=provider.configured,
                key_hint=key[-4:] if len(key) >= 4 else None,
                roles=roles,
            )
        )

    return ProvidersOut(
        providers=providers,
        translator=translator,
        chat_provider=chat,
        effective_translator=settings.translator_config().type,
    )


@router.post("/providers/{name}/probe", response_model=ProbeResult)
async def probe_provider(
    name: str,
    _admin: Annotated[User, Depends(require_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProbeResult:
    """Send one minimal completion to a provider and report the outcome.

    This is the only way to distinguish "a key is configured" from "the key
    works" — a typo'd key and a decommissioned relay both look perfectly healthy
    in the configuration listing.
    """
    provider = settings.providers().get(name)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    if not provider.configured:
        return ProbeResult(name=name, ok=False, latency_ms=None, detail="未配置密钥或模型")

    result = await asyncio.to_thread(
        _probe_provider, provider.base_url, provider.api_key or "", provider.model
    )
    return result.model_copy(update={"name": name})
