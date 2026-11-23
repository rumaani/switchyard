# Deploying to a client

Switchyard ships as **one container image** (UI + API) plus a **Postgres database**. Deploy one instance per client,
inside the client's cloud tenant where possible — it keeps credentials, data and audit logs in their boundary and
makes security review much shorter.

## Getting the image

Every push to `main` publishes `ghcr.io/rumaani/switchyard:latest` (and `:vX.Y.Z` for tags) via
`.github/workflows/release.yml`. Pull it from any container host, or build locally with `docker build -t switchyard .`.
If the package is private, make it public under GitHub → Packages → switchyard → Package settings, or give the host
a read-only token.

## Required settings

| Variable | Production value |
|---|---|
| `ENVIRONMENT` | `production` (turns on guard-rails, Secure cookies, HSTS) |
| `PUBLIC_URL` | `https://switchyard.<client-domain>` |
| `SECRET_KEY` | 64 random characters, from the vault (`python -c "import secrets;print(secrets.token_urlsafe(48))"`) |
| `DATABASE_URL` | `postgresql+psycopg://<user>:<pass>@<host>:5432/switchyard?sslmode=require` |
| `AUTH_MODE` + `OIDC_*` | see [SSO_SETUP.md](SSO_SETUP.md) |
| `ADMIN_EMAILS` | you + the client's platform owner (break-glass) |
| `SEED_DEMO` | `false` |

**Run exactly one replica** in v0.1 (workflow runs execute in-process; see ARCHITECTURE.md).

## Option A — Azure Container Apps (best fit when the client is on Entra ID)

```bash
RG=rg-switchyard; LOC=eastus; ACR=<registry>; APP=switchyard
az group create -n $RG -l $LOC
az acr build -r $ACR -t switchyard:0.1.0 .
az postgres flexible-server create -g $RG -n $APP-pg --tier Burstable --sku-name Standard_B1ms --version 16
az keyvault create -g $RG -n $APP-kv
az keyvault secret set --vault-name $APP-kv -n secret-key --value "$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
az containerapp env create -g $RG -n $APP-env -l $LOC
az containerapp create -g $RG -n $APP --environment $APP-env \
  --image $ACR.azurecr.io/switchyard:0.1.0 --registry-server $ACR.azurecr.io \
  --ingress external --target-port 8000 --min-replicas 1 --max-replicas 1 \
  --system-assigned \
  --env-vars ENVIRONMENT=production PUBLIC_URL=https://switchyard.client.com AUTH_MODE=oidc \
             OIDC_DISCOVERY_URL=... OIDC_CLIENT_ID=... OIDC_GROUPS_CLAIM=roles SEED_DEMO=false \
             SECRET_KEY=secretref:secret-key OIDC_CLIENT_SECRET=secretref:oidc-secret DATABASE_URL=secretref:db-url
# map the Key Vault secrets with `az containerapp secret set --keyvaultref`, then add the custom domain + managed cert:
az containerapp hostname add -g $RG -n $APP --hostname switchyard.client.com
az containerapp hostname bind -g $RG -n $APP --hostname switchyard.client.com --environment $APP-env --validation-method CNAME
```

## Option B — AWS

App Runner (simplest) or ECS Fargate behind an ALB · RDS for PostgreSQL · Secrets Manager for `SECRET_KEY`,
`OIDC_CLIENT_SECRET`, `DATABASE_URL` · ACM certificate · Route 53 record. Set min = max = 1 task.

## Option C — Oracle Cloud (OCI)

OCI Container Instances or OKE · OCI Database with PostgreSQL · OCI Vault · Load Balancer with a certificate.
Natural fit when the main endpoints are Oracle EPM / Essbase 26ai in the same tenancy.

## Option D — your own demo site

For a public demo, deploy the same image with `AUTH_MODE=oidc` against your own Entra tenant or Okta developer org,
`SEED_DEMO=true` and the mock vendors (`mock` service from `docker-compose.yml`) — never point a public demo at
real client endpoints.

## Go-live checklist

- [ ] `ENVIRONMENT=production`, app starts without guard-rail errors
- [ ] TLS on the custom domain; `PUBLIC_URL` matches it exactly
- [ ] Redirect + logout URIs registered in the IdP; test sign-in as each role
- [ ] Group → role mappings agreed with the client and recorded (export Access → screenshots or audit CSV)
- [ ] Endpoint credentials are **service accounts** scoped to least privilege on the vendor side
- [ ] Endpoint grants reviewed: destructive tools only for roles that need them
- [ ] Approval steps on every workflow that writes to a system of record
- [ ] Outbound network egress restricted to the vendor MCP hosts
- [ ] Container stdout shipped to the client's SIEM (audit events are JSON lines from logger `switchyard.audit`)
- [ ] Daily database backups + restore tested
- [ ] `/healthz` (liveness) and `/readyz` (DB) wired to the platform's probes
