import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ago, api, type Run } from "../api";
import { Empty, PageHeader, Spinner, Status } from "../components/ui";

const FILTERS = ["", "waiting_approval", "running", "succeeded", "failed", "rejected", "cancelled"];

export default function Runs() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "";
  const [runs, setRuns] = useState<Run[] | null>(null);

  useEffect(() => {
    const load = () => api.get<Run[]>(`/api/runs?limit=100${status ? `&status=${status}` : ""}`).then(setRuns);
    setRuns(null);
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [status]);

  return (
    <>
      <PageHeader title="Runs" subtitle="Every workflow execution, with its inputs, step results and approvals." />
      <div className="row gap-xs wrap filter-row">
        {FILTERS.map((f) => (
          <button key={f} className={status === f ? "pill active" : "pill"} onClick={() => setParams(f ? { status: f } : {})}>
            {f ? f.replace("_", " ") : "all"}
          </button>
        ))}
      </div>
      {!runs ? <Spinner /> : runs.length === 0 ? <Empty title="No runs match" /> : (
        <div className="card flush">
          <table className="table">
            <thead><tr><th>#</th><th>Workflow</th><th>Status</th><th>Progress</th><th>Started by</th><th>Started</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="clickable">
                  <td><Link to={`/runs/${r.id}`}>#{r.id}</Link></td>
                  <td><Link to={`/runs/${r.id}`}>{r.workflow_name}</Link> <span className="muted small">v{r.workflow_version}</span></td>
                  <td><Status value={r.status} /></td>
                  <td>
                    <div className="progress" title={`${r.progress.done}/${r.progress.total} steps`}>
                      <div style={{ width: `${(100 * r.progress.done) / Math.max(1, r.progress.total)}%` }} />
                    </div>
                  </td>
                  <td className="small">{r.started_by}</td>
                  <td className="small muted">{ago(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
