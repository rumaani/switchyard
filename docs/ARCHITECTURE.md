# Architecture

```
 Browser ──HTTPS──▶ Switchyard (one container: React UI + FastAPI)
   │                    │  session cookie (signed, HttpOnly) · CSRF header · CSP
   │                    ├─ Sign-in ──OIDC──▶ Client IdP (Entra / Okta / Keycloak→SAML)
   │                    ├─ RBAC: IdP groups → roles → permissions + endpoint tool grants
   │                    ├─ Endpoint registry ──MCP Streamable HTTP──▶ vendor MCP servers
   │                    │      auth adapters: none · bearer · API key · OAuth client-credentials
   │                    ├─ Workflow engine (steps → tool calls / approvals, state in DB)
   │                    └─ Audit log (DB + JSON lines on stdout → SIEM)
   └────────────────────┴─ Postgres (or SQLite for a single-user pilot)
```

## Key decisions

**Backend-for-frontend.** The browser never sees an access token or vendor credential. It holds only a signed
session cookie with the user id; everything else is resolved server-side on each request.

**Roles from groups, re-evaluated every request.** A user's roles are computed from the groups claim captured at
sign-in plus the current mappings. Changing a mapping takes effect immediately; removing someone from a group in the
IdP takes effect at their next sign-in (sessions last `SESSION_MAX_AGE_S`, default 8 h). Disabling a user in
Switchyard takes effect immediately.

**Two layers of authorisation.** Permissions decide which screens and actions are available. Endpoint grants decide
which tools a role may call on which endpoint. Workflow steps are authorised *as the person who started the run*,
checked again when each step executes — so a run cannot be used to reach tools the starter couldn't call directly,
and revoking access stops in-flight runs at their next step.

**Workflows are fixed sequences, not agents.** Deterministic steps are testable and explainable to auditors. An
LLM-driven step type is on the roadmap as an opt-in, bounded step (allowed tool subset, approval after).

**Run snapshots.** A run stores the workflow definition it started with, so editing a workflow never rewrites
history. Workflows are versioned on every save; the audit log keeps the previous definition.

**Fail closed after a crash.** Steps that were mid-call when the process stopped are marked failed, not retried —
the vendor may already have applied the change. The error tells the operator to check the target system.

**Segregation of duties by default.** The person who started a run cannot approve its approval steps unless the
workflow author explicitly allows it per step.

## Data model

`users` · `roles` · `group_mappings` · `endpoints` (encrypted `auth_config_enc`) · `endpoint_grants` ·
`workflows` · `runs` · `run_steps` · `audit_events`

## Security notes

- Credentials are encrypted with a key derived from `SECRET_KEY` (Fernet). Rotating `SECRET_KEY` requires
  re-entering endpoint credentials. Production: keep `SECRET_KEY` in Key Vault / Secrets Manager / OCI Vault.
- Templates run in Jinja's `SandboxedEnvironment` with `StrictUndefined`.
- Audit details pass through a redactor (authorization, tokens, secrets, passwords, cookies).
- Endpoint URLs are admin-configured; restrict outbound egress at the network layer to the vendor hosts you expect.
- `ENVIRONMENT=production` refuses to start with dev sign-in, a weak `SECRET_KEY` or a non-https `PUBLIC_URL`,
  marks cookies `Secure`, sends HSTS and hides the OpenAPI UI.

## Known limits of v0.1 (and the plan)

| Limit | Plan |
|---|---|
| Runs execute in-process; **run one replica** | Move execution to a queue (Postgres `SKIP LOCKED` or Temporal) to scale out |
| Vendor calls use each endpoint's service credentials | Per-user delegated OAuth (auth code + PKCE per user, Entra on-behalf-of) as an extra adapter |
| Tables created with `create_all` | Alembic migrations before the first schema change in a live client |
| No scheduler | Cron triggers per workflow, running as a designated service principal |
| No SCIM | SCIM 2.0 endpoint for user deprovisioning push |
| No LLM steps | Bounded agent step: allowed-tool subset, max calls, mandatory approval after |
| Config lives in one deployment | Export/import “packs” (endpoints without secrets, roles, mappings, workflows) to reuse across clients |
