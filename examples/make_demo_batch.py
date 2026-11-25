#!/usr/bin/env python3
"""Generate the deterministic 2,000-payload demo batch as NDJSON on stdout.

Two kinds of defect are mixed together on purpose:

* data defects that are valid JSON but wrong (a ZIP sent as a number, a date in
  MM/DD/YYYY, an enum with the wrong case), and
* text defects that are not JSON at all (a trailing comma, quotes autocorrected
  by a word processor, a truncated payload, a byte-order mark).

The first kind produces thousands of findings from a handful of causes, which is
exactly what grouping is for. Run it with:

    python examples/make_demo_batch.py > examples/demo-batch.ndjson
"""

from __future__ import annotations

import json
import random
import sys

FIRST_NAMES = [
    "Ada",
    "Grace",
    "Alan",
    "Katherine",
    "Linus",
    "Margaret",
    "Edsger",
    "Barbara",
    "Dennis",
    "Radia",
    "Ken",
    "Frances",
    "Bjarne",
    "Anita",
    "Guido",
    "Jean",
    "Tim",
    "Sophie",
    "Vint",
    "Carol",
    "José",
    "Renée",
    "Zoë",
    "Müller",
    "Søren",
]
LAST_NAMES = [
    "Lovelace",
    "Hopper",
    "Turing",
    "Johnson",
    "Torvalds",
    "Hamilton",
    "Dijkstra",
    "Liskov",
    "Ritchie",
    "Perlman",
    "Thompson",
    "Allen",
    "Stroustrup",
    "Borg",
    "Rossum",
    "Sammet",
    "Berners-Lee",
    "Wilson",
    "Cerf",
    "Shaw",
]
LOCATIONS = [
    ("75071", "MCKINNEY", "TX"),
    ("07030", "HOBOKEN", "NJ"),
    ("02139", "CAMBRIDGE", "MA"),
    ("94110", "SAN FRANCISCO", "CA"),
    ("78701", "AUSTIN", "TX"),
    ("33101", "MIAMI", "FL"),
    ("60614", "CHICAGO", "IL"),
    ("98101", "SEATTLE", "WA"),
]
TIERS = ["EMPLOYEE", "EMPLOYEE_SPOUSE", "EMPLOYEE_CHILD"]

PAYLOAD_COUNT = 2_000
SEED = 2026_11_24

# Payload indexes that get a text-level defect, so the demo has syntax groups too.
TRAILING_COMMA = {37, 512, 1488}
SMART_QUOTES = {204, 1_333}
TRUNCATED = {777}
BOM = {1_001}

# Payloads that carry a repeated key; json keeps the last value silently.
DUPLICATE_KEYS = {11, 402, 1_501, 1_903}


def member(rng: random.Random, index: int) -> dict[str, object]:
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    zip_code, _city, _state = rng.choice(LOCATIONS)
    year = rng.randint(1950, 2004)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)

    record: dict[str, object] = {
        "external_id": f"E-{index:06d}",
        "first_name": first,
        "last_name": last,
        "dob": f"{year:04d}-{month:02d}-{day:02d}",
        "hire_date": f"{rng.randint(2015, 2025):04d}-{rng.randint(1, 12):02d}-01",
        "zip": zip_code,
        "coverage_tier": rng.choice(TIERS),
        # Every record carries the same source tag. That is what an unmapped field
        # with a hard-coded default looks like, and the constant-smell rule catches it.
        "source_system": "LEGACY_HR",
    }

    if rng.random() < 0.72:
        # The classic: an upstream system stored the ZIP as an integer and the
        # leading zero is gone. Still valid JSON, still wrong.
        record["zip"] = int(zip_code)

    if rng.random() < 0.08:
        record["dob"] = f"{month:02d}/{day:02d}/{year:04d}"

    if rng.random() < 0.03:
        record["coverage_tier"] = str(record["coverage_tier"]).lower()

    if rng.random() < 0.01:
        record["frist_name"] = record.pop("first_name")

    if rng.random() < 0.004:
        record.pop("zip")

    # --- defects that are valid strings but corrupt data (layer 4)
    if rng.random() < 0.03:
        record["first_name"] = str(record.get("first_name", "")) + "\u200b"
    if rng.random() < 0.02:
        record["last_name"] = " " + str(record["last_name"])
    if rng.random() < 0.004:
        record["first_name"] = "TEST"
    if rng.random() < 0.02 and any(ord(char) > 127 for char in first):
        # What a UTF-8 name looks like after being decoded as cp1252 upstream.
        record["first_name"] = first.encode("utf-8").decode("cp1252")

    # --- a record that arrives twice under a different payload (layer 3)
    if rng.random() < 0.002 and index > 0:
        record["external_id"] = f"E-{index - 1:06d}"

    return record


def main() -> None:
    rng = random.Random(SEED)
    member_index = 0
    lines: list[str] = []
    for payload_index in range(PAYLOAD_COUNT):
        members = []
        for _ in range(rng.choice([1, 1, 2, 3])):
            members.append(member(rng, member_index))
            member_index += 1
        payload = {"batch_id": f"B-{payload_index:05d}", "members": members}
        line = json.dumps(payload, ensure_ascii=False)

        if payload_index in TRAILING_COMMA:
            line = line[:-1] + ",}"
        elif payload_index in SMART_QUOTES:
            line = line.replace('"first_name"', "\u201cfirst_name\u201d", 1)
        elif payload_index in TRUNCATED:
            line = line[: len(line) // 2]
        elif payload_index in BOM:
            line = "\ufeff" + line
        elif payload_index in DUPLICATE_KEYS:
            line = '{"batch_id": "B-DUPLICATE", ' + line[1:]

        lines.append(line)

    text = "\n".join(lines) + "\n"
    if len(sys.argv) > 1:
        # Write UTF-8 explicitly: `python ... > file` on Windows uses the console
        # code page and would mangle the smart quotes.
        with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
