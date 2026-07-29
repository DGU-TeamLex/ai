# Initial GitHub Issues

Use these as the first issues after the repository is pushed.

## Issue 1: Connect Real News API Collector

Labels: `feature`, `news`, `integration`

Replace the sample news collector with a real provider such as GDELT, NewsAPI, or Event Registry.

Acceptance criteria:

- Collector returns `date,title,summary,source,country,url`.
- Missing API key falls back to sample news.
- Batch pipeline still runs with `python -m src.main`.

## Issue 2: Build Stock Item-Material Mapping

Labels: `data`, `mapping`

Build reviewed mappings between raw stock item keys, normalized item names, MFDS product identifiers, materials, supplier groups, and mapping weights.

Acceptance criteria:

- Mapping file is human-editable CSV.
- Commodity risk output covers known item codes.
- Unknown stock items get zero risk until reviewed instead of random material assignment.

## Issue 3: Connect Commodity Price API

Labels: `feature`, `commodity`, `integration`

Replace the sample commodity collector with a real price source such as Alpha Vantage, EIA, Nasdaq Data Link, or internal data.

Acceptance criteria:

- Collector returns `date,material,price,volume,inventory,open_interest` when available.
- Price-only fallback remains supported.
- Risk score stays clipped to `0..1`.

## Issue 4: Add API Integration Test

Labels: `test`, `serving`

Add a lightweight test for `/health`, `/predictions`, and `/recommend-order`.

Acceptance criteria:

- Test uses generated or fixture `predictions.csv`.
- Test does not call external APIs.
- Test can run in CI.

## Issue 5: Dashboard Usability Pass

Labels: `dashboard`, `ux`

Improve the Streamlit MVP for operational review.

Acceptance criteria:

- Filters for month, institution, department, and item code.
- Shows predicted usage, recommended stock, and recommended order.
- Displays risk score breakdown.
- Handles missing predictions gracefully.
