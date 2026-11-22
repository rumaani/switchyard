import { useState } from "react";
import { Logo } from "../App";
import { api, type Me } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../components/ui";

// Matches the bundled demo IdP / seed group mappings.
const PERSONAS = [
  { email: "alice@client.example", name: "Alice Admin", groups: ["sy-admins"], blurb: "Full control" },
  { email: "ben@client.example", name: "Ben Builder", groups: ["sy-builders"], blurb: "Builds endpoints & workflows" },
  { email: "olivia@client.example", name: "Olivia Operator", groups: ["sy-operators"], blurb: "Runs workflows" },
  { email: "oscar@client.example", name: "Oscar Operator", groups: ["sy-operators"], blurb: "Approves Olivia's runs" },
  { email: "vera@client.example", name: "Vera Viewer", groups: ["sy-viewers"], blurb: "Read-only" },
  { email: "adam@client.example", name: "Adam Auditor", groups: ["sy-auditors"], blurb: "Audit log" },
];

export default function Login() {
  const { config, setMe } = useAuth();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [groups, setGroups] = useState("");
  const error = new URLSearchParams(window.location.search).get("login_error");

  const devLogin = async (body: { email: string; name?: string; groups: string[] }) => {
    try {
      setMe(await api.post<Me>("/api/auth/dev-login", body));
    } catch (e: any) {
      toast("error", e.message);
    }
  };

  return (
    <div className="login">
      <div className="login-card">
        <div className="brand big"><Logo /><span>Switchyard</span></div>
        <p className="muted">Connect your vendors' MCP tools and run them together as governed workflows.</p>
        {error && <div className="alert alert-error">Sign-in failed ({error}). Try again or contact your admin.</div>}

        {config?.mode === "oidc" ? (
          <a className="btn btn-primary btn-block btn-lg" href="/auth/login">Sign in with {config.provider_name}</a>
        ) : (
          <>
            <div className="alert alert-warn small">
              <strong>Development sign-in.</strong> Production deployments use your SSO (Entra ID, Okta, Ping…).
            </div>
            <div className="persona-grid">
              {PERSONAS.map((p) => (
                <button key={p.email} className="persona" onClick={() => devLogin(p)}>
                  <strong>{p.name}</strong>
                  <span>{p.blurb}</span>
                </button>
              ))}
            </div>
            <details className="small">
              <summary>Sign in as someone else</summary>
              <form className="stack gap-s" onSubmit={(e) => { e.preventDefault(); devLogin({ email, groups: groups.split(",").map((g) => g.trim()).filter(Boolean) }); }}>
                <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
                <input placeholder="IdP groups, comma separated" value={groups} onChange={(e) => setGroups(e.target.value)} />
                <button className="btn">Sign in</button>
              </form>
            </details>
          </>
        )}
      </div>
    </div>
  );
}
