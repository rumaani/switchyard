"""First-run data: built-in roles always; demo endpoints, mappings, grants and a workflow
when SEED_DEMO=true and the database has no endpoints yet."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Endpoint, EndpointGrant, GroupMapping, Role, Workflow
from .security.crypto import encrypt_json
from .security.rbac import ensure_builtin_roles

DEMO_GROUPS = {"sy-admins": "admin", "sy-builders": "builder", "sy-operators": "operator",
               "sy-viewers": "viewer", "sy-auditors": "auditor"}

MONTH_END = {
    "inputs": [
        {"name": "period", "label": "Period", "type": "string", "default": "Oct-2026", "required": True},
        {"name": "entity", "label": "Entity", "type": "string", "default": "US", "required": True},
        {"name": "threshold_pct", "label": "Variance threshold %", "type": "number", "default": 5,
         "required": True},
    ],
    "steps": [
        {"id": "pull", "type": "tool", "title": "Pull actuals from ERP", "endpoint": "erp",
         "tool": "get_actuals", "args": {"period": "{{ inputs.period }}", "entity": "{{ inputs.entity }}"},
         "retries": 2},
        {"id": "review", "type": "approval", "title": "Controller review",
         "message": "Load {{ steps.pull.output.row_count }} rows totalling "
                    "{{ '{:,.0f}'.format(steps.pull.output.total) }} for {{ inputs.entity }} "
                    "{{ inputs.period }} into Essbase?"},
        {"id": "load", "type": "tool", "title": "Load into Essbase", "endpoint": "essbase", "tool": "load_data",
         "args": {"cube": "Finance.Plan", "rows": "{{ steps.pull.output.rows }}"}},
        {"id": "calc", "type": "tool", "title": "Aggregate actuals", "endpoint": "essbase", "tool": "run_calc",
         "args": {"cube": "Finance.Plan", "script": "AggActuals"}},
        {"id": "variance", "type": "tool", "title": "Check variance vs forecast", "endpoint": "essbase",
         "tool": "get_variance",
         "args": {"period": "{{ inputs.period }}", "threshold_pct": "{{ inputs.threshold_pct }}"}},
        {"id": "ticket", "type": "tool", "title": "Raise ticket for breaches", "endpoint": "servicenow",
         "tool": "create_ticket", "when": "steps.variance.output.breaches | length > 0",
         "args": {"title": "Variance breaches – {{ inputs.entity }} {{ inputs.period }}",
                  "body": "{% for b in steps.variance.output.breaches %}{{ b.account }}: {{ b.variance_pct }}% "
                          "vs forecast\n{% endfor %}",
                  "priority": 2}},
    ],
}


def seed(db: Session) -> None:
    ensure_builtin_roles(db)
    s = get_settings()
    if not s.seed_demo or db.execute(select(func.count()).select_from(Endpoint)).scalar():
        return
    base = s.demo_mcp_url.rstrip("/")
    erp = Endpoint(name="erp", display_name="ERP (demo)", description="General ledger actuals – mock Oracle ERP",
                   url=f"{base}/erp/mcp", auth_type="none")
    ess = Endpoint(name="essbase", display_name="Essbase 26ai (demo)",
                   description="Planning cube – mock Essbase MCP (answers as an SSE stream)",
                   url=f"{base}/essbase/mcp", auth_type="bearer",
                   auth_config_enc=encrypt_json({"token": "demo-essbase-token"}))
    snow = Endpoint(name="servicenow", display_name="ServiceNow (demo)",
                    description="Ticketing – mock, protected by OAuth client credentials",
                    url=f"{base}/servicenow/mcp", auth_type="oauth_client_credentials",
                    auth_config_enc=encrypt_json({"token_url": f"{base}/oauth/token", "client_id": "switchyard",
                                                  "client_secret": "demo-secret", "scope": "tickets.write",
                                                  "auth_method": "client_secret_basic"}))
    db.add_all([erp, ess, snow])
    db.flush()
    roles = {r.name: r for r in db.execute(select(Role)).scalars()}
    for group, role in DEMO_GROUPS.items():
        db.add(GroupMapping(idp_group=group, role_id=roles[role].id))
    for ep in (erp, ess, snow):
        db.add(EndpointGrant(role_id=roles["builder"].id, endpoint_id=ep.id, tools=["*"]))
    db.add(EndpointGrant(role_id=roles["operator"].id, endpoint_id=erp.id, tools=["get_*"]))
    db.add(EndpointGrant(role_id=roles["operator"].id, endpoint_id=ess.id,
                         tools=["get_*", "load_data", "run_calc"]))
    db.add(EndpointGrant(role_id=roles["operator"].id, endpoint_id=snow.id, tools=["create_ticket"]))
    db.add(EndpointGrant(role_id=roles["viewer"].id, endpoint_id=ess.id, tools=["get_*"]))
    db.add(Workflow(name="Month-end actuals load",
                    description="Pull GL actuals, get controller sign-off, load and aggregate in Essbase, "
                                "then raise a ticket if any account breaches the variance threshold.",
                    definition=MONTH_END, allowed_roles=["operator", "builder"], created_by="seed",
                    updated_by="seed"))
    db.flush()
