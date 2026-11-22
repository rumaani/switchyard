import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ago, api, type Endpoint } from "../api";
import { useAuth } from "../auth";
import { Badge, Drawer, Empty, Field, PageHeader, Spinner, Status, useToast } from "../components/ui";

export default function Endpoints() {
  const { can } = useAuth();
  const [eps, setEps] = useState<Endpoint[] | null>(null);
  const [adding, setAdding] = useState(false);
  const nav = useNavigate();
  const load = () => api.get<Endpoint[]>("/api/endpoints").then(setEps);
  useEffect(() => { load(); }, []);

  return (
    <>
      <PageHeader
        title="Endpoints"
        subtitle="Vendor MCP servers this deployment can reach. Give each a short name; workflows refer to it by that name."
        actions={can("endpoints.manage") && <button className="btn btn-primary" onClick={() => setAdding(true)}>+ Add endpoint</button>}
      />
      {!eps ? <Spinner /> : eps.length === 0 ? (
        <Empty title="No endpoints yet">Add your first vendor MCP endpoint to get started.</Empty>
      ) : (
        <div className="card-grid">
          {eps.map((e) => (
            <Link key={e.id} to={`/endpoints/${e.id}`} className="card card-link">
              <div className="row between">
                <code className="ep-name">{e.name}</code>
                <Status value={e.enabled ? e.status : "cancelled"} />
              </div>
              <h3>{e.display_name}</h3>
              <p className="muted small clamp">{e.description || "—"}</p>
              <div className="row gap-s small muted wrap">
                <Badge>{e.allowed_tool_count}/{e.tool_count} tools for you</Badge>
                <Badge tone="info">{authLabel(e.auth_type)}</Badge>
                {!e.enabled && <Badge tone="warn">disabled</Badge>}
                <span>checked {ago(e.last_checked_at)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
      {adding && <EndpointForm onClose={() => setAdding(false)} onSaved={(e) => { setAdding(false); nav(`/endpoints/${e.id}`); }} />}
    </>
  );
}

export const AUTH_LABELS: Record<string, string> = {
  none: "No auth",
  bearer: "Bearer token",
  api_key: "API key header",
  oauth_client_credentials: "OAuth client credentials",
};
export const authLabel = (t: string) => AUTH_LABELS[t] ?? t;

export function EndpointForm({ initial, onClose, onSaved }: {
  initial?: Endpoint; onClose: () => void; onSaved: (e: Endpoint) => void;
}) {
  const toast = useToast();
  const [f, setF] = useState({
    name: initial?.name ?? "",
    display_name: initial?.display_name ?? "",
    description: initial?.description ?? "",
    url: initial?.url ?? "",
    auth_type: initial?.auth_type ?? "none",
    auth_config: { ...(initial?.auth_config ?? {}) } as Record<string, string>,
    extra_headers: JSON.stringify(initial?.extra_headers ?? {}, null, 0) === "{}" ? "" : JSON.stringify(initial?.extra_headers),
    enabled: initial?.enabled ?? true,
  });
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<any>(null);
  const set = (k: string, v: any) => setF((x) => ({ ...x, [k]: v }));
  const setCfg = (k: string, v: string) => setF((x) => ({ ...x, auth_config: { ...x.auth_config, [k]: v } }));

  const body = () => {
    let headers = {};
    if (f.extra_headers.trim()) {
      try { headers = JSON.parse(f.extra_headers); } catch { throw new Error("Extra headers must be a JSON object"); }
    }
    return { ...f, extra_headers: headers };
  };

  const runTest = async () => {
    setBusy(true); setTest(null);
    try { setTest(await api.post("/api/endpoints/test", body())); } catch (e: any) { toast("error", e.message); }
    setBusy(false);
  };
  const save = async () => {
    setBusy(true);
    try {
      const saved = initial
        ? await api.put<Endpoint>(`/api/endpoints/${initial.id}`, body())
        : await api.post<Endpoint>("/api/endpoints", body());
      toast(saved.status === "ok" ? "ok" : "error",
        saved.status === "ok" ? `Saved · ${saved.tool_count} tools discovered` : `Saved, but connection failed: ${saved.status_detail}`);
      onSaved(saved);
    } catch (e: any) { toast("error", e.message); }
    setBusy(false);
  };

  const secret = (k: string, label: string) => (
    <Field label={label} hint={initial ? "Leave as ●●● to keep the stored value" : undefined}>
      <input type="password" autoComplete="new-password" value={f.auth_config[k] ?? ""} onChange={(e) => setCfg(k, e.target.value)} />
    </Field>
  );

  return (
    <Drawer title={initial ? `Edit ${initial.name}` : "Add endpoint"} onClose={onClose}
      footer={<>
        <button className="btn" disabled={busy || !f.url} onClick={runTest}>Test connection</button>
        <span className="grow" />
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy || !f.name || !f.url} onClick={save}>{busy ? "Working…" : "Save"}</button>
      </>}>
      <div className="stack">
        <div className="grid-2 tight">
          <Field label="Short name" hint="Used in workflows, e.g. essbase">
            <input value={f.name} onChange={(e) => set("name", e.target.value.toLowerCase())} placeholder="essbase" />
          </Field>
          <Field label="Display name">
            <input value={f.display_name} onChange={(e) => set("display_name", e.target.value)} placeholder="Essbase 26ai – Prod" />
          </Field>
        </div>
        <Field label="MCP URL" hint="Streamable HTTP endpoint">
          <input value={f.url} onChange={(e) => set("url", e.target.value)} placeholder="https://vendor.example.com/mcp" />
        </Field>
        <Field label="Description">
          <input value={f.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
        <Field label="Authentication">
          <select value={f.auth_type} onChange={(e) => set("auth_type", e.target.value)}>
            {Object.entries(AUTH_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        {f.auth_type === "bearer" && secret("token", "Token")}
        {f.auth_type === "api_key" && (
          <div className="grid-2 tight">
            <Field label="Header name"><input value={f.auth_config.header_name ?? "X-API-Key"} onChange={(e) => setCfg("header_name", e.target.value)} /></Field>
            {secret("api_key", "Key")}
          </div>
        )}
        {f.auth_type === "oauth_client_credentials" && (
          <>
            <Field label="Token URL"><input value={f.auth_config.token_url ?? ""} onChange={(e) => setCfg("token_url", e.target.value)} placeholder="https://idp.example.com/oauth2/v1/token" /></Field>
            <div className="grid-2 tight">
              <Field label="Client ID"><input value={f.auth_config.client_id ?? ""} onChange={(e) => setCfg("client_id", e.target.value)} /></Field>
              {secret("client_secret", "Client secret")}
            </div>
            <div className="grid-2 tight">
              <Field label="Scope"><input value={f.auth_config.scope ?? ""} onChange={(e) => setCfg("scope", e.target.value)} /></Field>
              <Field label="Client auth">
                <select value={f.auth_config.auth_method ?? "client_secret_basic"} onChange={(e) => setCfg("auth_method", e.target.value)}>
                  <option value="client_secret_basic">HTTP Basic</option>
                  <option value="client_secret_post">In request body</option>
                </select>
              </Field>
            </div>
            <Field label="Resource / audience (optional)" hint="Sent as the RFC 8707 resource parameter">
              <input value={f.auth_config.audience ?? ""} onChange={(e) => setCfg("audience", e.target.value)} />
            </Field>
          </>
        )}
        <details>
          <summary className="small">Advanced</summary>
          <div className="stack" style={{ marginTop: 10 }}>
            <Field label="Extra headers (JSON)" hint='e.g. {"X-Tenant": "acme"}'>
              <input className="mono" value={f.extra_headers} onChange={(e) => set("extra_headers", e.target.value)} />
            </Field>
            <label className="field check"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled</label>
          </div>
        </details>
        {test && (
          <div className={`alert ${test.ok ? "alert-ok" : "alert-error"}`}>
            {test.ok
              ? <>Connected to <strong>{test.server?.name || "server"}</strong> {test.server?.version} — {test.tool_count} tools in {test.ms} ms</>
              : <>Connection failed: {test.error}</>}
          </div>
        )}
      </div>
    </Drawer>
  );
}
