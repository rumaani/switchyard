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

## Your own demo site on Azure (automated, ~free)

Uses the Azure free account: Container Apps' monthly free grant (both apps scale to zero) and 12 months of free
PostgreSQL Flexible Server B1ms with 32 GB storage. Sign-in is real Entra ID using app roles.

1. **Make the image public:** GitHub → Packages → switchyard → Package settings → Change visibility → Public.
2. **Run the setup once** in Azure Cloud Shell (Bash) at https://shell.azure.com:
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/rumaani/switchyard/main/scripts/azure-setup.sh
   ADMIN_EMAIL=you@example.com bash azure-setup.sh
   ```
   About 10 minutes. It prints the site address and four values.
3. **Add the four values** as repository variables (Settings → Secrets and variables → Actions → Variables):
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`.
4. From then on, every push to `main` → CI → image published → **Deploy to Azure** rolls both apps to that image
   and checks `/readyz`. Run it by hand any time from Actions → Deploy to Azure → Run workflow.

Give a colleague access: Entra ID → Enterprise applications → “Switchyard (switchyard)” → Users and groups →
Add assignment → pick one of the `sy-*` roles (they map to admin / builder / operator / viewer / auditor).

Cost guard-rails: keep `--max-replicas 1`, leave PostgreSQL storage at 32 GB with auto-grow off, and set a
budget alert on the resource group (Cost Management → Budgets). After the free year, PostgreSQL B1ms is the main
cost; delete the resource group to stop everything: `az group delete -n rg-switchyard`.

### Troubleshooting the demo setup

- **“The location is restricted from performing this operation”** on PostgreSQL: new subscriptions are often
  blocked in busy regions. The script tries several US regions; override with `PG_LOCATIONS="westus3 canadacentral"`.
- **Deploy fails at `azure/login` with `AADSTS700213 … No matching federated identity record`**: GitHub presented
  the repo by numeric ids (`repo:<owner>@<id>/<repo>@<id>:environment:production`). Re-run the setup script (it
  registers both subject forms), or add that exact subject as a federated credential on “Switchyard GitHub deploy”.
- **Site shows the Azure placeholder page**: the configure step didn't finish — re-run the script; it is idempotent.

## Option A — Azure Container Apps for a client (best fit when the client is on Entra ID)

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
