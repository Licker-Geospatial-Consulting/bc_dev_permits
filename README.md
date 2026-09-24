# gh-pages — Victoria QA dashboard (prototype)

This orphan branch hosts the static QA dashboard on GitHub Pages. It is a **snapshot**,
not the source of truth.

- `index.html` — loader (React + Babel from CDN, relative paths for the project path)
- `viewer.jsx` — copied from `tools/qa/viewer.jsx` on the code branch
- `victoria.json` — a copy of a `data/processed/*.json` export
- `.nojekyll` — serve files as-is (no Jekyll processing)

## Update the published prototype

From the code branch, regenerate the three files and commit them here:

```bash
git worktree add ../ghpages gh-pages          # once
cp tools/qa/viewer.jsx ../ghpages/viewer.jsx
cp data/processed/victoria_remote_machine.json ../ghpages/victoria.json
git -C ../ghpages commit -am "Update QA dashboard snapshot"
git -C ../ghpages push
```

Live URL: `https://licker-geospatial-consulting.github.io/bc_dev_permits/`
(enable in repo Settings → Pages → Source: `gh-pages` / `root`).
