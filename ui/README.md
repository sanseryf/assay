# Assay UI source

The browser bundle is built from this directory:

- **CodeMirror 6** for the editor, JSON language support and the lint layer.
- **jsonc-parser** for turning a report path like `members[3].zip` into an offset in
  the document.
- **esbuild** for the bundle. There is no framework and no runtime build step.

The output, `../src/assay/static/app.js`, is committed. `assay serve` and the Docker
image serve it directly; Node is only needed when changing the UI.

```bash
cd ui
npm install
npm run build      # writes ../src/assay/static/app.js
npm run watch      # rebuild on change
```

## Smoke test

`smoke.mjs` drives the real page in Chrome and checks the parts that Python tests
cannot: group clicks, squiggle clicks, expandable records, filters and ticket copy.

```bash
cd ui
npm install
# Assay must be running on http://127.0.0.1:8000
npm run smoke
```

It writes screenshots to `ui/smoke-shots/`. Override `ASSAY_URL`, `CHROME_PATH` or
`ASSAY_SHOT_DIR` if your setup differs. It is a development check, not part of CI.

## Licences

The fonts copied into `static/fonts` are Fraunces, Inter and JetBrains Mono, all
under the SIL Open Font License. Their licence files sit beside the `.woff2` files.
CodeMirror, jsonc-parser and esbuild are MIT licensed.
