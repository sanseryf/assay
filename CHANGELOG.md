# Changelog

All notable changes to Assay are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.4.0] - 2026-11-26

### Added

- **`assay ticket`.** Build a ticket for one report group from the CLI:
  - Markdown, Jira wiki markup, ADF or a JSON ticket model
  - one failing record as a minimal repro, expected value from the schema, actual value
    as a shape, affected count, fix hint and the raw fingerprint
  - redacted by default; `--no-redact` is explicit
  - `--group` by number or fingerprint, and `--output` for a file
- **Jira Cloud create/update.** `--push` searches for the ticket's `assay-...` label and
  comments on the existing issue, or creates one with the label and an ADF description.
  Credentials come from `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` and `JIRA_PROJECT`, or
  the matching options. The token is never written to disk and is scrubbed from errors.
- Optional `jira` extra containing `httpx`; the rest of the tool does not need it.
- Unit tests for path parsing, minimal repros, redaction, all renderers, the Jira client
  (creation, deduplication, error scrubbing) and the CLI.

## [1.3.0] - 2026-11-26

### Added

- **CodeMirror 6 editor.** JSON highlighting, line numbers, a lint gutter, squiggles and
  hover tooltips. The bundle lives in `static/app.js`; the source and esbuild build are in
  `ui/`. Node is only needed when changing the UI.
- **Two-way navigation.** `jsonc-parser` maps a report path such as `members[3].zip` to a
  document range. Clicking a group, path or affected record selects and scrolls to that
  range and highlights every instance of the group. Clicking a squiggle opens the group.
- **Expandable group cards.** The API now returns up to 50 occurrences per group with the
  payload id, concrete path, line and column, and a value preview. The card shows them on
  demand.
- **Layer and severity filters**, with a visible count and an empty state.
- **Drag and drop** for payloads, schema and rules files.
- **HTML export from the page** through `POST /assay/export/html`, alongside the existing
  JSON download.
- **Redacted tickets** from each group in Markdown, Jira wiki markup or Atlassian
  Document Format. They include a minimal repro, expected and actual values, the fix,
  the fingerprint and a stable `assay-...` label for deduplication.
- A favicon, and a `ui/smoke.mjs` browser check for the interactions Python tests cannot
  cover.

### Changed

- `/assay` and `/assay/batch` now include `occurrences` in the response. The CLI JSON
  output is unchanged; the field is opt-in in `Report.to_dict`.
- The payload textarea is gone; the editor is the input.

## [1.2.0] - 2026-11-26

### Changed

- **New visual system.** The UI now matches the rest of the tooling: near-black forest
  night and warm parchment day, bronze as the only functional accent, a green aurora
  atmosphere, and serif display type. Tokens cover both schemes, so no component needs a
  second rule set.
- Masthead rebuilt with a monogram, an eyebrow label and a sun/moon toggle. The choice is
  applied before paint by an inline script, persisted in `localStorage`, and reflected in
  `<meta name="theme-color">`.
- Panels, stat cards, group cards, chips, inputs and buttons restyled. Report groups rise
  in with a short stagger; `prefers-reduced-motion` disables it. The input column sticks
  on desktop so the report can scroll on its own.
- The status line now reads `<1 ms` instead of `0 ms` for fast runs.

### Added

- Self-hosted variable fonts: Fraunces, Inter and JetBrains Mono, with their SIL Open
  Font License files, in `static/fonts`. No external asset or CDN.
- A `?theme=day` preview parameter and a `?still=1` capture mode that disables motion.

## [1.1.0] - 2026-11-26

### Added

- **Web UI.** `assay serve` now opens a real application instead of a paste box:
  - payload textarea and file upload for JSON or NDJSON, schema input and upload, and
    optional rules YAML
  - redaction toggle, Ctrl+Enter to run, sample payload with planted defects
  - report view with summary counts, the highest-impact fix, one card per group with
    path, hint and sample values, correlation notes, and a JSON download
  - responsive dark and light themes, no build step and no external assets
- **`POST /assay/batch`** for the UI: up to 2,000 payloads and 10 MB per request.
- **`rules_yaml`** on both API endpoints, so a browser can send the rules file as text.
  It is validated with the same Pydantic models as the CLI.
- Jinja2 templates and static assets ship inside the package and the wheel.

### Changed

- The old inline paste page is gone; the route now renders `templates/index.html`.
- The API extra now includes Jinja2.

## [Unreleased]

### Changed

- **M8 is built in the separate Switchyard project (v0.1.0).** Assay remains the
  validation service; Switchyard verifies GitHub signatures, dedupes delivery ids,
  routes and retries, and stores Assay's grouped report on every dead letter.

## [1.0.0] - 2026-11-24

Milestones M6 and M7: opt-in USPS verification, and every interface the design called
for. Assay is feature-complete against the architecture document; the Switchyard stage
(M8) is a separate project.

### Added

- **USPS Addresses API v3 (M6).** `--verify` (or `geo.verify.enabled`) asks USPS whether
  an address is real:
  - OAuth client-credentials token, from `USPS_CLIENT_ID` / `USPS_CLIENT_SECRET` in the
    environment only; missing credentials exit `3` with the variable names
  - every result, including "no match", cached by normalized-address hash
    (`~/.cache/assay/usps-verification.sqlite`, TTL configurable, `ASSAY_CACHE_DIR`
    overrides the location)
  - requests throttled to the configured rate
  - findings for a standardized address (warning naming the USPS form), a non-match or
    DPV failure (error), and an API failure (warning, never a crash)
  - tests use mocked USPS responses; CI never calls the network
- **Redaction (M7).** `--redact` replaces values with shape tokens in previews and
  samples and scrubs them from hints; hints derived from a value (repaired mojibake, a
  standardized address) are removed instead of half-redacted. Fingerprints never held
  values, so grouping is unchanged.
- **HTML and SARIF output (M7).** `--format html` writes a standalone escaped page;
  `--format sarif` writes SARIF 2.1.0 with one result per group, for GitHub code scanning.
- **HTTP API and paste-in page (M7).** Optional `api` extra: `assay serve` runs FastAPI
  with `POST /assay` (`payload`, `schema`, optional `rules`, `redact`), `GET /` for a
  no-build web page, and `GET /health`. Stateless; nothing is written.
- **Docker (M7).** A `python:3.12-slim` image with the CLI on the entry point, plus a CI
  job that builds it and smoke-tests `--version`.
- **Version 1.0.0.** README, architecture document and this changelog now describe the
  completed surface; only Switchyard (M8) remains planned.

### Quality

- 259 tests, 91% coverage, `ruff` and `mypy --strict` clean.
- CI: ruff, format check, mypy, pytest with the coverage gate on 3.11 and 3.12, then the
  Docker build.

## [0.3.0] - 2026-11-24

Milestone M5: offline geography. The layer that catches addresses which are valid JSON,
match the schema, and still cannot be delivered.

### Added

- **Layer 5: geographic (offline).** A ~5.6 MB SQLite reference database ships inside
  the package (41,128 ZIPs, 40,981 city names, 54,251 ZIP-county rows), built from
  GeoNames postal codes (CC BY 4.0) and the Census Bureau's ZCTA-to-county relationship
  file (public domain). No address leaves the machine and no API key is needed.
  - ZIP format, four-digit ZIPs with the lost leading zero, unknown ZIPs
  - ZIP ↔ state, ZIP ↔ city (with the USPS preferred name and acceptable variants),
    ZIP ↔ county (with candidate counties and their area ratios)
  - two-letter state codes with suggestions, military APO/FPO/DPO ZIPs by range
  - PO boxes in residential fields, missing street numbers, units in line 1, line-length
    limits, city/state swaps, parsed with `usaddress`
  - batch smells: ZIP-state mismatches that all share one state, home addresses equal to
    the employer's, household members with different addresses
- **Builder.** `python -m assay.geo.build --geonames US.txt --census <file>` rebuilds the
  database, writes attribution and build metadata into it, and prints row counts. The
  wheel includes the database; a missing file fails with exit `3` and the rebuild command.
- **Configuration.** A `geo:` section declares address paths (`residential`, `line1_max`,
  `household_key`) and an optional `employer_address`; with no addresses configured the
  layer does nothing.
- **Demo.** `examples/geo-demo.ndjson` with twelve deliberately wrong addresses; eleven
  groups in `examples/geo-demo-report.txt`.
- **Attribution.** [`NOTICE.md`](NOTICE.md) records the licences, the rebuild command and
  what the data cannot do (PO-box-only and unique ZIP types, street-level validity).

### Quality

- 227 tests: builder from fixture files, every geographic check, config validation, and a
  pipeline run through a fixture database. 90% coverage.
- CI green on Python 3.11 and 3.12.

### Not yet

Online address verification (M6, needs USPS developer credentials), `--redact`,
HTML/SARIF output, the FastAPI endpoint and web UI, Docker packaging, and the Switchyard
validation stage. See the milestone table in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## [0.2.0] - 2026-11-24

Milestone M4: setup rules and string quality. The two layers that catch payloads which
are valid JSON, match the schema, and are still wrong.

### Added

- **Layer 3: setup rules.** YAML rules validated with Pydantic at load time, where
  unknown keys, malformed paths, duplicate ids and out-of-range thresholds all exit `3`
  instead of silently never firing:
  - `compare` (cross-field ordering), `required_if` (conditional requirements) and
    `placeholder` (TEST, N/A, 0000000000) per payload;
  - `constant_smell` (a field with one value on ≥95% of records), `case_drift`,
    `duplicate_records` (natural key) and `type_inconsistency` across the batch.
  - Duplicate keys are found while parsing, because `json` keeps the last value without
    complaint; they are reported as setup findings.
  - Batch collectors observe payloads as they stream and cap their observations, so a
    pathological batch loses a finding rather than the process.
- **Layer 4: string quality.** Mojibake (with the `ftfy` repair shown), zero-width
  characters, non-breaking spaces, control characters, leading/trailing whitespace,
  double-encoded JSON, U+FFFD, mixed-script words, and configurable length limits.
  Findings spell out the codepoint: `zero-width character U+200B`.
- **Rule paths.** A deliberately small JSONPath subset (`$.members[*].dob`,
  `$.members[0].dob`) with tests for the paths rules are allowed to use.
- **CLI.** `--rules/-r` for setup and string configuration; `assay check` now runs four
  layers. The 0/1/2/3 exit-code contract is unchanged, and `1` is finally reachable:
  warning-only batches (string damage, configuration smells) fail no harder than that.
- **Demo.** The 2,000-payload generator now also plants string and setup defects
  (constant source tag, case drift, duplicate keys and records, placeholders, zero-width
  characters, mojibake, whitespace). The committed report shows 9,270 findings collapsing
  to 19 groups across all four layers.

### Changed

- Correlation now requires 90% overlap (was 80%) and never compares two groups on the
  same path: problems in one field co-occur by construction, so those notes were noise.

### Quality

- 193 tests: the M1–M3 suite plus rule-file validation, every rule kind, every string
  check, duplicate-key parsing and end-to-end pipeline coverage.
- 91% line coverage, `ruff` and `mypy --strict` clean, golden snapshot unchanged.

### Not yet

Geographic checks (M5 offline, M6 USPS online), `--redact`, HTML/SARIF output, the
FastAPI endpoint and web UI, Docker packaging, and the Switchyard validation stage. See
the milestone table in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## [0.1.0] - 2026-11-24

First release. Milestones M1–M3: syntax, schema, and grouping.

### Added

- **Layer 1: syntax.** `json.loads` failures become findings with a line, a column and a
  plain-English cause: trailing comma, single quotes, smart quotes, BOM, unescaped
  newline, truncation (bracket-stack scan), comments, NaN/Infinity. Plus non-UTF-8 input,
  which names the offending byte and its cp1252 reading. A BOM is recoverable, so later
  layers still run.
- **Layer 2: schema.** Every `Draft202012Validator.iter_errors` violation at once, each
  with a JSON path and a fix hint, `FormatChecker` enabled explicitly, and `difflib`
  suggestions for enum values and unknown field names.
- **Grouping.** Fingerprints of `layer:rule:normalized-path:signature` with no values in
  them, impact ranking, sample values, and Jaccard correlation between groups that hit
  the same payloads.
- **CLI.** `assay check` over files, globs, directories, NDJSON and stdin; terminal and
  JSON reports; `--output`, `--quiet`, `--max-bytes`, `--max-depth`; exit codes 0/1/2/3
  where 3 always means Assay could not run.
- **Terminal report.** Rich table ranked by impact with hint lines and sample values, a
  correlation note, and a "start here" line naming the fix that clears the most findings.
- **Demo.** A deterministic generator for a 2,000-payload batch whose findings collapse
  to a handful of groups.
- **Guards.** Input size and nesting-depth limits, including a `RecursionError` path for
  payloads too deep for the parser.

### Quality

- 112 tests: one per detector, per schema error type, grouping and correlation, CLI exit
  codes and formats, a fifteen-fixture golden report, and Hypothesis properties (valid
  JSON never warns, trailing commas and every truncation prefix are always found,
  arbitrary bytes never raise).
- `ruff` and `mypy --strict` clean, `pre-commit` hooks, coverage gate at 85% in CI.

### Not yet

Layers 3–5 (setup rules, string quality, geographic checks), `--redact`, HTML/SARIF
output, the FastAPI endpoint and web UI, Docker packaging, and the Switchyard validation
stage. See the milestone table in [`ARCHITECTURE.md`](ARCHITECTURE.md).

[0.2.0]: https://github.com/sanseryf/assay/releases/tag/v0.2.0
[0.1.0]: https://github.com/sanseryf/assay/releases/tag/v0.1.0
