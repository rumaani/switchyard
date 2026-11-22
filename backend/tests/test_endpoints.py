def _eps(c):
    return {e["name"]: e for e in c.get("/api/endpoints").json()}


def test_seeded_endpoints_discover_all_auth_modes(builder):
    eps = _eps(builder)
    assert set(eps) == {"erp", "essbase", "servicenow"}
    for name, ep in eps.items():
        r = builder.post(f"/api/endpoints/{ep['id']}/refresh").json()
        assert r["status"] == "ok", (name, r["status_detail"])
        assert r["tool_count"] > 0
    ess = builder.get(f"/api/endpoints/{eps['essbase']['id']}").json()
    tools = {t["name"]: t for t in ess["tools"]}
    assert tools["get_variance"]["readOnly"] and tools["clear_data"]["destructive"]


def test_secrets_are_masked_and_preserved(builder):
    ep = _eps(builder)["essbase"]
    assert ep["auth_config"]["token"] == "••••••"
    body = {"name": "essbase", "display_name": "Essbase renamed", "url": ep["url"], "auth_type": "bearer",
            "auth_config": {"token": "••••••"}}
    r = builder.put(f"/api/endpoints/{ep['id']}", json=body).json()
    assert r["status"] == "ok" and r["display_name"] == "Essbase renamed"  # stored token still works


def test_viewer_sees_no_urls_or_secrets(viewer):
    ep = _eps(viewer)["essbase"]
    assert ep["url"] == "" and ep["auth_config"] == {}


def test_tool_grants(operator, builder):
    ess = _eps(builder)["essbase"]
    builder.post(f"/api/endpoints/{ess['id']}/refresh")
    tools = {t["name"]: t["allowed"] for t in operator.get(f"/api/endpoints/{ess['id']}").json()["tools"]}
    assert tools["get_variance"] and tools["load_data"] and not tools["clear_data"]


def test_call_tool_and_denials(builder, operator):
    eps = _eps(builder)
    r = builder.post(f"/api/endpoints/{eps['essbase']['id']}/tools/get_variance/call",
                     json={"arguments": {"period": "Oct-2026", "threshold_pct": 7}}).json()
    assert r["ok"] and {b["account"] for b in r["value"]["breaches"]} == {"5000 COGS", "6300 Travel"}
    r = builder.post(f"/api/endpoints/{eps['servicenow']['id']}/tools/create_ticket/call",
                     json={"arguments": {"title": "hello"}}).json()
    assert r["ok"] and r["value"]["number"].startswith("INC")
    # operators lack tools.call (they run workflows instead)
    assert operator.post(f"/api/endpoints/{eps['erp']['id']}/tools/get_actuals/call",
                         json={"arguments": {}}).status_code == 403


def test_tool_error_is_reported(builder):
    erp = _eps(builder)["erp"]
    r = builder.post(f"/api/endpoints/{erp['id']}/tools/get_actuals/call", json={"arguments": {}}).json()
    assert r["ok"] is False and "period is required" in r["value"]


def test_connection_test_reports_bad_credentials(builder):
    r = builder.post("/api/endpoints/test", json={"name": "bad", "url": "http://mock/essbase/mcp",
                                                   "auth_type": "bearer", "auth_config": {"token": "nope"}}).json()
    assert r["ok"] is False and "401" in r["error"]
    r = builder.post("/api/endpoints/test", json={"name": "good", "url": "http://mock/erp/mcp"}).json()
    assert r["ok"] and r["server"]["name"] == "mock-erp"


def test_create_validate_delete(builder):
    assert builder.post("/api/endpoints", json={"name": "Bad Name!", "url": "http://x"}).status_code == 422
    assert builder.post("/api/endpoints", json={"name": "erp", "url": "http://mock/erp/mcp"}).status_code == 409
    r = builder.post("/api/endpoints", json={"name": "erp2", "url": "http://mock/erp/mcp"})
    assert r.status_code == 201 and r.json()["status"] == "ok"
    assert builder.delete(f"/api/endpoints/{r.json()['id']}").status_code == 200
