# Permit Atlas - Cloudflare Pages deploy

Static deploy of the Permit Atlas dashboard (`tools/dashboard/dashboard.html`).

```
permit-atlas/
  index.html   # the dashboard (copy of tools/dashboard/dashboard.html)
  _worker.js   # optional HTTP Basic Auth gate (advanced mode, opt-in)
```

## Deploy (dashboard, GUI only)

Cloudflare dashboard -> Workers & Pages -> Create -> Pages -> Upload assets ->
drop this whole folder -> Deploy. You get `https://<project>.pages.dev`.

> `_worker.js` (advanced mode) runs on Direct Upload. A `functions/` directory does
> NOT - the dashboard uploads it as a static file without compiling it; only Wrangler
> or a Git-connected build compiles `functions/`. That is why this project uses
> `_worker.js`.

Wrangler also works if you prefer the CLI:

```bash
wrangler pages deploy ./permit-atlas --project-name permit-atlas
```

## Password protection - pick one

**A. Cloudflare Access (no code).** Leave `AUTH_USER` / `AUTH_PASS` unset so
`_worker.js` stays inert, then Zero Trust -> Access -> Applications -> add a
self-hosted app for the `pages.dev` hostname with an Allow policy.

**B. One shared password (HTTP Basic Auth).** Project -> Settings ->
Variables and Secrets -> add `AUTH_USER` (plaintext) and `AUTH_PASS` (Encrypt) on
Production, then redeploy. The browser then prompts for the shared username/password.
Variables bind at deploy time, so always redeploy (or Retry deployment) after
changing them.

## Updating

After rebuilding the dashboard (`build_data.py` -> `build_html.py`):

```bash
cp tools/dashboard/dashboard.html permit-atlas/index.html
```

then re-upload the folder (or `wrangler pages deploy ./permit-atlas`). The URL and
password settings persist across deploys.
