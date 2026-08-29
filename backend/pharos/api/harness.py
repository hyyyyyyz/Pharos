"""Owner-scoped Harness HTTP API.

Every route authenticates through the existing bearer dependency; every ID
that does not belong to the caller is a 404, exactly like the rest of the
API. The operator-only config surface is admin-gated and deliberately small:
status, validate, apply (full snapshot + expected head CAS).

SSE is fetch-based and replays from the durable cursor: the streaming
generator authenticates in a short session, then polls the database tail.
The database is the truth; the stream only shortens latency.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from pharos.api.deps import current_user, get_session, require_admin
from pharos.db.models import User
from pharos.harness.app import HarnessApp
from pharos.harness.configrev import HarnessConfigSnapshot, WorkflowRoute, validate_snapshot
from pharos.harness.contracts import (
    ApprovalState,
    HarnessError,
    IdempotencyConflictError,
    NotFoundError,
    StateError,
    UnavailableError,
)
from pharos.harness.repository import Scope, now_iso

router = APIRouter(prefix="/api/harness", tags=["harness"])

MAX_LIST_LIMIT = 200
SSE_POLL_SECONDS = 0.5
SSE_MAX_REPLAY = 200


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class RunCreateIn(CamelModel):
    workflow_key: str = Field(max_length=64)
    input: dict[str, Any]
    idempotency_key: Annotated[str, Field(max_length=256)] | None = None
    project_id: str | None = None


class ApprovalDecisionIn(CamelModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)


class OperatorConfigIn(CamelModel):
    snapshot: dict[str, Any]
    expected_head_revision: str | None = None
    actor: str = Field(default="operator", max_length=128)
    reason: str = Field(default="", max_length=2000)


def _harness(request: Request) -> HarnessApp:
    app = request.app.state.harness
    if app is None:
        raise HTTPException(status_code=503, detail="harness unavailable")
    return app


def _scope(user: User) -> Scope:
    return Scope.user(user.id)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, (NotFoundError,)):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, IdempotencyConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, UnavailableError):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, (StateError, ValueError)):
        return HTTPException(status_code=400, detail=str(error))
    if isinstance(error, HarnessError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=500, detail="harness error")


def _run_out(run: dict) -> dict:
    return {
        "id": run["id"],
        "workflowKey": run["workflow_key"],
        "workflowVersion": run["workflow_version"],
        "state": run["state"],
        "outcome": run["outcome"],
        "initiator": run["initiator"],
        "projectId": run["project_id"],
        "createdAtUs": run["created_at"],
        "startedAtUs": run["started_at"],
        "finishedAtUs": run["finished_at"],
        "errorCode": run["error_code"],
        "errorMessage": run["error_message"],
        "usage": json.loads(run["usage_json"] or "{}"),
    }


def _step_out(step: dict) -> dict:
    return {
        "id": step["id"],
        "definitionStepKey": step["definition_step_key"],
        "instanceKey": step["instance_key"],
        "stepKind": step["step_kind"],
        "state": step["state"],
        "attemptCount": step["attempt_count"],
        "errorCode": step["error_code"],
        "errorMessage": step["error_message"],
        "skipReason": step["skip_reason"],
        "waitingReason": step["waiting_reason"],
    }


def _approval_out(approval: dict) -> dict:
    return {
        "id": approval["id"],
        "runId": approval["run_id"],
        "action": approval["action"],
        "resource": json.loads(approval["resource_json"]),
        "effectSummary": json.loads(approval["effect_summary_json"]),
        "state": approval["state"],
        "expiresAtUs": approval["expires_at"],
        "resolvedAtUs": approval["resolved_at"],
        "resolverReason": approval["resolver_reason"],
    }


def _artifact_out(artifact: dict) -> dict:
    content = json.loads(artifact["content_json"]) if artifact["content_json"] else None
    if artifact["deleted_at"] is not None:
        content = None
    return {
        "id": artifact["id"],
        "artifactType": artifact["artifact_type"],
        "schemaName": artifact["schema_name"],
        "schemaVersion": artifact["schema_version"],
        "sensitivity": artifact["sensitivity"],
        "producerKind": artifact["producer_kind"],
        "qualityStatus": artifact["quality_status"],
        "evidenceLevel": artifact["evidence_level"],
        "contentSha256": artifact["content_sha256"],
        "content": content,
        "deleted": artifact["deleted_at"] is not None,
        "deletionReason": artifact["deletion_reason"],
    }


@router.get("/workflows")
def list_workflows(
    user: Annotated[User, Depends(current_user)],
    request: Request,
) -> list[dict]:
    harness = _harness(request)
    snapshot = harness.current_snapshot()
    routes = {route.workflow_key: route for route in snapshot.routes}
    out = []
    for workflow in harness.registry.all_workflows():
        route = routes.get(workflow.workflow_key)
        out.append(
            {
                "workflowKey": workflow.workflow_key,
                "version": workflow.version,
                "inputSchema": workflow.input_schema,
                "outputSchema": workflow.output_schema,
                "activationState": route.activation_state.value if route else "disabled",
                "executionMode": (
                    route.execution_mode.value if route and route.execution_mode else None
                ),
            }
        )
    return out


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: RunCreateIn,
    user: Annotated[User, Depends(current_user)],
    request: Request,
) -> dict:
    harness = _harness(request)
    key = (payload.idempotency_key or "").strip() or uuid.uuid4().hex
    try:
        run = harness.create_run(
            scope=_scope(user),
            workflow_key=payload.workflow_key,
            input=payload.input,
            idempotency_key=key,
            initiator="user",
            project_id=payload.project_id,
        )
    except Exception as error:
        raise _http_error(error) from error
    return _run_out(run)


@router.get("/runs")
def list_runs(
    user: Annotated[User, Depends(current_user)],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = 50,
    after: Annotated[int | None, Query()] = None,
) -> dict:
    harness = _harness(request)
    try:
        runs = harness.list_runs(scope=_scope(user), limit=limit, after=after)
    except Exception as error:
        raise _http_error(error) from error
    next_after = runs[-1]["created_at"] if len(runs) == limit else None
    return {"runs": [_run_out(run) for run in runs], "nextCursor": next_after}


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    user: Annotated[User, Depends(current_user)],
    request: Request,
) -> dict:
    harness = _harness(request)
    try:
        run = harness.get_run(scope=_scope(user), run_id=run_id)
        steps = harness.steps_for(scope=_scope(user), run_id=run_id)
    except Exception as error:
        raise _http_error(error) from error
    out = _run_out(run)
    out["steps"] = [_step_out(step) for step in steps]
    return out


@router.post("/runs/{run_id}/pause")
def pause_run(run_id: str, user: Annotated[User, Depends(current_user)], request: Request) -> dict:
    harness = _harness(request)
    try:
        return _run_out(harness.pause(scope=_scope(user), run_id=run_id))
    except Exception as error:
        raise _http_error(error) from error


@router.post("/runs/{run_id}/resume")
def resume_run(
    run_id: str,
    user: Annotated[User, Depends(current_user)],
    request: Request,
) -> dict:
    harness = _harness(request)
    try:
        return _run_out(harness.resume(scope=_scope(user), run_id=run_id))
    except Exception as error:
        raise _http_error(error) from error


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: Annotated[User, Depends(current_user)], request: Request) -> dict:
    harness = _harness(request)
    try:
        return _run_out(harness.cancel(scope=_scope(user), run_id=run_id))
    except Exception as error:
        raise _http_error(error) from error


@router.get("/runs/{run_id}/events")
def list_events(
    run_id: str,
    user: Annotated[User, Depends(current_user)],
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = 100,
) -> dict:
    harness = _harness(request)
    try:
        events = harness.replay_events(
            scope=_scope(user), run_id=run_id, after_seq=after_seq, limit=limit
        )
    except Exception as error:
        raise _http_error(error) from error
    return {
        "events": [event.public() for event in events],
        "nextSeq": events[-1].seq if events else after_seq,
    }


async def _event_stream(harness: HarnessApp, scope: Scope, run_id: str, after_seq: int, request):
    """The DB-tail generator behind the SSE endpoint; separated for tests."""
    cursor = after_seq
    try:
        while True:
            if await request.is_disconnected():
                break
            events = await asyncio.to_thread(
                harness.replay_events,
                scope=scope,
                run_id=run_id,
                after_seq=cursor,
                limit=SSE_MAX_REPLAY,
            )
            for event in events:
                yield f"data: {json.dumps(event.public(), ensure_ascii=False)}\n\n"
                cursor = max(cursor, event.seq)
            await asyncio.sleep(SSE_POLL_SECONDS)
    except asyncio.CancelledError:
        return


@router.get("/runs/{run_id}/events/stream")
async def stream_events(
    run_id: str,
    user: Annotated[User, Depends(current_user)],
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    harness = _harness(request)
    # Authenticate and verify ownership in a short session BEFORE streaming;
    # the generator below must not hold any request-scoped session.
    harness.replay_events(scope=_scope(user), run_id=run_id, after_seq=0, limit=1)

    return StreamingResponse(
        _event_stream(harness, _scope(user), run_id, after_seq, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/artifacts")
def list_artifacts(
    run_id: str, user: Annotated[User, Depends(current_user)], request: Request
) -> list[dict]:
    harness = _harness(request)
    try:
        artifacts = harness.artifacts_for(scope=_scope(user), run_id=run_id)
    except Exception as error:
        raise _http_error(error) from error
    return [_artifact_out(artifact) for artifact in artifacts]


@router.get("/runs/{run_id}/approvals")
def list_approvals(
    run_id: str, user: Annotated[User, Depends(current_user)], request: Request
) -> list[dict]:
    harness = _harness(request)
    try:
        approvals = harness.pending_approvals(scope=_scope(user), run_id=run_id)
    except Exception as error:
        raise _http_error(error) from error
    return [_approval_out(approval) for approval in approvals]


@router.post("/approvals/{approval_id}/decision")
def decide_approval(
    approval_id: str,
    payload: ApprovalDecisionIn,
    user: Annotated[User, Depends(current_user)],
    request: Request,
) -> dict:
    harness = _harness(request)
    try:
        approval = harness.decide_approval(
            scope=_scope(user),
            approval_id=approval_id,
            decision=ApprovalState(payload.decision),
            resolver_user_id=user.id,
            reason=payload.reason,
        )
    except Exception as error:
        raise _http_error(error) from error
    return _approval_out(approval)


# ------------------------------------------------------------- operator-only


@router.get("/operator/status")
def operator_status(
    admin: Annotated[User, Depends(require_admin)],
    request: Request,
) -> dict:
    harness = _harness(request)
    return harness.gate_status()


@router.post("/operator/config/validate")
def operator_validate(
    payload: OperatorConfigIn,
    admin: Annotated[User, Depends(require_admin)],
    request: Request,
) -> dict:
    harness = _harness(request)
    snapshot = HarnessConfigSnapshot(
        gates=payload.snapshot.get("gates", {}),
        routes=tuple(
            WorkflowRoute.model_validate(route) for route in payload.snapshot.get("routes", [])
        ),
        actor=payload.actor,
        reason=payload.reason,
    )
    errors = validate_snapshot(snapshot, harness.registry)
    return {"valid": not errors, "errors": errors, "hash": snapshot.snapshot_hash()}


@router.post("/operator/config/apply")
def operator_apply(
    payload: OperatorConfigIn,
    admin: Annotated[User, Depends(require_admin)],
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    harness = _harness(request)
    snapshot = HarnessConfigSnapshot(
        gates=payload.snapshot.get("gates", {}),
        routes=tuple(
            WorkflowRoute.model_validate(route) for route in payload.snapshot.get("routes", [])
        ),
        actor=payload.actor,
        reason=payload.reason,
    )
    try:
        revision_id = harness.config_service.apply(
            session,
            snapshot=snapshot,
            expected_head_revision=payload.expected_head_revision,
            actor=payload.actor,
            reason=payload.reason,
            now=now_iso(),
        )
        session.commit()
    except Exception as error:
        session.rollback()
        raise _http_error(error) from error
    return {"revisionId": revision_id, "hash": snapshot.snapshot_hash()}
