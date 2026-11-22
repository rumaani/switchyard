import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ago, api, type Run, type Workflow } from "../api";
import { useAuth } from "../auth";
import { Badge, Drawer, Empty, Field, PageHeader, Spinner, Status, useToast } from "../components/ui";

export default function Workflows() {
  const { can } = useAuth();
  const [wfs, setWfs] = useState<Workflow[] | null>(null);
  const [running, setRunning] = useState<Workflow | null>(null);
  useEffect(() => { api.get<Workflow[]>("/api/workflows").then(setWfs); }, []);

  return (
    <>
      <PageHeader
        title="Workflows"
        subtitle="Chains of tool calls across endpoints, with approvals where a person must sign off."
        actions={can("workflows.manage") && <Link className="btn btn-primary" to="/workflows/new">+ New workflow</Link>}
      />
      {!wfs ? <Spinner /> : wfs.length === 0 ? (
        <Empty title="No workflows yet">Create one to chain tools from different endpoints.</Empty>
      ) : (
        <div className="stack">
          {wfs.map((w) => (
            <div key={w.id} className="card wf-card">
              <div className="grow">
                <div className="row gap-s wrap">
                  <h3>{w.name}</h3>
                  <Badge>v{w.version}</Badge>
                  <Badge>{w.step_count} steps</Badge>
                  {w.allowed_roles.length > 0 && <Badge tone="info" title="Roles allowed to run">{w.allowed_roles.join(", ")}</Badge>}
                </div>
                <p className="muted small">{w.description}</p>
                <StepStrip wf={w} />
                <div className="small muted">
                  {w.last_run ? <>Last run <Link to={`/runs/${w.last_run.id}`}>#{w.last_run.id}</Link> {ago(w.last_run.created_at)} · <Status value={w.last_run.status} /></> : "Never run"}
                </div>
              </div>
              <div className="stack gap-s wf-actions">
                <button className="btn btn-primary" disabled={!w.can_run} onClick={() => setRunning(w)}
                  title={w.can_run ? "" : "Your roles can't run this workflow"}>▶ Run</button>
                {w.can_edit && <Link className="btn" to={`/workflows/${w.id}`}>Edit</Link>}
              </div>
            </div>
          ))}
        </div>
      )}
      {running && <RunDialog wf={running} onClose={() => setRunning(null)} />}
    </>
  );
}

function StepStrip({ wf }: { wf: Workflow }) {
  return (
    <ol className="step-strip">
      {wf.definition.steps.map((s) => (
        <li key={s.id} className={s.type === "approval" ? "approval" : ""} title={s.type === "tool" ? `${s.endpoint} · ${s.tool}` : "Approval"}>
          {s.type === "approval" ? "✋ " : ""}{s.title || s.id}
          {s.type === "tool" && <span className="muted"> · {s.endpoint}</span>}
        </li>
      ))}
    </ol>
  );
}

export function RunDialog({ wf, onClose }: { wf: Workflow; onClose: () => void }) {
  const toast = useToast();
  const nav = useNavigate();
  const [vals, setVals] = useState<Record<string, any>>(
    Object.fromEntries(wf.definition.inputs.map((i) => [i.name, i.default ?? (i.type === "boolean" ? false : "")])),
  );
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      const run = await api.post<Run>(`/api/workflows/${wf.id}/runs`, { inputs: vals });
      nav(`/runs/${run.id}`);
    } catch (e: any) { toast("error", e.message); setBusy(false); }
  };
  return (
    <Drawer title={`Run “${wf.name}”`} onClose={onClose}
      footer={<><span className="grow" /><button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={busy} onClick={start}>{busy ? "Starting…" : "Start run"}</button></>}>
      <div className="stack">
        {wf.definition.inputs.length === 0 && <p className="muted">This workflow takes no inputs.</p>}
        {wf.definition.inputs.map((i) => (
          <Field key={i.name} label={(i.label || i.name) + (i.required ? " *" : "")} hint={i.help}>
            {i.type === "boolean" ? (
              <input type="checkbox" checked={!!vals[i.name]} onChange={(e) => setVals({ ...vals, [i.name]: e.target.checked })} />
            ) : i.type === "json" ? (
              <textarea className="mono" rows={4} value={typeof vals[i.name] === "string" ? vals[i.name] : JSON.stringify(vals[i.name])}
                onChange={(e) => setVals({ ...vals, [i.name]: e.target.value })} />
            ) : (
              <input type={i.type === "number" ? "number" : "text"} value={vals[i.name] ?? ""}
                onChange={(e) => setVals({ ...vals, [i.name]: e.target.value })} />
            )}
          </Field>
        ))}
        <div className="small muted">
          Steps: {wf.definition.steps.map((s) => s.title || s.id).join(" → ")}
        </div>
      </div>
    </Drawer>
  );
}
