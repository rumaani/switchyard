"""Workflow runner.

State lives entirely in the database (runs / run_steps), so a run can pause for approval
and resume in a different request. Every step re-checks the starting user's *current*
permissions: if their access is removed mid-run, the next tool step fails closed.

Steps run as the user who started the run, using each endpoint's configured service
credentials. (Per-user delegated credentials are on the roadmap.)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from .. import audit
from ..config import get_settings
from ..db import session_scope
from ..mcp import service
from ..mcp.client import McpError
from ..models import Endpoint, Run, RunStep, User, utcnow
from ..security.rbac import build_principal, can_call_tool
from .definition import WorkflowDef, evaluate, render

log = logging.getLogger("switchyard.engine")

TERMINAL = {"succeeded", "failed", "rejected", "cancelled"}
_locks: dict[int, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()


def create_steps(run: Run, wf: WorkflowDef) -> None:
    for i, s in enumerate(wf.steps):
        run.steps.append(RunStep(position=i, step_id=s.id, step_type=s.type, title=s.title or s.id,
                                 endpoint=s.endpoint, tool=s.tool))


async def start(run_id: int) -> None:
    if get_settings().run_mode == "inline":
        await execute(run_id)
        return
    task = asyncio.create_task(execute(run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _context(run: Run) -> dict[str, Any]:
    return {
        "inputs": run.inputs or {},
        "steps": {s.step_id: {"output": s.output, "status": s.status, "error": s.error} for s in run.steps},
        "run": {"id": run.id, "started_by": run.started_by, "workflow": run.workflow_name},
    }


async def execute(run_id: int) -> None:
    lock = _locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        try:
            await _execute(run_id)
        except Exception as exc:  # never leave a run stuck in "running"
            log.exception("run %s crashed", run_id)
            with session_scope() as db:
                run = db.get(Run, run_id)
                if run and run.status not in TERMINAL:
                    run.status, run.error, run.finished_at = "failed", f"Internal error: {exc}", utcnow()
        finally:
            _locks.pop(run_id, None)


async def _execute(run_id: int) -> None:
    while True:
        # ---- pick the next step (short DB session) ----
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None or run.status in TERMINAL or run.status == "waiting_approval":
                return
            wf = WorkflowDef.model_validate(run.definition)
            step_row = next((s for s in run.steps if s.status in ("pending", "running")), None)
            if step_row is None:
                run.status, run.finished_at = "succeeded", utcnow()
                audit.record(db, run.started_by, "run.finish", f"run/{run.id}", detail={"status": "succeeded"})
                return
            run.status = "running"
            sdef = next(s for s in wf.steps if s.id == step_row.step_id)
            ctx = _context(run)
            step_row.started_at = utcnow()
            try:
                should_run = evaluate(sdef.when, ctx)
            except Exception as exc:  # noqa: BLE001
                _fail(db, run, step_row, f"Condition error: {exc}")
                return
            if not should_run:
                step_row.status, step_row.finished_at = "skipped", utcnow()
                continue

            if sdef.type == "approval":
                try:
                    msg = render(sdef.message or "Approve to continue?", ctx)
                except Exception as exc:  # noqa: BLE001
                    _fail(db, run, step_row, f"Template error: {exc}")
                    return
                step_row.status, step_row.request = "waiting_approval", {"message": str(msg)}
                run.status = "waiting_approval"
                audit.record(db, run.started_by, "run.awaiting_approval", f"run/{run.id}/{step_row.step_id}")
                return

            # tool step: resolve args + authorise as the run's starter
            try:
                args = render(sdef.args, ctx)
            except Exception as exc:  # noqa: BLE001
                _fail(db, run, step_row, f"Template error: {exc}")
                return
            step_row.status, step_row.request = "running", {"arguments": args}
            ep = db.execute(select(Endpoint).where(Endpoint.name == sdef.endpoint)).scalar_one_or_none()
            user = db.get(User, run.started_by_user_id) if run.started_by_user_id else None
            if ep is None:
                _fail(db, run, step_row, f"Endpoint '{sdef.endpoint}' is not registered")
                return
            if user is None or user.disabled or not can_call_tool(db, build_principal(db, user), ep, sdef.tool):
                audit.record(db, run.started_by, "tool.call", f"{ep.name}/{sdef.tool}", outcome="denied",
                             detail={"run": run.id, "step": sdef.id})
                _fail(db, run, step_row, f"{run.started_by} is not allowed to call '{sdef.tool}' on '{ep.name}'")
                return
            ref = service.EndpointRef(ep)
            step_id_pk = step_row.id

        # ---- call the tool outside any DB session, with retries ----
        attempts, result, error = 0, None, ""
        t0 = time.perf_counter()
        while True:
            attempts += 1
            try:
                result = await service.call_tool(ref, sdef.tool, args)
                error = _tool_error_text(result) if result["is_error"] else ""
            except McpError as exc:
                result, error = None, str(exc)
            if not error or attempts > sdef.retries:
                break
            await asyncio.sleep(sdef.retry_delay_s)
        ms = round((time.perf_counter() - t0) * 1000)

        # ---- record the outcome ----
        with session_scope() as db:
            run = db.get(Run, run_id)
            step_row = db.get(RunStep, step_id_pk)
            step_row.attempts, step_row.duration_ms, step_row.finished_at = attempts, ms, utcnow()
            if result is not None:
                step_row.output = result["value"]
            audit.record(db, run.started_by, "tool.call", f"{sdef.endpoint}/{sdef.tool}",
                         outcome="error" if error else "ok",
                         detail={"run": run.id, "step": sdef.id, "arguments": args, "attempts": attempts,
                                 "error": error or None})
            if error and not sdef.continue_on_error:
                _fail(db, run, step_row, error)
                return
            step_row.status, step_row.error = ("failed", error) if error else ("succeeded", "")
            if run.status == "cancelled":
                return


def _tool_error_text(result: dict) -> str:
    v = result.get("value")
    return v if isinstance(v, str) else f"Tool reported an error: {v}"


def _fail(db, run: Run, step_row: RunStep, error: str) -> None:
    step_row.status, step_row.error, step_row.finished_at = "failed", error[:4000], utcnow()
    for s in run.steps:
        if s.status == "pending":
            s.status = "skipped"
    run.status, run.error, run.finished_at = "failed", f"Step '{step_row.step_id}': {error}"[:4000], utcnow()
    audit.record(db, run.started_by, "run.finish", f"run/{run.id}", outcome="error",
                 detail={"status": "failed", "step": step_row.step_id, "error": error[:500]})


def recover_interrupted() -> int:
    """On startup: runs that were mid-step when the process died are marked failed (never re-run
    blindly, because the tool may already have had its side effect)."""
    n = 0
    with session_scope() as db:
        for run in db.execute(select(Run).where(Run.status.in_(["running", "queued"]))).scalars():
            for s in run.steps:
                if s.status == "running":
                    s.status, s.error = "failed", "Interrupted by a restart; check the target system before re-running"
                elif s.status == "pending":
                    s.status = "skipped"
            run.status, run.error, run.finished_at = "failed", "Interrupted by a restart", utcnow()
            n += 1
    return n
