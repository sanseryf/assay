# Assay

![CI](https://github.com/sanseryf/assay/actions/workflows/ci.yml/badge.svg)

> **A JSON payload error identifier.** Assay reads one payload or ten thousand, reports
> every error at once, pins each to a path — then groups the findings by root cause, so
> you fix one mapping instead of reading 4,000 identical failures.

**Status: v0.1 — milestones M1–M3 are built and tested.** That is the syntax layer, the
schema layer, and grouping. Layers 3–5 (setup rules, string quality, geographic checks)
and the later interfaces are designed in [`ARCHITECTURE.md`](ARCHITECTURE.md) and marked
**planned** there. This README describes only what runs today.

## The 2,000-payload demo

```text
Assay · 2,000 payloads · 265 clean · 1,735 with errors · 193 ms

 #   IMPACT   LAYER    WHERE                                  PROBLEM                                            RECORDS
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
 1   ██████   schema   members[*].zip                         integer, expected string                             1,660
                                                              e.g. 78701, 98101, 7030                                   
                                                              hint: a number here usually means leading zeros           
                                                              were stripped upstream (07030 became 7030); send          
                                                              the field as a string.                                    
 2   █        schema   members[*].dob                         not a valid date                                       269
                                                              e.g. 03/20/1976, 02/10/1963, 06/09/1980                   
                                                              hint: expected date such as YYYY-MM-DD.                   
 3   █        schema   members[*].coverage_tier               value not in allowed set                                98
                                                              e.g. employee, employee_spouse, employee_child            
                                                              hint: allowed values: EMPLOYEE, EMPLOYEE_SPOUSE,          
                                                              EMPLOYEE_CHILD. Did you mean 'EMPLOYEE'?                  
 4   █        schema   members[*].first_name                  missing required field                                  28
                                                              hint: check the field mapping; a missing field            
                                                              here usually means an empty source column.                
 5   █        schema   members[*].frist_name                  unknown field                                           28
                                                              e.g. Alan, Radia, Sophie                                  
                                                              hint: unknown field 'frist_name'. Did you mean            
                                                              'first_name'?                                             
 6   █        schema   members[*].zip                         missing required field                                  18
                                                              hint: check the field mapping; a missing field            
                                                              here usually means an empty source column.                
 7   █        syntax   examples/demo-batch.ndjson:38:1:209    trailing comma before a closing bracket                  3
                                                              hint: remove the comma at line 1, column 209.             
 8   █        syntax   examples/demo-batch.ndjson:205:1:65    curly quote U+201C (left double quotation mark)          2
                                                              hint: replace “ ” ‘ ’ with straight quotes and            
                                                              re-export as UTF-8; Word, Slack and Notion apply          
                                                              this autocorrect.                                         
 9   █        syntax   examples/demo-batch.ndjson:1002:1:1    byte-order mark at start of file                         1
                                                              hint: strip the BOM; some Windows editors save            
                                                              UTF-8 with one.                                           
10   █        syntax   examples/demo-batch.ndjson:778:1:271   unclosed string                                          1
                                                              hint: opened at line 1, column 271; the payload           
                                                              looks cut off. Brackets are still open too.               

correlated · members[*].first_name and members[*].frist_name co-occur in 100% of affected records; likely one upstream 
cause.

start here · fixing #1 (members[*].zip) clears 2,523 of 2,993 findings (84%).
```

2,993 findings collapse to ten groups, and one fix clears 84% of them. The full run is
committed as [`examples/demo-report.txt`](examples/demo-report.txt); regenerate it with
the commands under [Quick start](#quick-start).

## Why this exists

Integration support work runs on the same loop: a payload fails, the error message is
vague (`400 Bad Request`), and someone reads raw JSON to find out why. One root cause —
a date format, an unmapped field, a quote autocorrected by Word — breaks hundreds of
records at once, but the tooling reports one error at a time.

Assay answers three questions:

1. **Is it even valid JSON?** If not, where, and what probably caused it.
2. **What is wrong with it?** Every violation at once, each with a path and a fix hint.
3. **What is the root cause?** Findings clustered and ranked, with sample values.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev

# The demo batch is generated, not committed: 2,000 payloads, a handful of causes.
uv run python examples/make_demo_batch.py examples/demo-batch.ndjson
uv run assay check examples/demo-batch.ndjson --schema examples/member.schema.json
```

A single payload, a glob, a directory or stdin works the same way:

```bash
uv run assay check payload.json --schema examples/member.schema.json
uv run assay check 'payloads/*.json' --schema examples/member.schema.json
cat payload.json | uv run assay check - --schema examples/member.schema.json
```

Machine-readable for CI:

```bash
uv run assay check examples/demo-batch.ndjson \
  --schema examples/member.schema.json \
  --format json --output report.json
```

### Exit codes

| code | meaning |
| ---- | ------- |
| `0`  | no errors or warnings (informational notes may still be listed) |
| `1`  | findings, all of them warnings |
| `2`  | at least one error found |
| `3`  | Assay could not run: bad schema, unreadable input, invalid command line |

A schema that fails to compile exits `3` rather than reporting a verdict, because
nothing was actually checked. A mistyped command line also exits `3`, so a typo can
never be mistaken for "errors found". Both cases are covered by tests.

## What it checks today

### Layer 1 — syntax

`json.loads` reports where the parser gave up; that is a position, not a diagnosis. Each
detector explains the human cause and points at the character:

| Detector | Example | What the hint says |
| -------- | ------- | ------------------ |
| trailing comma | `{"a": 1,}` | remove the comma at line 1, column 8 |
| single quotes | `{'a': 1}` | JSON requires double quotes |
| smart quotes | `{“a”: 1}` | Word/Slack autocorrect; replace with straight quotes |
| BOM | `﻿{...}` | strip the byte-order mark (the payload is still checked) |
| unescaped newline | `"line one⏎line two"` | use `\n` |
| truncation | `{"a": [1, 2` | unclosed `[` at line 1, column 7; payload looks cut off |
| comments | `// note` | JSON has no comments |
| NaN / Infinity | `{"x": NaN}` | not valid JSON; use `null` or a string |
| encoding | a cp1252 byte | names the byte and offset, suggests re-exporting as UTF-8 |
| mismatched bracket | `{"a": 1]` | `]` where `}` was expected |

Detection is deliberate about false positives: every regex candidate is checked against
the string spans found by a single scanner pass, so a `//` inside a URL or an apostrophe
in `O'Brien` is never blamed. A truncated payload's trailing newline is not reported
separately: the cut is the root cause, and two groups for one defect would be noise.

A syntax failure stops later layers **for that payload only**; the batch keeps going.

### Layer 2 — schema

`Draft202012Validator.iter_errors` yields every violation instead of raising on the
first. Each becomes a finding with the expected shape and the likely fix: `required`,
`type`, `enum`, `const`, `additionalProperties`, `format`, `pattern`,
`minimum`/`maximum`, `minLength`/`maxLength`, `minItems`/`maxItems`, `uniqueItems`,
`dependentRequired`, and `oneOf`/`anyOf` (with the closest variant's reason).

Two details matter:

- **The format checker is explicit.** jsonschema treats `format` as an annotation unless
  you pass one, so `"03/14/1990"` passes a `date` field by default. There is a test that
  proves both halves of that.
- **Hints use the field name.** A number in a field called `zip` gets "leading zeros were
  stripped upstream (07030 became 7030)"; an enum typo gets the closest allowed value; an
  unknown field gets `Did you mean 'first_name'?`.

## Grouping, and why it is the point

Findings are fingerprinted as `layer:rule:normalized-path:signature`:

```
members[0].zip   → schema:type:members[*].zip:type:integer→string
members[847].zip → schema:type:members[*].zip:type:integer→string
```

The fingerprint contains **no values**, which is why 1,660 records collapse into one row
and sample values (`78701`, `98101`, `7030`) can be listed separately. Groups are ranked
by impact (affected records × severity weight), ties broken by layer order, so a syntax
error outranks a schema error of the same size. When two groups always hit the same
records, the report says so — `first_name` missing and `frist_name` unknown are one
copy-paste mistake, not two bugs.

Correlation is Jaccard overlap of the two affected-record sets, computed only for groups
with at least five affected payloads and only among the top 60 groups, because the
pairwise step would otherwise be quadratic in a pathological batch.

## Development

```bash
uv sync --extra dev
uv run pre-commit install      # ruff + mypy before every commit

uv run pytest                  # 112 tests: unit, golden, CLI, property-based
uv run pytest --cov=assay      # coverage
uv run ruff check .
uv run ruff format --check .
uv run mypy                    # strict, configured in pyproject.toml
```

The suite includes Hypothesis properties that matter: any valid JSON produces no syntax
findings, a trailing comma is always found, truncation is found at every prefix length,
and arbitrary bytes never raise. `tests/fixtures/` holds fifteen fixtures — two valid,
five schema-invalid, eight syntactically broken — and `tests/fixtures/expected.json` pins
the whole report. Regenerate it deliberately:

```bash
UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
```

CI (`.github/workflows/ci.yml`) runs ruff, `ruff format --check`, `mypy --strict` and
`pytest` with an 85% coverage gate on Python 3.11 and 3.12.

## Project layout

```
src/assay/
  models.py      Finding, Group, Report; layers and severities
  syntax.py      layer 1: decoding, detectors, one-pass string/bracket scanner
  schema.py      layer 2: jsonschema errors → findings + hints
  fingerprint.py path normalization and the fingerprint itself
  grouping.py    collapse, impact ranking, correlation
  pipeline.py    per-payload layer order, batch assembly, size/depth limits
  loader.py      files, globs, directories, NDJSON, stdin
  report.py      terminal table (Rich) and JSON
  cli.py         Typer app and the exit-code contract
ARCHITECTURE.md  the full design; unbuilt sections are marked planned
examples/        member schema, deterministic 2,000-payload generator, sample report
tests/           unit, golden, CLI, property-based, fixtures
```

## Limitations, stated plainly

- Layers 3–5, `--redact`, the HTTP API, the web UI, HTML/SARIF output and the Switchyard
  stage are **not implemented**. They are milestones M4–M8 in
  [`ARCHITECTURE.md`](ARCHITECTURE.md).
- The full JSON Schema vocabulary is not mapped: keywords outside the list above fall
  back to jsonschema's own message with a generic hint.
- External `$ref` targets are not resolved; internal references (`#/$defs/...`) work.
- Unknown `format` values are accepted (jsonschema's behaviour), not flagged.
- A file is read into memory up to `--max-bytes` (32 MB default); true streaming from
  disk is planned.
- Syntax detection is heuristic by nature. It only runs when parsing has already failed,
  and it never blames text inside a valid string.
- Payloads are not persisted. The report is the only artifact.

## License

MIT. See [LICENSE](LICENSE).
