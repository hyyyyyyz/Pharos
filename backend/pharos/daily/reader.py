"""The LLM reading layer for 每日论文 — abstract in, structured card out.

In the origin project (``hyyyyyyz/embodied-arxiv``) this step *was Claude in a
terminal conversation*: the user ran a skill, Claude read each abstract inline
and hand-wrote the cards. That is exactly why the digest stalls the moment the
user stops running it. This module replaces that human-triggered step with a
server-side API call so the sweep keeps producing cards unattended.

Two invariants drive every design choice here:

**Never fabricate.** If no provider is configured, :func:`read_paper` raises
:class:`ReaderUnavailable` immediately and the caller leaves the paper
``pending``. If a provider answers with something that does not validate, we
raise rather than patch the hole — the caller records ``read_status="error"``
with the reason. A visibly unread paper is a correct outcome; an invented
summary is a serious defect that silently poisons the user's reading list.

**Purity.** ``read_paper`` is input -> :class:`Reading` with no database access
and no global state beyond settings, so "re-read this paper with a better
model" is a loop over rows rather than a new code path.

**A reading is about the paper, never about a reader.** One arXiv paper is read
once and the resulting card is shown to every account on the instance, so
nothing the model produces here may depend on who is looking. That is why this
module no longer asks for a ``relevance`` score. It used to: the rubric was
built from the global ``DIRECTIONS`` table back when the operator's interests
*were* everyone's interests. With directions configured per user that number
would be a personalised-looking figure computed against somebody else's reading
list — the single most misleading thing this module could emit. Relevance is now
derived per caller at query time from their own keywords, by
:func:`pharos.daily.user_directions.relevance_for`. The scores that remain
(``recency``, ``popularity``, ``quality``, ``recommendation``) are all statements
about the paper itself and mean the same thing to every reader.

We use :mod:`urllib.request` rather than ``httpx``, matching
:mod:`pharos.services.enrich`: httpx is not a declared dependency of the
backend (it arrives only transitively via FastAPI), and one blocking POST does
not justify taking it on directly. Being consistent with the module that
already made this call is worth more than the ergonomics of httpx.

Every call is blocking, so async callers must dispatch it to a worker thread
(``anyio.to_thread.run_sync``) rather than awaiting it on the event loop.
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pharos.config import get_settings

__all__ = [
    "Reading",
    "ReaderError",
    "ReaderUnavailable",
    "ProviderError",
    "InvalidReading",
    "is_available",
    "read_paper",
]

log = logging.getLogger(__name__)

#: Keys the card's ``highlights`` object must carry, in the user's order.
HIGHLIGHT_KEYS: tuple[str, ...] = ("contribution", "innovation", "method", "results")

#: Keys the card's ``scores`` object must carry.
#:
#: ``relevance`` is deliberately absent, and its absence is the point. A reading
#: is shared by every account on the instance (see the module docstring), so a
#: relevance judgement made here would be answering "relevant to whom?" with the
#: wrong reader. The API layer supplies a per-caller ``relevance`` and a
#: per-caller ``recommendation`` when it renders the card; a model that emits
#: ``relevance`` anyway is not an error, it is simply ignored — :func:`_validate`
#: only reads the keys listed here.
SCORE_KEYS: tuple[str, ...] = ("recency", "popularity", "quality", "recommendation")


_TEMPERATURE = 0.4
_MAX_TOKENS = 1600

# Abstracts are ~1-2k characters; anything far beyond that is a malformed
# record, and sending it would just burn tokens for no extra signal.
_MAX_ABSTRACT_CHARS = 6000
# Author lists run to 30+ names on large collaborations. The first several
# carry the lab signal that `popularity` keys off; the tail adds nothing.
_MAX_AUTHORS = 12

# A chat completion is a few KB. A body far past that is a broken or hostile
# endpoint, and buffering it unbounded would be the bug.
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024

# Retry only failures that are plausibly transient. Unlike enrich.py — which
# runs inside a user-facing upload and deliberately never retries — this runs
# in an unattended batch over dozens of papers, where a single 429 would
# otherwise mark a whole day's sweep as "error" for no good reason.
_RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 2.0

# Language gate. An answer in English means the model ignored the instruction;
# that is a failure, not a result. But Chinese cards legitimately carry heavy
# Latin technical vocabulary ("LIBERO-Pro 上比最强基线高 38.6 个点"), so the
# gate is a *floor*, not a purity test. Measured over the 1475 hand-written
# cards in the origin project, the CJK share of letter characters bottoms out
# at 0.251 for summaries and 0.184 for highlights; these floors sit well below
# both while still rejecting all-English output, which scores 0.0.
_MIN_CJK_RATIO_SUMMARY = 0.15
_MIN_CJK_RATIO_HIGHLIGHTS = 0.10
# Guards the ratio against a degenerate short string ("好。" would score 1.0).
_MIN_CJK_CHARS = 8

# A one-line summary is not the 2-4 sentence card the user reads, and a
# thousand-character wall means the model dumped the abstract back.
_MIN_SUMMARY_CHARS = 30
_MAX_SUMMARY_CHARS = 1200


SYSTEM_PROMPT = """\
你是一位资深研究者，正在帮同事做每日 arXiv 论文速读。

**首先要清楚一件事：这张卡片是共享的。** 同一篇论文只读一次，产出的卡片会\
呈现给这台实例上的每一位读者，而每位读者关注的研究方向各不相同、由他们自己\
配置。所以：

- **不要判断这篇论文跟读者的研究方向有多相关**，也不要输出 `relevance` 这类\
分数。相关性由系统在展示时按每位读者自己写下的关键词单独计算 —— 你在这里给出\
的任何相关性判断，都只会是「相关于某个不存在的人」。
- 你只评价**论文本身**：它写得怎么样、做得扎不扎实、值不值得同行花时间。

你要为每篇论文产出一张卡片，**只输出一个 JSON 对象**，不要任何解释性文字、\
不要 Markdown 代码围栏。JSON 结构如下：

{
  "summary_zh": "中文 2-4 句：动机 → 方法 → 结果。",
  "highlights": {
    "contribution": "核心贡献",
    "innovation":   "创新点",
    "method":       "方法概要",
    "results":      "关键结果"
  },
  "scores": {
    "recency": 0-10,
    "popularity": 0-10,
    "quality": 0-10,
    "recommendation": 0-10
  }
}

写作要求：

- **summary_zh**：中文，2-4 句，自然口语化，像同事端着咖啡跟你聊到这篇 paper。\
按「动机 → 方法 → 结果」组织。**绝对不要逐句翻译摘要** —— 要提炼、要说人话。\
专有名词（模型名、benchmark 名、指标名）保留英文原文，不要硬译。
- **highlights**：四条，每条 1-3 句中文。
  - `contribution`：作者声称解决了什么，一句话讲清楚。
  - `innovation`：相对已有工作的关键差异，取最关键的 1-2 点。
  - `method`：实际怎么做的 —— 输入 / 模型 / 损失 / 训练设置。**不要写公式**，\
写读者要点。
  - `results`：在哪个 benchmark 上、提升了多少、是否 SOTA。摘要里有具体数字就\
把数字写进去。
- **scores**：0-10，最多一位小数。全部只描述论文本身，**不涉及任何读者**。
  - `recency`：这条流水线在论文公布当天阅读，所以正常就是 10；\
只有明确在回填旧文时才按周衰减到 5。
  - `popularity`：从作者 / 实验室 / 课题热度估计。知名实验室（DeepMind、\
Stanford、Tsinghua、Meta AI 等）7-9；完全不认识的作者给 5-6，**不要留空**。
  - `quality`：写作清晰度 + 实验完整度。摘要里有多 benchmark、有消融、\
有具体数字的偏高；只有定性描述、没有实验的偏低。
  - `recommendation`：**抛开读者的研究方向不谈**，这篇论文本身有多值得读 ——\
选题重要性 + 结果说服力的总评。注意它不是最终显示在卡片角标上的分数：\
系统会把它连同每位读者自己算出的相关性重新加权。但它是「这篇论文有多重要」\
的唯一来源，所以请认真给。

**关于打分，最重要的一条**：分数必须有区分度。**别全给 7**。\
区分度本身就是用户真正在读的信号 —— 一列全是 7.0 的卡片等于没有评分。\
真实的一天里，recommendation 应该散布在 5.5 到 9.0 之间：\
只有真正重要、实验扎实、结论站得住的工作才配 8.5 以上；\
增量改进或实验单薄的就该老实给 5-6。请果断地拉开差距。

**诚实要求**：只根据给定的标题和摘要作答。摘要没提到的实验结果、数据集、\
数字，绝对不要编造 —— 宁可在 `results` 里写「摘要未给出具体数值」。\
"""


USER_PROMPT_TEMPLATE = """\
请阅读下面这篇论文并输出卡片 JSON。

标题：{title}
{domain_line}{authors_line}
摘要：
{abstract}
"""


@dataclass(frozen=True)
class Reading:
    """One validated card — the LLM's reading of a single paper.

    Frozen because a Reading is a record of what a specific model said at a
    specific time; mutating it after the fact would make ``read_model``
    (persisted alongside it) a lie.
    """

    summary_zh: str
    highlights: dict[str, str]
    scores: dict[str, float]
    model: str


class ReaderError(RuntimeError):
    """Base class for every reading failure, so callers can catch one type."""


class ReaderUnavailable(ReaderError):
    """No LLM provider is configured — callers leave the paper 'pending'.

    Distinct from :class:`ProviderError` on purpose: this is a *configuration*
    state, not a failure. The paper was never attempted, so the UI should say
    "未配置阅读模型" rather than showing it as errored.
    """


class ProviderError(ReaderError):
    """The provider could not be reached, or answered with something unusable."""


class InvalidReading(ReaderError):
    """The provider answered, but the card failed validation.

    Raised instead of repairing the response. Every field of a card is content
    the user will read and trust; a locally-invented replacement for a missing
    one is indistinguishable from a real reading once persisted.
    """


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #


def is_available() -> bool:
    """Whether a chat provider is configured well enough to attempt a reading.

    Cheap and side-effect free, so a batch caller can check once up front and
    skip straight to marking the day's papers ``pending`` instead of raising
    :class:`ReaderUnavailable` once per paper.
    """
    return get_settings().provider_for("chat") is not None


# --------------------------------------------------------------------------- #
# secret hygiene
# --------------------------------------------------------------------------- #


def _scrub(text: str, secret: str | None) -> str:
    """Remove ``secret`` from text destined for an exception or a log line.

    The key is only ever sent as an ``Authorization`` header, so it should
    never come back to us — but exception messages here get persisted verbatim
    into ``DailyPaper.read_error`` and rendered in the UI. That is a one-way
    door, so we scrub unconditionally rather than reasoning about which
    provider echoes what.
    """
    if not secret:
        return text
    return text.replace(secret, "***")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _endpoint(base_url: str | None) -> str:
    """Build the chat-completions URL for an OpenAI-compatible base.

    Both spellings in the settings work unchanged: OpenAI's base already ends
    in ``/v1`` and DeepSeek's does not, because DeepSeek documents
    ``https://api.deepseek.com/chat/completions`` as a valid endpoint. So a
    plain join is correct for every provider we ship, and a self-hosted relay
    just supplies whatever prefix it serves.
    """
    if not base_url:
        raise ProviderError("chat provider has no base_url configured")
    return base_url.rstrip("/") + "/chat/completions"


def _read_body(response: Any, deadline: float) -> bytes:
    """Read a response body under a wall-clock deadline.

    ``urlopen``'s timeout bounds each individual socket operation, not the call
    as a whole, so a server trickling bytes just inside that timeout can block
    far past it — the same trap already documented in
    :mod:`pharos.services.enrich`. Re-checking a deadline per chunk is what
    actually bounds the call.

    The read must be ``read1``, not ``read``. ``read(n)`` keeps issuing socket
    reads until it has filled *n* bytes or hit EOF, so the deadline below is
    only consulted once the whole trickle has finished: measured against a
    server dribbling 10 bytes every 0.3s, a 2s timeout blocked for 60s. Since
    ``read1`` returns whatever one socket read yielded, the loop re-checks the
    deadline at every chunk boundary and the timeout is honoured for real.
    """
    read1 = getattr(response, "read1", None) or response.read
    chunks: list[bytes] = []
    total = 0
    while total < _MAX_RESPONSE_BYTES:
        if time.monotonic() >= deadline:
            raise ProviderError("chat provider timed out while streaming the response body")
        chunk = read1(min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _post_once(
    url: str,
    payload: dict[str, Any],
    api_key: str | None,
    timeout: float,
) -> tuple[int, bytes]:
    """POST one chat completion, returning ``(status, body)``.

    HTTP errors are returned rather than raised so the caller can decide
    whether the status is retryable or fatal; only transport failures raise.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The key travels only here — never in the URL, never in the body.
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Pharos/0.1 (https://github.com/hyyyyyyz/Pharos)",
        },
    )
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status), _read_body(response, deadline)
    except urllib.error.HTTPError as exc:
        # An HTTPError is also a readable response; its body carries the
        # vendor's error detail, which is the most useful thing we can show.
        try:
            detail = exc.read(_MAX_RESPONSE_BYTES)
        except Exception:
            detail = b""
        return int(exc.code), detail
    except urllib.error.URLError as exc:
        raise ProviderError(f"chat provider unreachable: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise ProviderError(f"chat provider connection failed: {exc}") from None


def _mentions_response_format(body: bytes) -> bool:
    """Whether a 4xx body blames ``response_format``.

    JSON mode is best-effort: a self-hosted relay may reject the parameter
    outright. Detecting that lets us retry without it instead of failing a
    paper over a capability the prompt already covers.
    """
    return b"response_format" in body


def _request_reading(
    provider: Any,
    messages: list[dict[str, str]],
    timeout: float,
) -> str:
    """Call the provider and return the raw assistant message content."""
    url = _endpoint(provider.base_url)
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "temperature": _TEMPERATURE,
        "max_tokens": _MAX_TOKENS,
        # Best-effort: honoured by OpenAI and DeepSeek, dropped on 400 below.
        "response_format": {"type": "json_object"},
    }

    last_error = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        status, body = _post_once(url, payload, provider.api_key, timeout)

        if status == 200:
            return _content_from(body, provider.api_key)

        snippet = _scrub(body[:400].decode("utf-8", "replace").strip(), provider.api_key)

        # A relay that does not implement JSON mode: drop it and try again.
        if (
            status in (400, 422)
            and "response_format" in payload
            and _mentions_response_format(body)
        ):
            log.info("chat provider rejected response_format; retrying without JSON mode")
            payload.pop("response_format")
            continue

        last_error = f"HTTP {status}: {snippet}" if snippet else f"HTTP {status}"
        if status not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS:
            raise ProviderError(f"chat provider returned {last_error}")

        # Linear backoff. The batch is not latency-sensitive and a rate limit
        # clears on the order of seconds.
        time.sleep(_BACKOFF_SECONDS * attempt)

    raise ProviderError(f"chat provider failed after {_MAX_ATTEMPTS} attempts: {last_error}")


def _content_from(body: bytes, api_key: str | None) -> str:
    """Pull the assistant text out of a chat-completions envelope."""
    try:
        envelope = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(f"chat provider returned non-JSON body: {exc}") from None

    if not isinstance(envelope, dict):
        raise ProviderError("chat provider returned a JSON value that is not an object")

    # Some relays answer 200 with an error envelope instead of an HTTP status.
    error = envelope.get("error")
    if isinstance(error, dict):
        message = _scrub(str(error.get("message") or error), api_key)
        raise ProviderError(f"chat provider reported an error: {message}")

    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("chat provider returned no choices")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderError("chat provider returned a malformed choice")

    message_obj = choice.get("message")
    if not isinstance(message_obj, dict):
        raise ProviderError("chat provider returned a choice without a message")

    # An explicit refusal is a real outcome, not a parse failure — surface it
    # as-is so the user can see *why* the paper was not read.
    refusal = message_obj.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        raise ProviderError(f"chat provider refused to read the paper: {refusal.strip()}")

    content = message_obj.get("content")
    if not isinstance(content, str) or not content.strip():
        reason = choice.get("finish_reason")
        if reason == "length":
            raise ProviderError("chat provider hit the token limit before emitting a card")
        raise ProviderError(f"chat provider returned empty content (finish_reason={reason!r})")

    if choice.get("finish_reason") == "length":
        # Truncated mid-object: the JSON will not balance, and a partial card
        # is exactly the kind of half-truth we refuse to persist.
        raise ProviderError("chat provider truncated the card at the token limit")

    return content


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def _extract_json_object(text: str) -> dict[str, Any]:
    """Find and parse the outermost balanced ``{...}`` in ``text``.

    JSON mode is best-effort, so the content may be a bare object, an object
    inside a ```json fence, or an object with prose on either side. Scanning
    for balance (while tracking string state, so a brace inside a Chinese
    sentence or an escaped quote does not throw off the depth count) handles
    all three, where a greedy regex would swallow trailing prose and a lazy one
    would stop at the first nested closing brace.
    """
    start = text.find("{")
    if start == -1:
        raise InvalidReading("model response contained no JSON object")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blob = text[start : index + 1]
                try:
                    parsed = json.loads(blob)
                except ValueError as exc:
                    raise InvalidReading(f"model response was not valid JSON: {exc}") from None
                if not isinstance(parsed, dict):
                    raise InvalidReading("model response JSON was not an object")
                return parsed

    raise InvalidReading("model response contained an unterminated JSON object")


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def _cjk_ratio(text: str) -> tuple[float, int]:
    """Return ``(cjk_share_of_letters, cjk_count)`` for ``text``.

    Only letters are counted, so digits, punctuation and whitespace cannot
    dilute the score — "38.6 个点" should not read as less Chinese than "个点".
    """
    cjk = 0
    latin = 0
    for char in text:
        if "一" <= char <= "鿿":
            cjk += 1
        elif char.isascii() and char.isalpha():
            latin += 1
    total = cjk + latin
    return (cjk / total if total else 0.0, cjk)


def _require_chinese(text: str, label: str, min_ratio: float) -> None:
    """Reject text the model wrote in English despite the instruction."""
    ratio, count = _cjk_ratio(text)
    if count < _MIN_CJK_CHARS or ratio < min_ratio:
        raise InvalidReading(
            f"{label} is not Chinese (CJK share {ratio:.2f}, {count} chars); "
            "the model ignored the language instruction"
        )


def _clean_text(value: Any, label: str) -> str:
    """Require a non-empty string, normalised of surrounding whitespace."""
    if not isinstance(value, str):
        raise InvalidReading(f"{label} must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise InvalidReading(f"{label} is empty")
    return cleaned


def _coerce_score(value: Any, label: str) -> float:
    """Coerce one score to a float in ``[0, 10]``, rounded to one decimal.

    Clamping an out-of-range number is safe — the model's *ordering* survives,
    which is the signal the user reads. Inventing a missing or non-numeric one
    is not, so those raise.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a score
        raise InvalidReading(f"score '{label}' must be a number, got a boolean")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise InvalidReading(f"score '{label}' is not numeric: {value!r}") from None
    if not isinstance(value, (int, float)):
        raise InvalidReading(f"score '{label}' must be a number, got {type(value).__name__}")

    number = float(value)
    if not math.isfinite(number):
        raise InvalidReading(f"score '{label}' is not a finite number")
    return round(min(10.0, max(0.0, number)), 1)


def _validate(payload: dict[str, Any], model: str) -> Reading:
    """Turn a parsed response into a :class:`Reading`, or raise.

    Deliberately strict and deliberately non-repairing: this is the only gate
    between a model's output and something the user will read as fact.
    """
    summary = _clean_text(payload.get("summary_zh"), "summary_zh")
    if len(summary) < _MIN_SUMMARY_CHARS:
        raise InvalidReading(
            f"summary_zh is too short to be a 2-4 sentence card ({len(summary)} chars)"
        )
    if len(summary) > _MAX_SUMMARY_CHARS:
        raise InvalidReading(
            f"summary_zh is too long to be a 2-4 sentence card ({len(summary)} chars); "
            "the model likely echoed the abstract"
        )
    _require_chinese(summary, "summary_zh", _MIN_CJK_RATIO_SUMMARY)

    raw_highlights = payload.get("highlights")
    if not isinstance(raw_highlights, dict):
        raise InvalidReading("highlights must be an object")
    highlights = {
        key: _clean_text(raw_highlights.get(key), f"highlights.{key}") for key in HIGHLIGHT_KEYS
    }
    # Checked jointly rather than per-field: a single highlight can be short
    # and term-heavy ("首个开源 VLA benchmark"), but all four in English is the
    # same instruction-following failure the summary gate catches.
    _require_chinese(" ".join(highlights.values()), "highlights", _MIN_CJK_RATIO_HIGHLIGHTS)

    raw_scores = payload.get("scores")
    if not isinstance(raw_scores, dict):
        raise InvalidReading("scores must be an object")
    missing = [key for key in SCORE_KEYS if key not in raw_scores]
    if missing:
        raise InvalidReading(f"scores is missing: {', '.join(missing)}")
    scores = {key: _coerce_score(raw_scores[key], key) for key in SCORE_KEYS}

    return Reading(summary_zh=summary, highlights=highlights, scores=scores, model=model)


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #


def read_paper(
    title: str,
    abstract: str,
    *,
    domain: str | None = None,
    authors: Sequence[str] = (),
    timeout: float | None = None,
) -> Reading:
    """Read one paper and return its validated card.

    Pure: no database access and no persistence, so re-reading a stored paper
    with a stronger model is just another call with the same arguments.

    Args:
        title: The paper's title, as fetched from arXiv.
        abstract: The English abstract. The model reads this and nothing else —
            we never hand it a guess to elaborate on.
        domain: A topical label from the *shared default* rubric
            (``DailyPaper.matched_domain``), passed purely as context so the
            model knows roughly what field it is reading in. It is explicitly
            **not** any particular reader's direction and no score depends on
            it — the prompt tells the model not to judge reader relevance at
            all. ``None`` simply leaves the hint out.
        authors: Author names, used to estimate ``popularity``.
        timeout: Per-request timeout; defaults to the provider's own.

    Raises:
        ReaderUnavailable: No chat provider is configured. The caller should
            leave the paper ``pending`` — not ``error``.
        ProviderError: The provider was unreachable, errored, or refused.
        InvalidReading: The response did not validate as a card.
    """
    provider = get_settings().provider_for("chat")
    if provider is None:
        raise ReaderUnavailable(
            "no chat provider is configured; set PHAROS_CHAT_PROVIDER and the "
            "matching API key to enable the daily reading layer"
        )

    clean_title = (title or "").strip()
    clean_abstract = (abstract or "").strip()
    if not clean_title or not clean_abstract:
        # Guard the caller rather than the model: asking for a card about an
        # empty abstract is asking to have one invented.
        raise InvalidReading("cannot read a paper without both a title and an abstract")
    if len(clean_abstract) > _MAX_ABSTRACT_CHARS:
        clean_abstract = clean_abstract[:_MAX_ABSTRACT_CHARS].rstrip() + " …"

    names = [name.strip() for name in authors if name and name.strip()][:_MAX_AUTHORS]
    # No ``.format`` on the system prompt any more: it used to interpolate the
    # global interest list, and now that the rubric is reader-independent there
    # is nothing per-run to substitute. Passing it through verbatim also means
    # its JSON braces need no doubling, so what is written above is exactly what
    # the model is shown.
    system = SYSTEM_PROMPT
    user = USER_PROMPT_TEMPLATE.format(
        title=clean_title,
        # Labelled as the *shared* rubric's guess, so neither the model nor a
        # human reading a prompt dump mistakes it for this reader's direction.
        domain_line=f"论文领域（共享粗分类，仅供参考）：{domain}\n" if domain else "",
        authors_line=f"作者：{', '.join(names)}\n" if names else "",
        abstract=clean_abstract,
    )

    content = _request_reading(
        provider,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout if timeout is not None else provider.timeout,
    )
    return _validate(_extract_json_object(content), provider.model)
