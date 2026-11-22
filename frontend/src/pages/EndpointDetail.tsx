import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ago, api, type Endpoint, type Tool } from "../api";
import { useAuth } from "../auth";
import { SchemaForm } from "../components/SchemaForm";
import { Badge, Empty, PageHeader, Spinner, Status, useToast, ValueView } from "../components/ui";
import { authLabel, EndpointForm } from "./Endpoints";

export default function EndpointDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const [ep, setEp] = useState<Endpoint | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Tool | null>(null);

  const load = () => api.get<Endpoint>(`/api/endpoints/${id}`).then((e) => {
    setEp(e);
    setSel((s) => (s ? e.tools?.find((t) => t.name === s.name) ?? null : e.tools?.[0] ?? null));
  });
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const tools = useMemo(() => (ep?.tools ?? []).filter((t) =>
    !q || t.name.toLowerCase().includes(q.toLowerCase()) || t.description.toLowerCase().includes(q.toLowerCase())), [ep, q]);

  if (!ep) return <Spinner />;

  const refresh = async () => {
    setBusy(true);
    try {
      const e = await api.post<Endpoint>(`/api/endpoints/${id}/refresh`);
      setEp(e);
      toast(e.status === "ok" ? "ok" : "error", e.status === "ok" ? `${e.tool_count} tools discovered` : e.status_detail);
    } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!confirm(`Remove endpoint "${ep.name}"? Workflows that use it will fail until it is re-added.`)) return;
    await api.del(`/api/endpoints/${id}`);
    toast("ok", "Endpoint removed");
    nav("/endpoints");
  };

  return (
    <>
      <PageHeader
        title={<>{ep.display_name} <code className="ep-name">{ep.name}</code></>}
        subtitle={ep.description}
        actions={<>
          <button className="btn" disabled={busy} onClick={refresh}>{busy ? "Checking…" : "Refresh tools"}</button>
          {can("endpoints.manage") && <button className="btn" onClick={() => setEditing(true)}>Edit</button>}
          {can("endpoints.manage") && <button className="btn btn-danger-ghost" onClick={remove}>Remove</button>}
        </>}
      />
      <div className="row gap-s wrap meta-row">
        <Status value={ep.enabled ? ep.status : "cancelled"} />
        {ep.server?.name && <Badge>{ep.server.name} {ep.server.version}</Badge>}
        <Badge tone="info">{authLabel(ep.auth_type)}</Badge>
        {ep.url && <code className="small muted">{ep.url}</code>}
        <span className="small muted">checked {ago(ep.last_checked_at)}</span>
      </div>
      {ep.status === "error" && <div className="alert alert-error">{ep.status_detail}</div>}

      {(ep.tools?.length ?? 0) === 0 ? (
        <Empty title="No tools discovered">Check the connection, then press “Refresh tools”.</Empty>
      ) : (
        <div className="split">
          <aside className="card tool-list">
            <input placeholder={`Search ${ep.tools!.length} tools…`} value={q} onChange={(e) => setQ(e.target.value)} />
            <ul>
              {tools.map((t) => (
                <li key={t.name}>
                  <button className={sel?.name === t.name ? "tool-item active" : "tool-item"} onClick={() => setSel(t)}>
                    <span className="row between">
                      <code>{t.name}</code>
                      <span className="row gap-xs">
                        {t.readOnly && <Badge tone="ok" title="Read-only">R</Badge>}
                        {t.destructive && <Badge tone="danger" title="Destructive">!</Badge>}
                        {!t.allowed && <Badge tone="muted" title="Not granted to your roles">🔒</Badge>}
                      </span>
                    </span>
                    <span className="small muted clamp">{t.title || t.description}</span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
          {sel && <ToolPanel key={sel.name} ep={ep} tool={sel} />}
        </div>
      )}
      {editing && <EndpointForm initial={ep} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); load(); }} />}
    </>
  );
}

function ToolPanel({ ep, tool }: { ep: Endpoint; tool: Tool }) {
  const { can } = useAuth();
  const toast = useToast();
  const [args, setArgs] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const canCall = can("tools.call") && tool.allowed && ep.enabled;

  const call = async () => {
    if (tool.destructive && !confirm(`"${tool.name}" is marked destructive. Run it against ${ep.display_name}?`)) return;
    setBusy(true); setRes(null);
    try { setRes(await api.post(`/api/endpoints/${ep.id}/tools/${encodeURIComponent(tool.name)}/call`, { arguments: args })); }
    catch (e: any) { toast("error", e.message); }
    setBusy(false);
  };

  return (
    <section className="card tool-panel">
      <div className="row between">
        <h2><code>{tool.name}</code></h2>
        <span className="row gap-xs">
          {tool.readOnly && <Badge tone="ok">read-only</Badge>}
          {tool.destructive && <Badge tone="danger">destructive</Badge>}
        </span>
      </div>
      {tool.description && <p className="muted">{tool.description}</p>}
      {!tool.allowed && <div className="alert alert-warn small">Your roles are not granted this tool on <code>{ep.name}</code>. An admin can change that under Access → Endpoint grants.</div>}
      <h3>Arguments</h3>
      <SchemaForm schema={tool.inputSchema} value={args} onChange={setArgs} />
      <div className="row gap-s">
        <button className="btn btn-primary" disabled={!canCall || busy} onClick={call}
          title={canCall ? "" : "You can't call this tool directly"}>{busy ? "Running…" : "Run tool"}</button>
        {!can("tools.call") && <span className="small muted">Direct tool calls need the <code>tools.call</code> permission — use a workflow instead.</span>}
      </div>
      {res && (
        <div className="result">
          <div className="row between">
            <h3>{res.ok ? "Result" : "Error"}</h3>
            <span className="small muted">
              {res.ms} ms{res.trace && ` · ${res.trace.map((t: any) => `${t.method} ${t.ms}ms`).join(" → ")}`}
            </span>
          </div>
          {res.ok ? <ValueView value={res.value} /> : <div className="alert alert-error">{typeof res.value === "string" ? res.value : res.error}</div>}
        </div>
      )}
    </section>
  );
}
