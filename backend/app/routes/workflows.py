"""Workflows (definitions) and runs (executions)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..models import Endpoint, Run, Workflow, utcnow
from ..security.rbac import Principal, require
from ..workflows import engine
from ..workflows.definition import WorkflowDef, coerce_inputs, validate
from .auth import client_ip

router = APIRouter(prefix="/api", tags=["workflows"])


# --- helpers -----------------------------------------------------------------------

def _registry(db: Session) -> dict[str, set[str]]:
    return {e.name: {t.get("name") for t in (e.tools_cache or [])} for e in db.execute(select(Endpoint)).scalars()}


def _can_run(p: Principal, wf: Workflow) -> bool:
    if not p.can("workflows.run"):
        return False
    return p.is_admin or not wf.allowed_roles or bool(set(wf.allowed_roles) & set(p.roles))


def wf_json(wf: Workflow, p: Principal, last_run: Run | None = None) -> dict:
    return {"id": wf.id, "name": wf.name, "description": wf.description, "definition": wf.definition,
            "allowed_roles": wf.allowed_roles or [], "version": wf.version, "updated_at": wf.updated_at,
            "updated_by": wf.updated_by, "can_run": _can_run(p, wf), "can_edit": p.can("workflows.manage"),
            "step_count": len((wf.definition or {}).get("steps", [])),
            "last_run": {"id": last_run.id, "status": last_run.status, "created_at": last_run.created_at}
            if last_run else None}


def run_json(run: Run, p: Principal, full: bool = False) -> dict:
    out = {"id": run.id, "workflow_id": run.workflow_id, "workflow_name": run.workflow_name,
           "workflow_version": run.workflow_version, "status": run.status, "started_by": run.started_by,
           "created_at": run.created_at, "finished_at": run.finished_at, "error": run.error,
           "inputs": run.inputs,
           "progress": {"done": sum(1 for s in run.steps if s.status in ("succeeded", "skipped")),
                        "total": len(run.steps)}}
    if full:
        out["steps"] = [{
            "step_id": s.step_id, "type": s.step_type, "title": s.title, "status": s.status, "endpoint": s.endpoint,
            "tool": s.tool, "request": s.request, "output": s.output, "error": s.error, "attempts": s.attempts,
            "decided_by": s.decided_by, "decision_note": s.decision_note, "started_at": s.started_at,
            "finished_at": s.finished_at, "duration_ms": s.duration_ms,
            "can_decide": s.status == "waiting_approval" and _can_decide(p, run, s.step_id),
        } for s in run.steps]
    return out


def _can_decide(p: Principal, run: Run, step_id: str) -> bool:
    if not p.can("runs.approve"):
        return False
    sdef = next((s for s in WorkflowDef.model_validate(run.definition).steps if s.id == step_id), None)
    if sdef and not sdef.allow_self_approval and run.started_by_user_id == p.user_id:
        return False  # segregation of duties
    return True


def _get_wf(db: Session, wf_id: int) -> Workflow:
    wf = db.get(Workflow, wf_id)
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


# --- workflows ---------------------------------------------------------------------

class WorkflowIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str = ""
    definition: dict[str, Any] = Field(default_factory=lambda: {"inputs": [], "steps": []})
    allowed_roles: list[str] = Field(default_factory=list)


@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db), p: Principal = Depends(require("workflows.view"))):
    out = []
    for wf in db.execute(select(Workflow).order_by(Workflow.name)).scalars():
        last = db.execute(select(Run).where(Run.workflow_id == wf.id).order_by(Run.id.desc()).limit(1)).scalar()
        out.append(wf_json(wf, p, last))
    return out


@router.get("/workflows/{wf_id}")
def get_workflow(wf_id: int, db: Session = Depends(get_db), p: Principal = Depends(require("workflows.view"))):
    return wf_json(_get_wf(db, wf_id), p)


@router.post("/workflows/validate")
def validate_workflow(body: WorkflowIn, db: Session = Depends(get_db),
                      _: Principal = Depends(require("workflows.view"))):
    return validate(body.definition, _registry(db))


def _check(db: Session, body: WorkflowIn) -> None:
    result = validate(body.definition, _registry(db))
    if result["errors"]:
        raise HTTPException(422, {"message": "Workflow has errors", **result})


@router.post("/workflows", status_code=201)
def create_workflow(body: WorkflowIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require("workflows.manage"))):
    if db.execute(select(Workflow).where(Workflow.name == body.name)).scalar_one_or_none():
        raise HTTPException(409, "A workflow with that name already exists")
    _check(db, body)
    wf = Workflow(name=body.name, description=body.description, definition=body.definition,
                  allowed_roles=body.allowed_roles, created_by=p.email, updated_by=p.email)
    db.add(wf)
    db.flush()
    audit.record(db, p.email, "workflow.create", wf.name, ip=client_ip(request))
    return wf_json(wf, p)


@router.put("/workflows/{wf_id}")
def update_workflow(wf_id: int, body: WorkflowIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require("workflows.manage"))):
    wf = _get_wf(db, wf_id)
    clash = db.execute(select(Workflow).where(Workflow.name == body.name, Workflow.id != wf.id)).scalar_one_or_none()
    if clash:
        raise HTTPException(409, "A workflow with that name already exists")
    _check(db, body)
    before = {"definition": wf.definition, "allowed_roles": wf.allowed_roles}
    wf.name, wf.description, wf.definition = body.name, body.description, body.definition
    wf.allowed_roles, wf.updated_by, wf.version = body.allowed_roles, p.email, wf.version + 1
    audit.record(db, p.email, "workflow.update", wf.name, detail={"version": wf.version, "before": before},
                 ip=client_ip(request))
    return wf_json(wf, p)


@router.delete("/workflows/{wf_id}")
def delete_workflow(wf_id: int, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require("workflows.manage"))):
    wf = _get_wf(db, wf_id)
    audit.record(db, p.email, "workflow.delete", wf.name, ip=client_ip(request))
    db.delete(wf)
    return {"ok": True}


class RunIn(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


@router.post("/workflows/{wf_id}/runs", status_code=201)
async def start_run(wf_id: int, body: RunIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require("workflows.run"))):
    wf = _get_wf(db, wf_id)
    if not _can_run(p, wf):
        raise HTTPException(403, "Your roles are not allowed to run this workflow")
    defn = WorkflowDef.model_validate(wf.definition)
    try:
        inputs = coerce_inputs(defn, body.inputs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    run = Run(workflow_id=wf.id, workflow_name=wf.name, workflow_version=wf.version, definition=wf.definition,
              inputs=inputs, status="queued", started_by=p.email, started_by_user_id=p.user_id)
    engine.create_steps(run, defn)
    db.add(run)
    db.flush()
    audit.record(db, p.email, "run.start", f"run/{run.id}", detail={"workflow": wf.name, "inputs": inputs},
                 ip=client_ip(request))
    db.commit()
    await engine.start(run.id)
    db.refresh(run)
    return run_json(run, p, full=True)


# --- runs --------------------------------------------------------------------------

@router.get("/runs")
def list_runs(db: Session = Depends(get_db), p: Principal = Depends(require("workflows.view")),
              workflow_id: int | None = None, status: str | None = None, limit: int = Query(50, le=200)):
    q = select(Run).order_by(Run.id.desc()).limit(limit)
    if not p.can("runs.view_all"):
        q = q.where(Run.started_by_user_id == p.user_id)
    if workflow_id:
        q = q.where(Run.workflow_id == workflow_id)
    if status:
        q = q.where(Run.status == status)
    return [run_json(r, p) for r in db.execute(q).scalars()]


@router.get("/runs/summary")
def runs_summary(db: Session = Depends(get_db), p: Principal = Depends(require("workflows.view"))):
    q = select(Run.status, func.count()).group_by(Run.status)
    if not p.can("runs.view_all"):
        q = q.where(Run.started_by_user_id == p.user_id)
    return dict(db.execute(q).all())


def _get_run(db: Session, run_id: int, p: Principal) -> Run:
    run = db.get(Run, run_id)
    if not run or (not p.can("runs.view_all") and run.started_by_user_id != p.user_id):
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), p: Principal = Depends(require("workflows.view"))):
    return run_json(_get_run(db, run_id, p), p, full=True)


class Decision(BaseModel):
    approve: bool
    note: str = ""


@router.post("/runs/{run_id}/steps/{step_id}/decision")
async def decide(run_id: int, step_id: str, body: Decision, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require("runs.approve"))):
    run = _get_run(db, run_id, p)
    step = next((s for s in run.steps if s.step_id == step_id), None)
    if not step or step.status != "waiting_approval":
        raise HTTPException(409, "That step is not waiting for approval")
    if not _can_decide(p, run, step_id):
        raise HTTPException(403, "You cannot approve a run you started (segregation of duties)")
    step.decided_by, step.decision_note, step.finished_at = p.email, body.note, utcnow()
    step.output = {"approved": body.approve, "by": p.email, "note": body.note}
    if body.approve:
        step.status, run.status = "succeeded", "running"
    else:
        step.status = "rejected"
        for s in run.steps:
            if s.status == "pending":
                s.status = "skipped"
        run.status, run.finished_at = "rejected", utcnow()
        run.error = f"Rejected by {p.email}" + (f": {body.note}" if body.note else "")
    audit.record(db, p.email, "run.approve" if body.approve else "run.reject", f"run/{run.id}/{step_id}",
                 detail={"note": body.note}, ip=client_ip(request))
    db.commit()
    if body.approve:
        await engine.start(run.id)
    db.refresh(run)
    return run_json(run, p, full=True)


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, request: Request, db: Session = Depends(get_db),
           p: Principal = Depends(require("workflows.run"))):
    run = _get_run(db, run_id, p)
    if run.status in engine.TERMINAL:
        raise HTTPException(409, f"Run is already {run.status}")
    if run.started_by_user_id != p.user_id and not p.can("runs.approve"):
        raise HTTPException(403, "Only the starter or an approver can cancel a run")
    run.status, run.finished_at, run.error = "cancelled", utcnow(), f"Cancelled by {p.email}"
    for s in run.steps:
        if s.status in ("pending", "waiting_approval"):
            s.status = "skipped"
    audit.record(db, p.email, "run.cancel", f"run/{run.id}", ip=client_ip(request))
    return run_json(run, p, full=True)
