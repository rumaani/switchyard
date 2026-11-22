import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, TERMINAL, when, type Run, type RunStep } from "../api";
import { useAuth } from "../auth";
import { PageHeader, Spinner, Status, useToast, ValueView } from "../components/ui";

export default function RunDetail() {
  const { id } = useParams();
  const { me, can } = useAuth();
  const toast = useToast();
  const [run, setRun] = useState<Run | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const r = await api.get<Run>(`/api/runs/${id}`);
      if (!alive) return;
      setRun(r);
      if (!TERMINAL.has(r.status)) timer = setTimeout(load, r.status === "waiting_approval" ? 4000 : 1200);
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [id]);

  if (!run) return <Spinner />;

  const cancel = async () => {
    if (!confirm("Cancel this run? Steps already completed are not undone.")) return;
    try { setRun(await api.post<Run>(`/api/runs/${id}/cancel`)); } catch (e: any) { toast("error", e.message); }
  };
  const canCancel = !TERMINAL.has(run.status) && (run.started_by === me?.email || can("runs.approve"));

  return (
    <>
      <PageHeader
        title={<>{run.workflow_name} <span className="muted">#{run.id}</span></>}
        subtitle={<>Started by {run.started_by} · {when(run.created_at)}{run.finished_at && <> · finished {when(run.finished_at)}</>} · version {run.workflow_version}</>}
        actions={<>
          {canCancel && <button className="btn btn-danger-ghost" onClick={cancel}>Cancel run</button>}
          {run.workflow_id && <Link className="btn" to="/workflows">Back to workflows</Link>}
        </>}
      />
      <div className="row gap-s wrap meta-row">
        <Status value={run.status} />
        {Object.entries(run.inputs).map(([k, v]) => <span key={k} className="kv-chip"><span>{k}</span>{String(v)}</span>)}
      </div>
      {run.error && <div className={`alert ${run.status === "rejected" || run.status === "cancelled" ? "alert-warn" : "alert-error"}`}>{run.error}</div>}

      <ol className="timeline">
        {run.steps!.map((s, i) => <StepRow key={s.step_id} step={s} index={i} runId={run.id} onRun={setRun} />)}
      </ol>
    </>
  );
}

function StepRow({ step, index, runId, onRun }: { step: RunStep; index: number; runId: number; onRun: (r: Run) => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(step.status === "failed" || step.status === "waiting_approval");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (step.status === "waiting_approval" || step.status === "failed") setOpen(true); }, [step.status]);

  const decide = async (approve: boolean) => {
    if (!approve && !note.trim() && !confirm("Reject without a note?")) return;
    setBusy(true);
    try { onRun(await api.post<Run>(`/api/runs/${runId}/steps/${step.step_id}/decision`, { approve, note })); toast("ok", approve ? "Approved" : "Rejected"); }
    catch (e: any) { toast("error", e.message); }
    setBusy(false);
  };

  const hasDetail = step.request || step.output !== null || step.error;
  return (
    <li className={`tl-item tl-${step.status}`}>
      <span className="tl-dot">{index + 1}</span>
      <div className="tl-body card">
        <button className="tl-head" onClick={() => hasDetail && setOpen(!open)} aria-expanded={open}>
          <span className="grow">
            <strong>{step.type === "approval" ? "✋ " : ""}{step.title}</strong>
            {step.type === "tool" && <span className="muted small"> · {step.endpoint} / <code>{step.tool}</code></span>}
          </span>
          <span className="row gap-s small muted">
            {step.attempts > 1 && <span>{step.attempts} attempts</span>}
            {step.duration_ms !== null && <span>{step.duration_ms} ms</span>}
            <Status value={step.status} />
            {hasDetail && <span aria-hidden>{open ? "▾" : "▸"}</span>}
          </span>
        </button>

        {step.status === "waiting_approval" && (
          <div className="approval-box">
            <p>{step.request?.message}</p>
            {step.can_decide ? (
              <>
                <input placeholder="Note for the record (optional for approval)" value={note} onChange={(e) => setNote(e.target.value)} />
                <div className="row gap-s">
                  <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>Approve</button>
                  <button className="btn btn-danger" disabled={busy} onClick={() => decide(false)}>Reject</button>
                </div>
              </>
            ) : (
              <p className="small muted">Waiting for someone with approval rights (other than the person who started this run).</p>
            )}
          </div>
        )}
        {step.decided_by && (
          <p className="small muted">{step.status === "rejected" ? "Rejected" : "Approved"} by {step.decided_by} · {when(step.finished_at)}{step.decision_note && <> — “{step.decision_note}”</>}</p>
        )}

        {open && step.type === "tool" && (
          <div className="tl-detail">
            {step.error && <div className="alert alert-error">{step.error}</div>}
            {step.request?.arguments && (
              <details>
                <summary className="small">Arguments sent</summary>
                <pre className="code">{JSON.stringify(step.request.arguments, null, 2)}</pre>
              </details>
            )}
            {step.output !== null && step.output !== undefined && (
              <>
                <h4>Output</h4>
                <ValueView value={step.output} />
              </>
            )}
          </div>
        )}
      </div>
    </li>
  );
}
