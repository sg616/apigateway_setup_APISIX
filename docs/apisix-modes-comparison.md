# APISIX: without etcd vs with etcd (and Keycloak)

APISIX can get its configuration in two ways:

- **Without etcd (current setup).** `config_provider: yaml`. The ingress controller reads the CRDs in `routes/` (git) and pushes the full config to every APISIX pod every 10s. See [`helm/values-sky47.yaml`](../helm/values-sky47.yaml) (lines 2-4, 67-69).
- **With etcd.** APISIX stores its config in an etcd cluster, and every pod watches it. The built-in dashboard (`/ui/` on the Admin port 9180, APISIX 3.13+) needs this mode. Without etcd its API calls return 404.

## 1. Comparing the two setups

| | **Without etcd (current, config from git)** | **With etcd (dashboard)** |
|---|---|---|
| **Where config lives** | CRDs in git. The controller pushes the whole config to each pod every 10s. | etcd. Pods watch it and apply changes as they happen. |
| **What you run** | APISIX and the ingress controller. | Both of those plus an etcd cluster: 3 nodes for redundancy, disks, backups, TLS, auth, and monitoring of etcd's own data and disk use. |
| **How you change something** | Commit a change, or `kubectl apply`. A hand edit in the Admin API is undone within 10s. | Through the dashboard or Admin API. Live straight away. |
| **Dashboard `/ui/`** | Doesn't work (404). Use Grafana for traffic, and the read-only Admin API (`viewer-key`) to see the config. | Works: browse and edit routes, upstreams, consumers, plugins and certificates. |
| **History and review** | Every change is a reviewed git commit, and `git revert` rolls it back. | Only if you build it yourself. Edits made in the UI leave no history and drift from git. |
| **Using both git and the UI** | Not applicable. | Risky. The controller overwrites UI edits to anything it manages, so each resource has to belong to either the UI or git. |
| **If a component fails** | Controller down: running pods keep their last config, but new pods stay not-ready until it's back. | etcd loses its majority of nodes: running pods keep serving cached config, but no changes go through and new pods can't load their config. etcd becomes a critical system you have to look after. |
| **Security** | Nothing a person can edit is exposed. The admin key is used only by the controller. | The dashboard's admin key can rewrite production routing. It has to stay behind a port-forward or an IP allowlist. |
| **Backup and restore** | Git is the backup. A rebuild just syncs from git. | You need regular etcd snapshots and practised restores. |
| **Scale** | Each push sends the whole config. Fine up to hundreds of routes. | Pushes only what changed. Better for thousands of routes or very frequent changes. |
| **Day-to-day effort** | Low. | Noticeably higher, mostly etcd upkeep. |

## 2. With Keycloak authentication added

### Keycloak plugins at the gateway (the same in both setups)

| Need | APISIX plugin | Notes |
|---|---|---|
| Check tokens on API calls (mobile app, front end, partners) | `openid-connect` set to only accept tokens (`bearer_only: true`, `use_jwks: true`) | APISIX checks the token's signature against Keycloak's public keys and caches them, so it doesn't call Keycloak on every request. New Keycloak signing keys are picked up automatically. |
| Browser login (redirect to Keycloak, then a session cookie) | `openid-connect` with session cookies | Every APISIX pod must use the **same** cookie secret (`session.secret`), or logins break when requests switch between replicas. |
| Fine-grained permissions (Keycloak decides who may call each path) | `authz-keycloak` | Calls Keycloak on each request, so latency depends on Keycloak. |
| Pass user info to the backends | Headers the plugin adds (`X-Userinfo`, `X-Access-Token`) | Remove any copies the client sends in with `proxy-rewrite`, or users can fake their identity. |
| Rate limits per user or client | `limit-count` keyed on the user ID from the token | Needs Redis to count across all pods. That's true in both setups. |
| Protect the Keycloak admin console | `ip-restriction` on `/admin` of the Keycloak route | The same allowlist pattern used for Grafana. |

### Each setup with Keycloak

| | **Without etcd (current)** | **With etcd + dashboard** |
|---|---|---|
| **Where auth rules live** | Plugin blocks in `ApisixRoute` files in git, reviewed in a PR. An `ApisixGlobalRule` can require a token on every route by default. | Set in the UI. Fast, but nothing reviews a change to who can access what. |
| **Keycloak client secret and cookie secret** | Stay in a Kubernetes Secret that the route file points to (`secretRef`), never in git. That's the same approach as the admin key. *Check that controller 2.2 supports this before using it.* | Stored in etcd, unencrypted unless you turn on APISIX's field encryption (`data_encryption`) and manage its key. Anyone with the dashboard's admin key can read them. |
| **Mistake in an auth rule** | Caught in review or on the beta cluster first. Rollback is `git revert`. | Goes live immediately. One wrong click can lock out every user, or open a route to everyone, with no record of who changed what. |
| **Proving who changed access** (audit, PCI-style controls) | Git history: who, when, why, and who approved it. | Only APISIX's own logs. You'd have to build an audit trail. |
| **API clients registered in APISIX itself** (for example partner API keys) | `ApisixConsumer` files with key Secrets. Adding a partner means a PR. | Created in the UI or by API, so onboarding can be self-service. **This is the main case where etcd helps.** |
| **Systems that store data** | Keycloak and its Postgres. | Keycloak, Postgres **and** etcd: two databases that both sit on the login path. |
| **If something fails** | Keycloak down: token checks keep working from the cached keys, but no new logins or token refreshes. Controller down: the gateway keeps serving. | The same Keycloak risk, plus etcd losing its majority of nodes, which freezes config and stops new pods from starting. |
| **Speed of change** | About 10s after the change is applied, plus the time for the PR. | Instant. |
| **Learning and experimenting** | Slower: edit YAML, apply, test. | Easier: forms for the plugin settings. |

### Summary

For a payments gateway with Keycloak, **stay without etcd**. Every change to who can access what goes through review and git history. The Keycloak secrets stay in Kubernetes Secrets. You don't add a second database to the login path. All the Keycloak plugins work the same way in both setups.

Consider etcd only if APISIX itself needs self-service creation of API clients (consumers). Even then, partner apps could be registered as Keycloak clients instead, which keeps APISIX without etcd.
