# Third-party data and attribution

Assay ships one data file, `src/assay/geo/data/zips.sqlite`, built from two public
sources.

## GeoNames: postal codes

<https://www.geonames.org/>

The postal code, place name, state and county rows come from GeoNames' US postal code
export (`US.zip` / `US.txt`). GeoNames data is licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
Assay attributes GeoNames in this file, in the README and in the database's `meta`
table.

## U.S. Census Bureau: ZCTA to county

<https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.html>

The ZIP-to-county ratios come from the Census Bureau's 2020 ZCTA-to-county
relationship file (`tab20_zcta520_county20_natl.txt`), using the land-area part of
each ZIP/county pair. Census Bureau data is in the public domain.

## What the data does not cover

No USPS-licensed product is bundled. That means:

- **PO-box-only and "unique" ZIP types are not known.** A residential check catches a
  PO Box written in the address line, not a ZIP that is only served by PO boxes.
- **Street-level validity and deliverability need the USPS Addresses API (M6).**
  Offline checks confirm that ZIP, city, state and county agree with each other and
  with the reference table; they do not confirm that an address exists.
- **Military ZIPs** (APO/FPO/DPO) are not geographic ZCTAs. They are recognised by
  their ZIP ranges and must use the state `AA`, `AE` or `AP`.

## Rebuilding

```bash
python -m assay.geo.build \
  --geonames US.txt \
  --census tab20_zcta520_county20_natl.txt
```

The command writes `src/assay/geo/data/zips.sqlite` and records the build date, the
source file names and the attribution in its `meta` table.
