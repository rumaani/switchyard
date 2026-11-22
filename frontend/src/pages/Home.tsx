import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ago, api, type Endpoint, type Run, type Workflow } from "../api";
import { useAuth } from "../auth";
import { Empty, PageHeader, Spinner, Status } from "../components/ui";

export default function Home() {
  const { me, can } = useAuth();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [eps, setEps] = useState<Endpoint[]>([]);
  const [wfs, setWfs] = useState<Workflow[]>([]);

  useEffect(() => {
    if (can("workflows.view")) {
      api.get<Run[]>("/api/runs?limit=20").then(setRuns);
      api.get<Workflow[]>("/api/workflows").then(setWfs);
    } else setRuns([]);
    if (can("endpoints.view")) api.get<Endpoint[]>("/api/endpoints").then(setEps);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!runs) return <Spinner />;
  const waiting = runs.filter((r) => r.status === "waiting_approval");
  const okEps = eps.filter((e) => e.status === "ok").length;
  const firstName = (me?.name || me?.email || "").split(/[ @]/)[0];

  return (
    <>
      <PageHeader title={`Hello, ${firstName}`} subtitle="Here's what's happening across your connected systems." />
      <div className="stats">
        <Link to="/runs?status=waiting_approval" className="stat">
          <span className="stat-num">{waiting.length}</span>
          <span className="stat-label">Waiting for approval</span>
        </Link>
        <Link to="/workflows" className="stat">
          <span className="stat-num">{wfs.length}</span>
          <span className="stat-label">Workflows</span>
        </Link>
        <Link to="/endpoints" className="stat">
          <span className="stat-num">{okEps}<small>/{eps.length}</small></span>
          <span className="stat-label">Endpoints connected</span>
        </Link>
        <Link to="/runs" className="stat">
          <span className="stat-num">{runs.filter((r) => r.status === "failed").length}</span>
          <span className="stat-label">Recent failures</span>
        </Link>
      </div>

      {waiting.length > 0 && (
        <section className="card">
          <h2>Needs a decision</h2>
          <ul className="list">
            {waiting.map((r) => (
              <li key={r.id}>
                <Link to={`/runs/${r.id}`} className="list-row">
                  <span><strong>{r.workflow_name}</strong> <span className="muted">#{r.id} · started by {r.started_by}</span></span>
                  <Status value={r.status} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid-2">
        <section className="card">
          <div className="row between"><h2>Recent runs</h2><Link to="/runs" className="small">All runs →</Link></div>
          {runs.length === 0 ? (
            <Empty title="No runs yet">Start one from <Link to="/workflows">Workflows</Link>.</Empty>
          ) : (
            <ul className="list">
              {runs.slice(0, 8).map((r) => (
                <li key={r.id}>
                  <Link to={`/runs/${r.id}`} className="list-row">
                    <span>{r.workflow_name} <span className="muted small">#{r.id} · {ago(r.created_at)}</span></span>
                    <Status value={r.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card">
          <div className="row between"><h2>Endpoints</h2><Link to="/endpoints" className="small">Manage →</Link></div>
          {eps.length === 0 ? (
            <Empty title="No endpoints visible" />
          ) : (
            <ul className="list">
              {eps.map((e) => (
                <li key={e.id}>
                  <Link to={`/endpoints/${e.id}`} className="list-row">
                    <span>{e.display_name} <code className="small">{e.name}</code></span>
                    <Status value={e.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
