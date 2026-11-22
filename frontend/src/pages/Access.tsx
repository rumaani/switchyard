import { useEffect, useState } from "react";
import { ago, api, type Endpoint, type Role } from "../api";
import { useAuth } from "../auth";
import { Badge, PageHeader, Spinner, Tabs, useToast } from "../components/ui";

type Tab = "mappings" | "roles" | "grants" | "users";

export default function Access() {
  const [tab, setTab] = useState<Tab>("mappings");
  const [roles, setRoles] = useState<Role[] | null>(null);
  const loadRoles = () => api.get<Role[]>("/api/access/roles").then(setRoles);
  useEffect(() => { loadRoles(); }, []);
  if (!roles) return <Spinner />;
  return (
    <>
      <PageHeader title="Access" subtitle="Who can do what. People get roles through their identity-provider groups — no per-user setup." />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "mappings", label: "Group → role" },
        { id: "roles", label: "Roles & permissions" },
        { id: "grants", label: "Endpoint grants" },
        { id: "users", label: "Users" },
      ]} />
      {tab === "mappings" && <Mappings roles={roles} />}
      {tab === "roles" && <Roles roles={roles} reload={loadRoles} />}
      {tab === "grants" && <Grants roles={roles} reload={loadRoles} />}
      {tab === "users" && <Users />}
    </>
  );
}

function Mappings({ roles }: { roles: Role[] }) {
  const toast = useToast();
  const [rows, setRows] = useState<any[] | null>(null);
  const [group, setGroup] = useState("");
  const [roleId, setRoleId] = useState(roles[0]?.id ?? 0);
  const load = () => api.get("/api/access/mappings").then(setRows);
  useEffect(() => { load(); }, []);
  const add = async () => {
    try { await api.post("/api/access/mappings", { idp_group: group, role_id: Number(roleId) }); setGroup(""); load(); }
    catch (e: any) { toast("error", e.message); }
  };
  if (!rows) return <Spinner />;
  return (
    <div className="card">
      <p className="muted small">
        Enter the group exactly as your IdP sends it in the token — a name (Okta, Keycloak) or an object ID / app-role value (Entra ID).
      </p>
      <table className="table">
        <thead><tr><th>IdP group</th><th>Role</th><th /></tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td><code>{m.idp_group}</code></td>
              <td><Badge tone="info">{m.role}</Badge></td>
              <td className="right"><button className="btn btn-ghost" onClick={async () => { await api.del(`/api/access/mappings/${m.id}`); load(); }}>Remove</button></td>
            </tr>
          ))}
          <tr>
            <td><input placeholder="e.g. FIN-EPM-Operators" value={group} onChange={(e) => setGroup(e.target.value)} /></td>
            <td><select value={roleId} onChange={(e) => setRoleId(Number(e.target.value))}>{roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></td>
            <td className="right"><button className="btn btn-primary" disabled={!group.trim()} onClick={add}>Add mapping</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Roles({ roles, reload }: { roles: Role[]; reload: () => void }) {
  const toast = useToast();
  const [perms, setPerms] = useState<Record<string, string>>({});
  const [selId, setSelId] = useState(roles[0]?.id);
  const sel = roles.find((r) => r.id === selId);
  const [draft, setDraft] = useState<string[]>(sel?.permissions ?? []);
  const [desc, setDesc] = useState(sel?.description ?? "");
  const [newName, setNewName] = useState("");
  useEffect(() => { api.get("/api/access/permissions").then(setPerms); }, []);
  useEffect(() => { setDraft(sel?.permissions ?? []); setDesc(sel?.description ?? ""); }, [selId, roles]); // eslint-disable-line

  const save = async () => {
    try { await api.put(`/api/access/roles/${sel!.id}`, { name: sel!.name, description: desc, permissions: draft }); toast("ok", "Role saved"); reload(); }
    catch (e: any) { toast("error", e.message); }
  };
  const create = async () => {
    try { const r = await api.post<Role>("/api/access/roles", { name: newName, permissions: [] }); setNewName(""); await reload(); setSelId(r.id); }
    catch (e: any) { toast("error", e.message); }
  };
  const del = async () => {
    if (!confirm(`Delete role "${sel!.name}"? Its group mappings and grants go with it.`)) return;
    await api.del(`/api/access/roles/${sel!.id}`); setSelId(roles[0]?.id); reload();
  };

  return (
    <div className="split">
      <aside className="card tool-list">
        <ul>
          {roles.map((r) => (
            <li key={r.id}>
              <button className={r.id === selId ? "tool-item active" : "tool-item"} onClick={() => setSelId(r.id)}>
                <span className="row between"><strong>{r.name}</strong>{r.builtin && <Badge tone="muted">built-in</Badge>}</span>
                <span className="small muted clamp">{r.description}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="row gap-xs">
          <input placeholder="new role name" value={newName} onChange={(e) => setNewName(e.target.value.toLowerCase())} />
          <button className="btn" disabled={!newName} onClick={create}>Add</button>
        </div>
      </aside>
      {sel && (
        <section className="card stack">
          <div className="row between"><h2>{sel.name}</h2>{!sel.builtin && <button className="btn btn-danger-ghost" onClick={del}>Delete role</button>}</div>
          <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description" />
          {sel.permissions.includes("*") ? (
            <div className="alert alert-ok">Admin has every permission and every tool on every endpoint.</div>
          ) : (
            <div className="perm-grid">
              {Object.entries(perms).map(([k, v]) => (
                <label key={k} className="perm">
                  <input type="checkbox" checked={draft.includes(k)} onChange={(e) => setDraft(e.target.checked ? [...draft, k] : draft.filter((x) => x !== k))} />
                  <span><code>{k}</code><span className="small muted">{v}</span></span>
                </label>
              ))}
            </div>
          )}
          <div><button className="btn btn-primary" onClick={save}>Save role</button></div>
        </section>
      )}
    </div>
  );
}

function Grants({ roles, reload }: { roles: Role[]; reload: () => void }) {
  const toast = useToast();
  const [eps, setEps] = useState<Endpoint[] | null>(null);
  useEffect(() => { api.get<Endpoint[]>("/api/endpoints").then(setEps); }, []);
  if (!eps) return <Spinner />;
  const editable = roles.filter((r) => !r.permissions.includes("*"));
  const save = async (role: Role, ep: Endpoint, text: string) => {
    const tools = text.split(",").map((t) => t.trim()).filter(Boolean);
    const current = role.grants.find((g) => g.endpoint_id === ep.id)?.tools ?? [];
    if (tools.join(",") === current.join(",")) return;
    try { await api.put("/api/access/grants", { role_id: role.id, endpoint_id: ep.id, tools }); toast("ok", `${role.name} @ ${ep.name} updated`); reload(); }
    catch (e: any) { toast("error", e.message); }
  };
  return (
    <div className="card flush">
      <p className="muted small pad">
        Which tools each role may call on each endpoint. Comma-separated names or patterns: <code>*</code> = all, <code>get_*</code> = every tool starting with get_. Empty = no access. Admin always has everything.
      </p>
      <div className="table-scroll">
        <table className="table grants">
          <thead><tr><th>Role</th>{eps.map((e) => <th key={e.id}>{e.display_name}<br /><code className="small">{e.name}</code></th>)}</tr></thead>
          <tbody>
            {editable.map((r) => (
              <tr key={r.id}>
                <td><strong>{r.name}</strong></td>
                {eps.map((e) => {
                  const g = r.grants.find((x) => x.endpoint_id === e.id);
                  return (
                    <td key={e.id}>
                      <input key={`${r.id}-${e.id}-${g?.tools.join(",")}`} className="mono" defaultValue={g?.tools.join(", ") ?? ""} placeholder="no access"
                        onBlur={(ev) => save(r, e, ev.target.value)} onKeyDown={(ev) => ev.key === "Enter" && (ev.target as HTMLInputElement).blur()} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Users() {
  const { me } = useAuth();
  const [users, setUsers] = useState<any[] | null>(null);
  const load = () => api.get("/api/access/users").then(setUsers);
  useEffect(() => { load(); }, []);
  if (!users) return <Spinner />;
  return (
    <div className="card flush">
      <p className="muted small pad">Users appear here after their first sign-in. Roles come from groups; disabling blocks sign-in and stops their running workflows at the next step.</p>
      <table className="table">
        <thead><tr><th>User</th><th>Roles</th><th>IdP groups</th><th>Last sign-in</th><th /></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className={u.disabled ? "dim" : ""}>
              <td><strong>{u.name}</strong><br /><span className="small muted">{u.email}</span></td>
              <td>{u.roles.length ? u.roles.map((r: string) => <Badge key={r} tone="info">{r}</Badge>) : <span className="muted small">none</span>}</td>
              <td className="small">{u.groups.map((g: string) => <code key={g}>{g}</code>)}</td>
              <td className="small muted">{ago(u.last_login_at)}</td>
              <td className="right">
                {u.id !== me?.id && (
                  <button className="btn btn-ghost" onClick={async () => { await api.patch(`/api/access/users/${u.id}`, { disabled: !u.disabled }); load(); }}>
                    {u.disabled ? "Enable" : "Disable"}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
