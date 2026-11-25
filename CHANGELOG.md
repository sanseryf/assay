# Changelog

All notable changes to Assay are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-11-24

First release. Milestones M1–M3: syntax, schema, and grouping.

### Added

- **Layer 1 — syntax.** `json.loads` failures become findings with a line, a column and a
  plain-English cause: trailing comma, single quotes, smart quotes, BOM, unescaped
  newline, truncation (bracket-stack scan), comments, NaN/Infinity. Plus non-UTF-8 input,
  which names the offending byte and its cp1252 reading. A BOM is recoverable, so later
  layers still run.
- **Layer 2 — schema.** Every `Draft202012Validator.iter_errors` violation at once, each
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
- **Demo.** A deterministic generator for a 2,000-payload batch in which 2,993 findings
  collapse to 10 groups and one fix clears 84%.
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

[0.1.0]: https://github.com/sanseryf/assay/releases/tag/v0.1.0
