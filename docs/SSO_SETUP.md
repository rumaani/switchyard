# Connecting a client's SSO

Switchyard speaks OpenID Connect (authorization code + PKCE). You need four values from the client's identity team:

| Setting | Meaning |
|---|---|
| `OIDC_DISCOVERY_URL` | The provider's `.well-known/openid-configuration` URL |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | The app registration for Switchyard |
| `OIDC_GROUPS_CLAIM` | Which token claim carries group membership (`groups` or `roles`) |

Always set `AUTH_MODE=oidc`, `PUBLIC_URL=https://<your host>` and give them the **redirect URI**
`https://<your host>/auth/callback` and **post-logout URI** `https://<your host>`.

Then, in Switchyard → **Access → Group → role**, map the group values the token carries to roles. Put your own
email in `ADMIN_EMAILS` for the first sign-in so you can create those mappings.

## Microsoft Entra ID

1. *App registrations → New registration.* Redirect URI (Web): `https://<host>/auth/callback`.
2. *Certificates & secrets →* new client secret → `OIDC_CLIENT_SECRET`.
3. Pick one way to send roles:
   - **App roles (recommended).** *App roles →* create `Switchyard.Admin`, `Switchyard.Operator`, … Then
     *Enterprise applications → Users and groups →* assign groups to those app roles.
     Set `OIDC_GROUPS_CLAIM=roles` and map `Switchyard.Operator` → `operator` in Switchyard.
     Avoids the 200-group token limit and keeps assignment in the client's hands.
   - **Group claims.** *Token configuration → Add groups claim → Security groups*, emitted as Group ID.
     Set `OIDC_GROUPS_CLAIM=groups` and map the group **object IDs**. Users in more than 200 groups hit the
     “overage” limit — use app roles or “Groups assigned to the application” instead.
4. `OIDC_DISCOVERY_URL=https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration`
5. Scopes stay `openid email profile`. If `email` is empty for guest users, Switchyard falls back to
   `preferred_username`/`upn`.

## Okta

1. *Applications → Create App Integration → OIDC → Web Application.* Sign-in redirect `https://<host>/auth/callback`,
   sign-out redirect `https://<host>`.
2. *Sign On → OpenID Connect ID Token → Groups claim:* name `groups`, filter e.g. *Starts with* `SY-`.
3. `OIDC_DISCOVERY_URL=https://<org>.okta.com/.well-known/openid-configuration`
   (or your custom authorization server's URL), `OIDC_GROUPS_CLAIM=groups`. Map group **names**.

## Ping, Auth0, Google Workspace, others

Any certified OIDC provider works. Make sure the id_token (or userinfo) includes a groups-like claim and set
`OIDC_GROUPS_CLAIM` to its name.

## SAML-only identity providers (ADFS, older Ping, Shibboleth)

Put **Keycloak** (or the client's Entra/Okta tenant) in front as a broker: the IdP federates to Keycloak over SAML,
and Switchyard talks OIDC to Keycloak. In Keycloak: create a realm, add the SAML IdP under *Identity providers*, add a
*Group Membership* mapper (claim `groups`, full path off) to the Switchyard client, and point
`OIDC_DISCOVERY_URL` at `https://<keycloak>/realms/<realm>/.well-known/openid-configuration`.

## Troubleshooting

- **Signed in, but “no role yet”.** The page lists the groups Switchyard received — map one of those exact values.
- **`login_error=mismatching_state`.** The browser blocked the session cookie (third-party context, or `PUBLIC_URL`
  host differs from the address in the browser). Use the same host for both.
- **Issuer mismatch.** The `iss` in the token must equal the discovery document's `issuer`; don't mix
  `login.microsoftonline.com/<tenant>/v2.0` and `/common`.
- Every sign-in attempt, including failures, is in the audit log under `auth.login`.
