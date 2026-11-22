import { Fragment, useEffect, useState } from "react";
import { api, when } from "../api";
import { Badge, PageHeader, Spinner } from "../components/ui";

export default function Audit() {
  const [f, setF] = useState({ actor: "", action: "", outcome: "" });
  const [rows, setRows] = useState<any[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const qs = () => new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();

  useEffect(() => {
    const t = setTimeout(() => api.get(`/api/audit?limit=300&${qs()}`).then(setRows), 250);
    return () => clearTimeout(t);
  }, [f]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <PageHeader title="Audit log" subtitle="Append-only record of sign-ins, configuration changes, tool calls, runs and approvals."
        actions={<a className="btn" href={`/api/audit/export.csv?${qs()}`}>Export CSV</a>} />
      <div className="row gap-s wrap filter-row">
        <input placeholder="Actor email" value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} />
        <select value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}>
          <option value="">All actions</option>
          {["auth", "tool", "run", "workflow", "endpoint", "role", "mapping", "grant", "user", "audit"].map((a) => <option key={a} value={a}>{a}.*</option>)}
        </select>
        <select value={f.outcome} onChange={(e) => setF({ ...f, outcome: e.target.value })}>
          <option value="">Any outcome</option><option value="ok">ok</option><option value="denied">denied</option><option value="error">error</option>
        </select>
      </div>
      {!rows ? <Spinner /> : (
        <div className="card flush">
          <table className="table">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <Fragment key={e.id}>
                  <tr className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                    <td className="small muted nowrap">{when(e.ts)}</td>
                    <td className="small">{e.actor}</td>
                    <td><code>{e.action}</code></td>
                    <td className="small">{e.target}</td>
                    <td><Badge tone={e.outcome === "ok" ? "ok" : e.outcome === "denied" ? "warn" : "danger"}>{e.outcome}</Badge></td>
                  </tr>
                  {open === e.id && (
                    <tr><td colSpan={5}><pre className="code">{JSON.stringify({ ...e.detail, ip: e.ip }, null, 2)}</pre></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
