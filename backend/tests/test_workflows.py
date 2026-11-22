import pytest

from app.workflows.definition import evaluate, render, validate


def _month_end(c):
    return next(w for w in c.get("/api/workflows").json() if w["name"] == "Month-end actuals load")


@pytest.fixture(autouse=True)
def _discover(builder):
    for ep in builder.get("/api/endpoints").json():
        builder.post(f"/api/endpoints/{ep['id']}/refresh")


def test_render_keeps_native_types():
    ctx = {"inputs": {"n": 3}, "steps": {"a": {"output": {"rows": [1, 2]}}}}
    assert render("{{ steps.a.output.rows }}", ctx) == [1, 2]
    assert render({"x": "n={{ inputs.n }}", "y": ["{{ inputs.n + 1 }}"]}, ctx) == {"x": "n=3", "y": [4]}
    assert evaluate("steps.a.output.rows | length > 1", ctx) is True
    assert evaluate("", ctx) is True


def test_sandbox_blocks_unsafe_access():
    with pytest.raises(Exception):
        render("{{ ''.__class__.__mro__ }}", {})


def test_validate_catches_problems():
    reg = {"erp": {"get_actuals"}}
    v = validate({"steps": [{"id": "a", "endpoint": "nope", "tool": "x"},
                            {"id": "b", "endpoint": "erp", "tool": "get_actuals",
                             "args": {"p": "{{ steps.c.output }}"}},
                            {"id": "c", "endpoint": "erp", "tool": "other"}]}, reg)
    assert any("not registered" in e for e in v["errors"])
    assert any("does not run before it" in e for e in v["errors"])
    assert any("not in erp" in w for w in v["warnings"])
    assert validate({"steps": [{"id": "Bad Id", "endpoint": "erp", "tool": "x"}]}, reg)["errors"]


def test_month_end_with_approval_and_segregation_of_duties(operator, operator2):
    wf = _month_end(operator)
    run = operator.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {"period": "Oct-2026"}}).json()
    assert run["status"] == "waiting_approval", run
    steps = {s["step_id"]: s for s in run["steps"]}
    assert steps["pull"]["status"] == "succeeded" and steps["pull"]["output"]["row_count"] == 6
    assert "Load 6 rows totalling 305,750" in steps["review"]["request"]["message"]
    assert steps["review"]["can_decide"] is False  # starter may not approve own run

    r = operator.post(f"/api/runs/{run['id']}/steps/review/decision", json={"approve": True})
    assert r.status_code == 403

    run2 = operator2.get(f"/api/runs/{run['id']}").json()
    assert next(s for s in run2["steps"] if s["step_id"] == "review")["can_decide"] is True
    done = operator2.post(f"/api/runs/{run['id']}/steps/review/decision",
                          json={"approve": True, "note": "tied out to TB"}).json()
    assert done["status"] == "succeeded", done
    steps = {s["step_id"]: s for s in done["steps"]}
    assert steps["load"]["output"]["loaded"] == 6
    assert steps["ticket"]["status"] == "succeeded"
    assert "5000 COGS: 7.4%" in steps["ticket"]["request"]["arguments"]["body"]


def test_condition_skips_ticket_and_reject_flow(operator, operator2):
    wf = _month_end(operator)
    run = operator.post(f"/api/workflows/{wf['id']}/runs",
                        json={"inputs": {"period": "Nov-2026", "threshold_pct": 50}}).json()
    done = operator2.post(f"/api/runs/{run['id']}/steps/review/decision", json={"approve": True}).json()
    assert {s["step_id"]: s["status"] for s in done["steps"]}["ticket"] == "skipped"
    assert done["status"] == "succeeded"

    run = operator.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {"period": "Dec-2026"}}).json()
    rej = operator2.post(f"/api/runs/{run['id']}/steps/review/decision",
                         json={"approve": False, "note": "numbers off"}).json()
    assert rej["status"] == "rejected" and "numbers off" in rej["error"]
    assert all(s["status"] == "skipped" for s in rej["steps"][2:])


def test_run_permissions(viewer, operator, admin):
    wf = _month_end(viewer)
    assert wf["can_run"] is False
    assert viewer.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {}}).status_code == 403
    r = operator.post(f"/api/workflows/{wf['id']}/runs", json={"inputs": {"period": ""}})
    assert r.status_code == 422  # required input


def test_tool_denied_mid_workflow_fails_closed(builder, operator):
    """A step calling a tool outside the operator's grants fails the run (no call made)."""
    erp = next(e for e in builder.get("/api/endpoints").json() if e["name"] == "erp")
    body = {"name": "Post journal", "definition": {"steps": [
        {"id": "post", "endpoint": "erp", "tool": "post_journal", "args": {"lines": []}}]},
        "allowed_roles": ["operator"]}
    wf = builder.post("/api/workflows", json=body).json()
    run = operator.post(f"/api/workflows/{wf['id']}/runs", json={}).json()
    assert run["status"] == "failed" and "not allowed" in run["error"]
    assert erp  # sanity


def test_workflow_crud_and_versioning(builder):
    bad = builder.post("/api/workflows", json={"name": "Broken", "definition": {
        "steps": [{"id": "x", "endpoint": "ghost", "tool": "t"}]}})
    assert bad.status_code == 422
    wf = builder.post("/api/workflows", json={"name": "Entities", "definition": {
        "steps": [{"id": "list", "endpoint": "erp", "tool": "get_entities"}]}}).json()
    assert wf["version"] == 1
    wf2 = builder.put(f"/api/workflows/{wf['id']}", json={"name": "Entities", "description": "v2",
                                                          "definition": wf["definition"]}).json()
    assert wf2["version"] == 2
    run = builder.post(f"/api/workflows/{wf['id']}/runs", json={}).json()
    assert run["status"] == "succeeded" and run["steps"][0]["output"]["entities"] == ["US", "UK", "IN"]
    assert builder.delete(f"/api/workflows/{wf['id']}").status_code == 200
    assert builder.get(f"/api/runs/{run['id']}").json()["workflow_name"] == "Entities"  # history survives
