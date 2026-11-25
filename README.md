# Assay

![CI](https://github.com/sanseryf/assay/actions/workflows/ci.yml/badge.svg)

> **A JSON payload error identifier.** Assay reads one payload or ten thousand, reports
> every error at once, pins each to a path — then groups the findings by root cause, so
> you fix one mapping instead of reading 4,000 identical failures.

**Status: v1.0 — milestones M1–M7 are built and tested.** Syntax, schema, setup rules,
string quality, offline geography, USPS verification (opt-in), redaction, HTML/SARIF
reports, an HTTP API with a paste-in page, and a Docker image. The Switchyard validation
stage is designed in [`ARCHITECTURE.md`](ARCHITECTURE.md) and marked **planned**; this
README describes only what runs today.

## The 2,000-payload demo

```text
Assay · 2,000 payloads · 285 clean · 1,715 with errors · 537 ms

 #   IMPACT   LAYER    WHERE                                  PROBLEM                                            RECORDS
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
 1   ██████   schema   members[*].zip                         integer, expected string                             1,653
                                                              e.g. 75071, 7030, 98101                                   
                                                              hint: a number here usually means leading zeros           
                                                              were stripped upstream (07030 became 7030); send          
                                                              the field as a string.                                    
 2   ███      setup    members[*].source_system               100% of records share one value                      1,994
                                                              e.g. LEGACY_HR                                            
                                                              hint: A single source tag on every record is              
                                                              usually a hard-coded default.                             
 3   ██       setup    members[*].zip                         integer here, string elsewhere in the batch          1,653
                                                              e.g. 75071, 7030, 98101                                   
                                                              hint: ZIPs are strings in some records and                
                                                              numbers in others; the leading zero is being              
                                                              lost.                                                     
 4   █        schema   members[*].dob                         not a valid date                                       271
                                                              e.g. 03/20/1976, 05/19/1997, 10/14/1965                   
                                                              hint: expected date such as YYYY-MM-DD.                   
 5   █        schema   members[*].coverage_tier               value not in allowed set                                94
                                                              e.g. employee, employee_spouse, employee_child            
                                                              hint: allowed values: EMPLOYEE, EMPLOYEE_SPOUSE,          
                                                              EMPLOYEE_CHILD. Did you mean 'EMPLOYEE'?                  
 6   █        string   members[*].first_name                  zero-width character U+200B                            110
                                                              e.g. José​, Müller​, Barbara​                                
                                                              hint: strip it; it is invisible and breaks                
                                                              string equality, joins and dedupe.                        
 7   █        setup    members[*].coverage_tier               case differs from the rest of the batch                 94
                                                              e.g. employee_spouse, employee_child, employee            
                                                              hint: the batch predominantly uses                        
                                                              'EMPLOYEE_SPOUSE'; make the field consistent.             
 8   █        schema   members[*].first_name                  missing required field                                  34
                                                              hint: check the field mapping; a missing field            
                                                              here usually means an empty source column.                
 9   █        schema   members[*].frist_name                  unknown field                                           34
                                                              e.g. Müller, Katherine, Renée                             
                                                              hint: unknown field 'frist_name'. Did you mean            
                                                              'first_name'?                                             
10   █        string   members[*].last_name                   leading or trailing whitespace                          65
                                                              e.g.  Lovelace,  Allen,  Liskov                           
                                                              hint: trim the value; whitespace breaks                   
                                                              matching, joins and dedupe.                               
11   █        schema   members[*].zip                         missing required field                                  12
                                                              hint: check the field mapping; a missing field            
                                                              here usually means an empty source column.                
12   █        setup    members[*].external_id                 duplicate key in this batch                              6
                                                              e.g. E-001108, E-001554, E-002048                         
                                                              hint: The same external id arrived twice; dedupe          
                                                              or make the write an upsert.                              
13   █        string   members[*].first_name                  mojibake (UTF-8 decoded as cp1252)                      13
                                                              e.g. RenÃ©e, SÃ¸ren, JosÃ©                                
                                                              hint: reads as 'Renée'; re-export the source as           
                                                              UTF-8 (ftfy repairs this).                                
14   █        setup    batch_id                               duplicate key                                            4
                                                              hint: the later value silently wins; remove or            
                                                              rename the repeated key.                                  
15   █        setup    members[*].first_name                  placeholder value                                        9
                                                              e.g. TEST                                                 
                                                              hint: TEST or N/A in a name field means the               
                                                              mapping fell back to a placeholder.                       
16   █        syntax   examples/demo-batch.ndjson:38:1:231    trailing comma before a closing bracket                  3
                                                              hint: remove the comma at line 1, column 231.             
17   █        syntax   examples/demo-batch.ndjson:205:1:65    curly quote U+201C (left double quotation mark)          2
                                                              hint: replace “ ” ‘ ’ with straight quotes and            
                                                              re-export as UTF-8; Word, Slack and Notion apply          
                                                              this autocorrect.                                         
18   █        syntax   examples/demo-batch.ndjson:1002:1:1    byte-order mark at start of file                         1
                                                              hint: strip the BOM; some Windows editors save            
                                                              UTF-8 with one.                                           
19   █        syntax   examples/demo-batch.ndjson:778:1:118   unclosed string                                          1
                                                              hint: opened at line 1, column 118; the payload           
                                                              looks cut off. Brackets are still open too.               

correlated · members[*].first_name and members[*].frist_name co-occur in 100% of affected records; likely one upstream 
cause.

start here · fixing #1 (members[*].zip) clears 2,507 of 9,270 findings (27%).
```

Nine thousand findings collapse to nineteen groups across four layers: a ZIP sent as a
number, a date format, a constant source tag, case drift, zero-width characters,
mojibake, duplicate keys — everything a schema alone would let through. The full run is
committed as [`examples/demo-report.txt`](examples/demo-report.txt); regenerate it with
the commands under [Quick start](#quick-start).

## The geographic demo

A second, smaller batch exercises the offline geography layer: twelve member payloads with
addresses that are each a little wrong — a four-digit ZIP, a Texas ZIP with `OK`, a Dallas
city on a McKinney ZIP, a PO box in a home field, an APO ZIP with `TX`, a state written
`Texas`, a home address equal to the employer's, two household members with different
addresses. Eleven groups, one fix each:

```text
Assay · 12 payloads · 2 clean · 10 with errors · 4 ms

 1  ██████  geo  members[*].home_address.city   city is not associated with this ZIP
 2  ██████  geo  members[*].home_address.state  ZIP is in TX
 ...
```

The full report is [`examples/geo-demo-report.txt`](examples/geo-demo-report.txt). Every
lookup is local: the ZIP database ships with the package.

## Why this exists

Integration support work runs on the same loop: a payload fails, the error message is
vague (`400 Bad Request`), and someone reads raw JSON to find out why. One root cause —
a date format, an unmapped field, a quote autocorrected by Word — breaks hundreds of
records at once, but the tooling reports one error at a time.

Assay answers three questions:

1. **Is it even valid JSON?** If not, where, and what probably caused it.
2. **What is wrong with it?** Every violation at once, each with a path and a fix hint.
3. **What is the root cause?** Findings clustered and ranked, with sample values.

It also catches the second failure mode: **payloads that are perfectly valid and still
wrong**. `"7030"` lost its leading zero upstream, `"TEST"` is a placeholder, `"employee"`
is the wrong case for an enum the schema only checked at one record, and `"Smith\u200b"`
carries an invisible character that will silently break every later match.

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev

# The demo batch is generated, not committed: 2,000 payloads, a handful of causes.
uv run python examples/make_demo_batch.py examples/demo-batch.ndjson
uv run assay check examples/demo-batch.ndjson \
  --schema examples/member.schema.json \
  --rules examples/member.rules.yaml

# Geography, with the bundled ZIP reference:
uv run assay check examples/geo-demo.ndjson \
  --schema examples/member.schema.json \
  --rules examples/geo.rules.yaml
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
  --rules examples/member.rules.yaml \
  --format json --output report.json
```

### Exit codes

| code | meaning |
| ---- | ------- |
| `0`  | no errors or warnings (informational notes may still be listed) |
| `1`  | findings, all of them warnings |
| `2`  | at least one error found |
| `3`  | Assay could not run: bad schema, bad rules, unreadable input, invalid command line |

A schema that fails to compile exits `3` rather than reporting a verdict, because
nothing was actually checked. So does a rules file with a typo: a rule that silently
never fires is worse than one that refuses to load. A mistyped command line also exits
`3`, so a typo can never be mistaken for "errors found". All of this is covered by tests.

### Output formats

`--format` selects the report: `terminal` (the table), `json` (the CI contract), `html`
(a standalone, escaped page) or `sarif` (2.1.0, for GitHub code scanning).

```bash
uv run assay check payload.json --schema schema.json --format sarif --output assay.sarif
uv run assay check payload.json --schema schema.json --format html  --output report.html
```

### Redaction

`--redact` keeps paths, layers and counts, and replaces values with shape tokens
(`"J*** S****"`, `"####-##-##"`, `"#####"`). Hints that are *derived* from values — the
repaired text in a mojibake hint, the standardized address from USPS — are removed
rather than half-redacted, because a substring scrub cannot know the derived form.
Fingerprints never contained values, so grouping is unchanged.

### HTTP API and web page

The API is an optional extra, because a CLI user should not install a web framework:

```bash
uv sync --extra api
uv run assay serve --port 8000
```

Open <http://127.0.0.1:8000> for the paste-in page, or call the endpoint directly:

```bash
curl -s localhost:8000/assay -H 'content-type: application/json' -d '{
  "payload": {"members": [{"zip": 7030}]},
  "schema":  {"type": "object"},
  "redact": true
}' | jq
```

`POST /assay` accepts `payload`, `schema`, optional `rules` and `redact`, and returns the
same `Report` object as `--format json`. `GET /health` returns the version. The service is
stateless and writes nothing.

### Docker

```bash
docker build -t assay .
docker run --rm -v "$PWD:/data" assay check /data/batch.ndjson --schema /data/schema.json
```

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

### Layer 3 — setup rules

Valid JSON that matches the schema can still be wrong. Rules are YAML, validated with
Pydantic at startup (unknown keys, bad paths, duplicate ids and out-of-range thresholds
all fail with exit `3`):

```yaml
version: 1
duplicate_keys: true
strings:
  max_length:
    "$.members[*].first_name": 40
rules:
  - id: dates-ordered
    kind: compare
    left: "$.members[*].start_date"
    op: "<="
    right: "$.members[*].end_date"
    severity: error
    hint: "End date precedes start date. Check the timezone or field mapping."
```

| Kind | What it catches | Scope |
| ---- | --------------- | ----- |
| `compare` | `end_date` before `start_date`; values not comparable are skipped | per payload |
| `required_if` | `type = "dependent"` but no `relationship` | per payload |
| `placeholder` | `TEST`, `N/A`, `string`, `xxx`, `0000000000` | per payload |
| `constant_smell` | a field with the same value on ≥95% of records (an unmapped default) | batch |
| `case_drift` | the same enum written `Active`, `ACTIVE`, `active` | batch |
| `duplicate_records` | the same natural key twice in one batch | batch |
| `type_inconsistency` | `zip` a string in some records, a number in others | batch |

Duplicate keys (`{"id": 1, "id": 2}`) are found during parsing, because `json` silently
keeps the last value; they are reported as setup findings. Batch checks observe payloads
as they stream past and emit their findings at the end, with a fixed observation cap so
a pathological batch loses a finding rather than exhausting memory.

### Layer 4 — string quality

Every string value is checked for damage that passes every schema:

| Check | Example | Why it matters |
| ----- | ------- | -------------- |
| mojibake | `JosÃ©` | UTF-8 decoded as cp1252; ftfy shows the repair |
| zero-width characters | `Smith\u200b` | invisible; breaks equality, joins and dedupe |
| non-breaking spaces | `New\u00a0York` | looks identical, fails equality |
| control characters | `bad\u0007name` | truncates logs and downstream fields |
| leading/trailing whitespace | `" Smith "` | breaks matching and dedupe |
| double-encoded JSON | `"{\"id\": 1}"` | the payload nests a string where it needs an object |
| replacement character | `Mu\ufffdoz` | the original character is already lost |
| mixed scripts | `Vl\u0430dimir` | a Cyrillic `а` inside a Latin name |
| configured length | first name at 300 chars | the destination will truncate or reject it |

Findings show the invisible: `zero-width character U+200B`. A report that says "bad
character" next to text that looks fine is useless. These are warnings by default,
because the payload is well-formed — `1` is the exit code when that is all a batch has.

### Layer 5 — geographic (offline, plus opt-in USPS verification)

Downstream systems reject records whose ZIP, city and state disagree, or whose "home
address" is a mailbox. Those payloads pass every schema check, because `"75071"`,
`"Dallas"` and `"OK"` are each perfectly valid strings. This layer compares them with
the bundled reference data and with each other:

| Check | Example | Finding |
| ----- | ------- | ------- |
| ZIP format | `7507`, `750711`, `75071-12` | must be `#####` or `#####-####` |
| stripped leading zero | `7030` in NJ | likely `07030`; the zero was lost as a number |
| ZIP exists | `99999` | ZIP is not in the reference data |
| ZIP ↔ state | `75071` with `OK` | ZIP is in TX (grouped by the offending state) |
| ZIP ↔ city | `75071` with `Dallas` | not an acceptable city; USPS preferred is `MCKINNEY` |
| city alias | `Jersey City` where `HOBOKEN` is preferred | acceptable variant (information) |
| ZIP ↔ county | `75071` with `Dallas County` | ZIP is in Collin County |
| state code | `Texas`, `Tex.` | expect a two-letter code; suggests `TX` |
| military addresses | `75071` with `AA`, or an APO ZIP with `TX` | APO/FPO/DPO uses `AA`, `AE` or `AP` |
| PO box as home | `PO Box 123`, `PMB 44` | a home address must be a physical residence |
| missing street number | `Main St` | line 1 needs a primary number |
| unit in the wrong field | `123 Main St Apt 4`, line 2 empty | move the unit to line 2 |
| line length | line 1 at 52 characters | over the configured destination limit |
| city/state swapped | city `TX`, state `McKinney` | the two fields look swapped |
| batch smells | every ZIP-state mismatch uses one state | that state is probably defaulted upstream |
| batch smells | home address equals the employer's | the office address was mapped into the home field |
| batch smells | household members with different addresses | one address was copied incompletely |

Address fields are declared, not guessed:

```yaml
geo:
  addresses:
    - path: "$.members[*].home_address"
      residential: true
      line1_max: 40
      household_key: "$.members[*].household_id"
  employer_address: "$.employer.mailing_address"
```

The reference data is a ~5.6 MB SQLite file that ships inside the package, built from
GeoNames postal codes and the Census Bureau's ZCTA-to-county relationship file.
Nothing is sent anywhere. Rebuild it with:

```bash
uv run python -m assay.geo.build \
  --geonames US.txt \
  --census tab20_zcta520_county20_natl.txt
```

Attribution and known gaps are in [`NOTICE.md`](NOTICE.md).

**Street-level verification is opt-in** (`--verify`, or `geo.verify.enabled: true`)
because it sends address fields to the USPS Addresses API v3:

- credentials come from `USPS_CLIENT_ID` / `USPS_CLIENT_SECRET` in the environment, never
  from the rules file; without them the run exits `3` and names the variables
- every result — including "no match" — is cached by normalized-address hash in
  `~/.cache/assay/usps-verification.sqlite` (TTL configurable, `ASSAY_CACHE_DIR`
  overrides the location)
- requests are throttled to `max_requests_per_minute`
- a USPS standardization is a warning naming the corrected address, a non-match or DPV
  failure is an error, and an API outage is a warning — never a crash
- the tests use recorded mock responses, so CI never calls the network. Live behaviour
  depends on your USPS credentials.

```bash
USPS_CLIENT_ID=... USPS_CLIENT_SECRET=... uv run assay check payload.json \
  --schema schema.json --rules geo.rules.yaml --verify
```

## Grouping, and why it is the point

Findings are fingerprinted as `layer:rule:normalized-path:signature`:

```
members[0].zip   → schema:type:members[*].zip:type:integer→string
members[847].zip → schema:type:members[*].zip:type:integer→string
```

The fingerprint contains **no values**, which is why 1,653 records collapse into one row
and sample values can be listed separately. Groups are ranked by impact (affected
records × severity weight), ties broken by layer order, so a syntax error outranks a
string warning of the same size. When two groups always hit the same records, the report
says so — `first_name` missing and `frist_name` unknown are one copy-paste mistake, not
two bugs.

Correlation is Jaccard overlap of the two affected-record sets, computed only for groups
with at least five affected payloads, only among the top 60 groups, and only for pairs on
different paths: two problems in the same field co-occur by construction, so saying so
would teach the reader nothing.

## Development

```bash
uv sync --extra dev
uv run pre-commit install      # ruff + mypy before every commit

uv run pytest                  # unit, golden, CLI and property-based tests
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
  rules.py       layer 3: Pydantic rule config, loaded from YAML
  setup.py       layer 3: per-payload rules and batch collectors
  strings.py     layer 4: mojibake, invisible characters, double encoding
  geo/           layer 5: bundled ZIP database, builder and address checks
  geo/verify.py  opt-in USPS Addresses API client: OAuth, cache, throttle
  jsonpath.py    the small JSONPath subset rules are written in
  fingerprint.py path normalization and the fingerprint itself
  grouping.py    collapse, impact ranking, correlation
  pipeline.py    per-payload layer order, batch assembly, size/depth limits
  loader.py      files, globs, directories, NDJSON, stdin
  report.py      terminal table (Rich) and JSON
  formats.py     standalone HTML and SARIF 2.1.0
  redact.py      shape tokens and value scrubbing for --redact
  api.py         FastAPI endpoint and paste-in page
  cli.py         Typer app and the exit-code contract
ARCHITECTURE.md  the full design; unbuilt sections are marked planned
NOTICE.md        attribution for the bundled GeoNames and Census data
Dockerfile       python:3.12-slim image with the CLI on the entry point
examples/        schema, rules, deterministic 2,000-payload generator, sample reports
tests/           unit, golden, CLI, property-based, API, fixtures
```

## Limitations, stated plainly

- **USPS verification is tested against mocked responses**, not against the live API in
  this repository's CI. Live behaviour depends on your credentials and USPS availability.
- **The Switchyard validation stage (M8) is not implemented.** Everything else in the
  milestone table ships; see [`ARCHITECTURE.md`](ARCHITECTURE.md).
- PO-box-only and "unique" ZIP types are not known without USPS-licensed data, so a
  residential check catches a written PO Box but not a ZIP that only serves them.
- Redaction shapes values and scrubs them from hints, but it is not a guarantee about
  third-party logs: do not run `--verify` with redaction if USPS must not see addresses.
- Setup rules are the seven kinds above; there is no expression language and no
  per-rule path exclusions.
- The full JSON Schema vocabulary is not mapped: keywords outside the list above fall
  back to jsonschema's own message with a generic hint.
- External `$ref` targets are not resolved; internal references (`#/$defs/...`) work.
- Unknown `format` values are accepted (jsonschema's behaviour), not flagged.
- A file is read into memory up to `--max-bytes` (32 MB default); true streaming from
  disk is planned.
- Syntax detection is heuristic by nature. It only runs when parsing has already failed,
  and it never blames text inside a valid string.
- Payloads are not persisted. The report is the only artifact.

## Attribution

The bundled ZIP reference database is built from
[GeoNames](https://www.geonames.org/) postal codes (CC BY 4.0) and the
[U.S. Census Bureau](https://www.census.gov/) ZCTA-to-county relationship file (public
domain). See [`NOTICE.md`](NOTICE.md) for details, rebuild instructions and the gaps in
the data.

## License

MIT. See [LICENSE](LICENSE).
