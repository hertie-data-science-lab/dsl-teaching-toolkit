# Sign-in relay

A Cloudflare Worker (`src/index.ts`) that lets the console sign people in with the lab's
GitHub App. GitHub hands the console a one-time `code`; turning it into a token needs the
App's client secret, and GitHub's token endpoint sends no CORS headers, so a static page
cannot do it. The relay does that one call and nothing else:

- `POST /exchange` `{code, code_verifier}` -> `{access_token, refresh_token, expires_in, refresh_token_expires_in}`
- `POST /refresh` `{refresh_token}` -> the same, with a new pair (the old pair stops working)

It keeps no state, stores nothing and logs nothing; tokens pass through. It answers only the
console's origin (CORS) and at most 20 requests a minute per client address. GitHub's own
refusal (an expired code, a used refresh token) comes back as `400 {error, error_description}`.

An institution may run no relay at all: build the console without `VITE_AUTH_RELAY_URL` and
people sign in with a pasted token instead (see `console/README.md`).

## Settings

| Name | Kind | Where it is set |
|---|---|---|
| `GH_APP_CLIENT_ID` | variable | repository variable `GH_APP_CLIENT_ID`, passed at deploy |
| `CONSOLE_ORIGIN` | variable | repository variable `CONSOLE_ORIGIN`, e.g. `https://hertie-data-science-lab.github.io`; comma-separate several (a local `http://localhost:5173` while testing) |
| `GH_APP_CLIENT_SECRET` | Worker secret | repository secret `GH_APP_CLIENT_SECRET`, pushed as a Worker secret at deploy; never in the repo |
| `LIMITER` | rate-limit binding | `wrangler.toml` |

## Deploy

`.github/workflows/relay-deploy.yml` deploys on a push to `main` that changes `relay/`, or by
hand (Actions > relay-deploy > Run workflow). It skips until the `GH_APP_CLIENT_ID` variable
is set. It needs these in the toolkit repo's settings:

- secrets `CLOUDFLARE_API_TOKEN` (a token with the "Edit Cloudflare Workers" template),
  `CLOUDFLARE_ACCOUNT_ID`, `GH_APP_CLIENT_SECRET`;
- variables `GH_APP_CLIENT_ID`, `CONSOLE_ORIGIN`.

The Worker answers at `https://dsl-console-auth.<account subdomain>.workers.dev`; build the
console with that as `VITE_AUTH_RELAY_URL`.

## Develop

    cd relay
    npm ci
    npm test           # vitest, GitHub stubbed; no network
    npm run typecheck
