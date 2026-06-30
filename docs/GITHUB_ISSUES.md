# Initial GitHub Issues

Use these as the first issues after the repository is pushed.

## Issue 1: Connect Real News API Collector

Labels: `feature`, `news`, `integration`

Replace the sample news collector with a real provider such as GDELT, NewsAPI, or Event Registry.

Acceptance criteria:

- Collector returns `date,title,summary,source,country,url`.
- Missing API key falls back to sample news.
- Batch pipeline still runs with `python -m src.main`.

## Issue 2: Improve Device-Material Mapping

Labels: `data`, `mapping`

Replace the temporary `device_material_mapping.csv` with reviewed mappings between `MED_DEVICE_5`, item names, materials, supplier groups, and mapping weights.

Acceptance criteria:

- Mapping file is human-editable CSV.
- Commodity risk output covers known item codes.
- Unknown item codes get safe default risk handling.

## Issue 3: Connect Commodity Price API

Labels: `feature`, `commodity`, `integration`

Replace the sample commodity collector with a real price source such as Alpha Vantage, EIA, Nasdaq Data Link, or internal data.

Acceptance criteria:

- Collector returns `date,material,price,volume,inventory,open_interest` when available.
- Price-only fallback remains supported.
- Risk score stays clipped to `0..1`.

## Issue 4: Add AI Serving API Integration Test

Labels: `test`, `serving`

Add a lightweight test for `/health`, `/api/v1/ai/forecasts`, `/api/v1/ai/inventory-policy`, and `/api/v1/ai/recommend-order`.

Acceptance criteria:

- Test uses generated or fixture `predictions.csv`.
- Test does not call external APIs.
- Test can run in CI.

## Issue 5: AI Result Dashboard Usability Pass

Labels: `dashboard`, `ux`

Improve the Streamlit MVP for AI result review.

Acceptance criteria:

- Filters for month, SIDO, and item code.
- Shows predicted usage, recommended stock, and recommended order.
- Displays risk score breakdown.
- Handles missing predictions gracefully.
