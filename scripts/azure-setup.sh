#!/usr/bin/env bash
# One-time setup of the Switchyard demo site on Azure (near-zero cost on an Azure free account).
#
# Run it in Azure Cloud Shell (Bash) — https://shell.azure.com — which is already signed in:
#
#   curl -fsSLO https://raw.githubusercontent.com/rumaani/switchyard/main/scripts/azure-setup.sh
#   ADMIN_EMAIL=you@example.com bash azure-setup.sh
#
# Creates, in one resource group:
#   * Azure Database for PostgreSQL flexible server (Burstable B1ms, 32 GB — free-account eligible)
#   * Container Apps environment + two apps, both scale to zero:
#       switchyard       public site (image ghcr.io/<owner>/switchyard)
#       switchyard-mock  demo vendor MCP servers, reachable only inside the environment
#   * Entra ID app registration for sign-in, with app roles sy-admins / sy-builders / sy-operators /
#     sy-viewers / sy-auditors (they match Switchyard's seeded role mappings); you are assigned sy-admins
#   * A deployment identity GitHub Actions uses (OIDC federation, no stored password), Contributor on the group
#
# Before running: make the image public — GitHub → your profile → Packages → switchyard →
# Package settings → Change visibility → Public (or Azure cannot pull it).
#
# Safe to re-run: existing resources are reused; the Entra client secret and DB password are only
# created the first time (re-run with RESET_SECRETS=1 to rotate them).
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:-rumaani/switchyard}"
LOCATION="${LOCATION:-eastus}"
RG="${RG:-rg-switchyard}"
ENV_NAME="${ENV_NAME:-switchyard-env}"
APP="${APP:-switchyard}"
MOCK="${MOCK:-switchyard-mock}"
IMAGE="${IMAGE:-ghcr.io/${GITHUB_REPO%%/*}/switchyard:latest}"
PG="${PG:-switchyard-pg-$(az account show --query id -o tsv | cut -c1-6)}"
ADMIN_EMAIL="${ADMIN_EMAIL:?Set ADMIN_EMAIL=you@example.com (gets admin as a break-glass account)}"
RESET_SECRETS="${RESET_SECRETS:-0}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
say "Subscription $SUB_ID · tenant $TENANT_ID · region $LOCATION"

say "Registering resource providers (first run can take a few minutes)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL; do
  az provider register -n "$ns" --wait >/dev/null
done
az extension add -n containerapp --upgrade -y >/dev/null 2>&1 || true

say "Resource group $RG"
az group create -n "$RG" -l "$LOCATION" -o none

# ---------------------------------------------------------------- PostgreSQL
if ! az postgres flexible-server show -g "$RG" -n "$PG" -o none 2>/dev/null; then
  say "PostgreSQL flexible server $PG (Burstable B1ms, 32 GB) — takes ~5 minutes"
  PG_PASS="Sy-$(openssl rand -hex 16)"
  az postgres flexible-server create -g "$RG" -n "$PG" -l "$LOCATION" \
    --tier Burstable --sku-name Standard_B1ms --storage-size 32 --storage-auto-grow Disabled \
    --version 16 --admin-user syadmin --admin-password "$PG_PASS" \
    --public-access 0.0.0.0 --yes -o none   # 0.0.0.0 = allow Azure services only
  az postgres flexible-server db create -g "$RG" -s "$PG" -d switchyard -o none
  NEW_DB_URL="postgresql+psycopg://syadmin:${PG_PASS}@${PG}.postgres.database.azure.com:5432/switchyard?sslmode=require"
elif [[ "$RESET_SECRETS" == "1" ]]; then
  PG_PASS="Sy-$(openssl rand -hex 16)"
  az postgres flexible-server update -g "$RG" -n "$PG" --admin-password "$PG_PASS" -o none
  NEW_DB_URL="postgresql+psycopg://syadmin:${PG_PASS}@${PG}.postgres.database.azure.com:5432/switchyard?sslmode=require"
else
  say "PostgreSQL $PG exists — keeping its password"
  NEW_DB_URL=""
fi

# ---------------------------------------------------------------- Container Apps
if ! az containerapp env show -g "$RG" -n "$ENV_NAME" -o none 2>/dev/null; then
  say "Container Apps environment $ENV_NAME"
  az containerapp env create -g "$RG" -n "$ENV_NAME" -l "$LOCATION" -o none
fi

if ! az containerapp show -g "$RG" -n "$MOCK" -o none 2>/dev/null; then
  say "Demo vendor servers ($MOCK, internal only)"
  az containerapp create -g "$RG" -n "$MOCK" --environment "$ENV_NAME" --image "$IMAGE" \
    --ingress internal --target-port 9100 --cpu 0.25 --memory 0.5Gi --min-replicas 0 --max-replicas 1 \
    --env-vars SWITCHYARD_ROLE=mock -o none
fi

if ! az containerapp show -g "$RG" -n "$APP" -o none 2>/dev/null; then
  # Start with Microsoft's placeholder page so the public address exists (needed for the Entra
  # redirect URI) without ever exposing Switchyard before SSO is configured.
  say "Switchyard app ($APP) — placeholder until configured"
  az containerapp create -g "$RG" -n "$APP" --environment "$ENV_NAME" \
    --image mcr.microsoft.com/k8se/quickstart:latest \
    --ingress external --target-port 80 --cpu 0.5 --memory 1Gi --min-replicas 0 --max-replicas 1 -o none
fi
FQDN=$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)
PUBLIC_URL="https://${FQDN}"
say "Site address: $PUBLIC_URL"

# ---------------------------------------------------------------- Entra ID sign-in app
APP_ROLES=$(cat <<'JSON'
[
 {"allowedMemberTypes":["User"],"displayName":"Switchyard admin","description":"Full control","isEnabled":true,"value":"sy-admins","id":"6f1d2c3a-1b7e-4b8a-9a51-0c1e5a000001"},
 {"allowedMemberTypes":["User"],"displayName":"Switchyard builder","description":"Builds endpoints and workflows","isEnabled":true,"value":"sy-builders","id":"6f1d2c3a-1b7e-4b8a-9a51-0c1e5a000002"},
 {"allowedMemberTypes":["User"],"displayName":"Switchyard operator","description":"Runs and approves workflows","isEnabled":true,"value":"sy-operators","id":"6f1d2c3a-1b7e-4b8a-9a51-0c1e5a000003"},
 {"allowedMemberTypes":["User"],"displayName":"Switchyard viewer","description":"Read-only","isEnabled":true,"value":"sy-viewers","id":"6f1d2c3a-1b7e-4b8a-9a51-0c1e5a000004"},
 {"allowedMemberTypes":["User"],"displayName":"Switchyard auditor","description":"Audit log","isEnabled":true,"value":"sy-auditors","id":"6f1d2c3a-1b7e-4b8a-9a51-0c1e5a000005"}
]
JSON
)
OPTIONAL_CLAIMS='{"idToken":[{"name":"email","essential":false},{"name":"preferred_username","essential":false}]}'

SSO_APP_ID=$(az ad app list --display-name "Switchyard ($APP)" --query "[0].appId" -o tsv)
if [[ -z "$SSO_APP_ID" ]]; then
  say "Entra ID app registration for sign-in"
  echo "$APP_ROLES" > /tmp/sy-roles.json
  echo "$OPTIONAL_CLAIMS" > /tmp/sy-claims.json
  SSO_APP_ID=$(az ad app create --display-name "Switchyard ($APP)" --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$PUBLIC_URL/auth/callback" "$PUBLIC_URL/" \
    --app-roles @/tmp/sy-roles.json --optional-claims @/tmp/sy-claims.json --query appId -o tsv)
  NEW_SSO_SECRET=1
else
  say "Entra app exists ($SSO_APP_ID) — refreshing redirect URIs"
  az ad app update --id "$SSO_APP_ID" --web-redirect-uris "$PUBLIC_URL/auth/callback" "$PUBLIC_URL/"
  NEW_SSO_SECRET=$RESET_SECRETS
fi
SSO_SP_ID=$(az ad sp show --id "$SSO_APP_ID" --query id -o tsv 2>/dev/null || az ad sp create --id "$SSO_APP_ID" --query id -o tsv)
SSO_SECRET=""
if [[ "$NEW_SSO_SECRET" == "1" ]]; then
  SSO_SECRET=$(az ad app credential reset --id "$SSO_APP_ID" --display-name switchyard --years 1 --query password -o tsv)
fi

ME_ID=$(az ad signed-in-user show --query id -o tsv)
if ! az rest --method get --url "https://graph.microsoft.com/v1.0/users/$ME_ID/appRoleAssignments" \
     --query "value[?resourceId=='$SSO_SP_ID']" -o tsv | grep -q .; then
  say "Assigning you the sy-admins app role"
  az rest --method post --url "https://graph.microsoft.com/v1.0/users/$ME_ID/appRoleAssignments" \
    --body "{\"principalId\":\"$ME_ID\",\"resourceId\":\"$SSO_SP_ID\",\"appRoleId\":\"6f1d2c3a-1b7e-4b8a-9a51-0c1e5a000001\"}" -o none
fi

# ---------------------------------------------------------------- configure the app
say "Configuring Switchyard (production mode, Entra sign-in, demo data)"
SECRETS=()
[[ "$NEW_SSO_SECRET" == "1" ]] && SECRETS+=("oidc-secret=$SSO_SECRET")
[[ -n "$NEW_DB_URL" ]] && SECRETS+=("db-url=$NEW_DB_URL")
if ! az containerapp secret show -g "$RG" -n "$APP" --secret-name secret-key -o none 2>/dev/null || [[ "$RESET_SECRETS" == "1" ]]; then
  SECRETS+=("secret-key=$(openssl rand -hex 32)")
fi
if (( ${#SECRETS[@]} )); then
  az containerapp secret set -g "$RG" -n "$APP" --secrets "${SECRETS[@]}" -o none
fi

az containerapp ingress update -g "$RG" -n "$APP" --target-port 8000 -o none
az containerapp update -g "$RG" -n "$APP" --image "$IMAGE" --min-replicas 0 --max-replicas 1 \
  --set-env-vars \
    ENVIRONMENT=production \
    PUBLIC_URL="$PUBLIC_URL" \
    SECRET_KEY=secretref:secret-key \
    DATABASE_URL=secretref:db-url \
    AUTH_MODE=oidc \
    OIDC_PROVIDER_NAME="Microsoft Entra ID" \
    OIDC_DISCOVERY_URL="https://login.microsoftonline.com/$TENANT_ID/v2.0/.well-known/openid-configuration" \
    OIDC_CLIENT_ID="$SSO_APP_ID" \
    OIDC_CLIENT_SECRET=secretref:oidc-secret \
    OIDC_GROUPS_CLAIM=roles \
    ADMIN_EMAILS="$ADMIN_EMAIL" \
    SEED_DEMO=true \
    DEMO_MCP_URL="http://$MOCK" -o none

# ---------------------------------------------------------------- GitHub deployment identity
DEPLOY_APP_ID=$(az ad app list --display-name "Switchyard GitHub deploy" --query "[0].appId" -o tsv)
if [[ -z "$DEPLOY_APP_ID" ]]; then
  say "Deployment identity for GitHub Actions (federated, no secret)"
  DEPLOY_APP_ID=$(az ad app create --display-name "Switchyard GitHub deploy" --query appId -o tsv)
  az ad sp create --id "$DEPLOY_APP_ID" -o none
  az ad app federated-credential create --id "$DEPLOY_APP_ID" --parameters "{
    \"name\": \"github-production\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:${GITHUB_REPO}:environment:production\",
    \"audiences\": [\"api://AzureADTokenExchange\"]}" -o none
  sleep 20  # let the service principal replicate before the role assignment
fi
az role assignment create --assignee "$DEPLOY_APP_ID" --role Contributor \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$RG" -o none 2>/dev/null || true

# ---------------------------------------------------------------- done
cat <<EOF

$(printf '\033[1;32m')Done.$(printf '\033[0m')

  Site:  $PUBLIC_URL
         (first visit after idle takes a few seconds while it wakes up)

Add these as GitHub repository *variables* (Settings → Secrets and variables → Actions → Variables):

  AZURE_CLIENT_ID        $DEPLOY_APP_ID
  AZURE_TENANT_ID        $TENANT_ID
  AZURE_SUBSCRIPTION_ID  $SUB_ID
  AZURE_RESOURCE_GROUP   $RG

After that, every push to main redeploys automatically.

To let someone else in: Entra ID → Enterprise applications → "Switchyard ($APP)" → Users and groups →
Add assignment → pick a person and one of the sy-* roles.
EOF
