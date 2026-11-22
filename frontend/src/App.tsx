import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Spinner } from "./components/ui";
import Access from "./pages/Access";
import Audit from "./pages/Audit";
import EndpointDetail from "./pages/EndpointDetail";
import Endpoints from "./pages/Endpoints";
import Home from "./pages/Home";
import Login from "./pages/Login";
import RunDetail from "./pages/RunDetail";
import Runs from "./pages/Runs";
import WorkflowEditor from "./pages/WorkflowEditor";
import Workflows from "./pages/Workflows";

export function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="var(--accent)" />
      <path d="M6 11h8l4 10h8M6 21h8l4-10h8" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const NAV = [
  { to: "/", label: "Home", icon: "◧", perm: null, end: true },
  { to: "/workflows", label: "Workflows", icon: "⇉", perm: "workflows.view" },
  { to: "/runs", label: "Runs", icon: "▸", perm: "workflows.view" },
  { to: "/endpoints", label: "Endpoints", icon: "⌁", perm: "endpoints.view" },
  { to: "/access", label: "Access", icon: "⚿", perm: "access.manage" },
  { to: "/audit", label: "Audit log", icon: "☰", perm: "audit.view" },
];

export default function App() {
  const { me, loading, can, logout, config } = useAuth();
  if (loading) return <div className="center-screen"><Spinner /></div>;
  if (!me) return <Login />;

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand"><Logo /><span>Switchyard</span></div>
        <ul>
          {NAV.filter((n) => !n.perm || can(n.perm)).map((n) => (
            <li key={n.to}>
              <NavLink to={n.to} end={n.end} className={({ isActive }) => (isActive ? "nav active" : "nav")}>
                <span className="nav-icon" aria-hidden>{n.icon}</span>
                {n.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-foot">
          <div className="who">
            <div className="avatar" aria-hidden>{(me.name || me.email).slice(0, 1).toUpperCase()}</div>
            <div className="who-text">
              <strong title={me.email}>{me.name || me.email}</strong>
              <span>{me.roles.length ? me.roles.join(", ") : "no role"}</span>
            </div>
          </div>
          <button className="btn btn-ghost btn-block" onClick={logout}>Sign out</button>
          {config?.mode === "dev" && <div className="env-pill">Dev sign-in mode</div>}
        </div>
      </nav>
      <main className="content">
        {me.roles.length === 0 ? (
          <NoAccess groups={me.groups} />
        ) : (
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/workflows/new" element={<WorkflowEditor />} />
            <Route path="/workflows/:id" element={<WorkflowEditor />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/endpoints" element={<Endpoints />} />
            <Route path="/endpoints/:id" element={<EndpointDetail />} />
            <Route path="/access" element={<Access />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        )}
      </main>
    </div>
  );
}

function NoAccess({ groups }: { groups: string[] }) {
  return (
    <div className="card narrow">
      <h1>You're signed in, but have no role yet</h1>
      <p className="muted">
        Access is granted through your identity-provider groups. Ask a Switchyard admin to map one of your groups to a role.
      </p>
      <p className="small">Your groups: {groups.length ? groups.map((g) => <code key={g}>{g}</code>) : <em>none received</em>}</p>
    </div>
  );
}
