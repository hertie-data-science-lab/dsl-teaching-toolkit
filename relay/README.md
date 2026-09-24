# Sign-in relay

A Cloudflare Worker (`src/index.ts`) that lets the console sign people in with the lab's
GitHub App. GitHub hands the console a one-time `code`; turning it into a token needs the
App's client secret, and GitHub's token endpoint sends no CORS headers, so a static page
cannot do it. The relay does that one call and nothing else:

- `POST /exchange` `{code, code_verifier}` -> `{access_token, refresh_token, expires_in, refresh_token_expires_in}`
- `POST /refresh` `{refresh_token}` -> the same, with a new pair (the old pair stops working)

It keeps no state, stores nothing and logs nothing; tokens pass through. It answers only the
console's origin (CORS) and at most 20 requests a minute per client address. GitHub's own
refusal (an expired code, a used refresh token) comes back as `400 {error, error_description}`;
a missing or wrong client id or secret comes back as `503 {error: "not_configured"}`, and the
console then says to use a token. A `redirect_uri` sent with `/exchange` must be on the
console's origin and is passed on to GitHub.

An institution may run no relay at all: build the console without `VITE_AUTH_RELAY_URL` and
people sign in with a pasted token instead (see `console/README.md`).

## Settings

| Name | Kind | Where it is set |
|---|---|---|
| `GH_APP_CLIENT_ID` | variable | repository variable `GH_APP_CLIENT_ID`, passed at deploy |
| `CONSOLE_ORIGIN` | variable | repository variable `CONSOLE_ORIGIN`, e.g. `https://hertie-data-science-lab.github.io`; comma-separate several (a local `http://localhost:5173` while testing) |
| `GH_APP_CLIENT_SECRET` | Worker secret | secret `GH_APP_CLIENT_SECRET` in the `relay` environment, pushed as a Worker secret at deploy; never in the repo |
| `LIMITER` | rate-limit binding | `wrangler.toml` |

## Deploy

`.github/workflows/relay-deploy.yml` deploys on a push to `main` that changes `relay/`, or by
hand from `main` (Actions > relay-deploy > Run workflow); on any other ref it skips, as it
does until the `GH_APP_CLIENT_ID` variable is set. It runs in the GitHub environment `relay`.
In the toolkit repo's settings:

- create the environment `relay` (Settings > Environments), limit its deployment branches to
  `main`, and put the two secrets there: `CLOUDFLARE_API_TOKEN` (a token from the "Edit
  Cloudflare Workers" template) and `GH_APP_CLIENT_SECRET`;
- secret `CLOUDFLARE_ACCOUNT_ID` (not sensitive; the environment or the repo);
- repository variables `GH_APP_CLIENT_ID`, `CONSOLE_ORIGIN`.

The Worker answers at `https://dsl-console-auth.<account subdomain>.workers.dev`; build the
console with that as `VITE_AUTH_RELAY_URL`.

## Develop

    cd relay
    npm ci
    npm test           # vitest, GitHub stubbed; no network
    npm run typecheck

## What the origin check does not cover

The console is served from `https://hertie-data-science-lab.github.io`, an origin every
GitHub Pages site in that organisation shares (project sites are paths on it). So the
relay's CORS check admits any of those sites, and the console's `sessionStorage` (the
access and refresh tokens) is readable by script on any of them opened in the same tab, a
link followed from the console for one. The console's CSP (D2)
limits script injected into the console itself; it does nothing about a sibling Pages site
in the same organisation. Keep that organisation's Pages sites to repos the maintainers
control, or serve the console from its own origin.
