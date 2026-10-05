---
name: gee-satellite-json
description: >
  Generates a ready-to-paste JSON entry for a new satellite/dataset to add to the
  GEE_data_extraction_UI satellites.json file (Mastro1/SatLit).
  Use this skill whenever the user wants to add a new Google Earth Engine dataset,
  satellite, or climate product to satellites.json — even if they just paste a GEE
  catalog URL, a dataset name, or say things like "add a new satellite", "new dataset
  entry", "add GPM", "add ERA5", "add MODIS band", or "update my satellites file".
  The skill fetches the full band catalog directly from the official GEE documentation
  and produces a JSON object that precisely matches the existing schema.
---
 
# GEE Satellite JSON Entry Creator
 
Produces a single JSON entry object for the `satellites` (or `masks`) array of `satellites.json`
for the [SatLit](https://github.com/Mastro1/SatLit) project, then walks it through the safe
validate → insert → grid-probe → test pipeline (Steps 5–6).
 
---
 
## Step 1 — Get the dataset URL
 
Ask the user for the GEE catalog URL of the new dataset if they haven't already provided it.
 
Example prompt:
> "Please share the Google Earth Engine catalog URL for the dataset you want to add.
> For example: `https://developers.google.com/earth-engine/datasets/catalog/NASA_GPM_L3_IMERG_V07`"
 
---
 
## Step 2 — Fetch the source documentation
 
The GEE catalog exposes a machine-readable Markdown file for every dataset.
Construct the `.md.txt` URL by appending `.md.txt` to the catalog page URL:
 
```
https://developers.google.com/earth-engine/datasets/catalog/DATASET_ID.md.txt
```
 
**Rules:**
- If the user already provided a `.md.txt` URL → fetch it directly.
- If the user provided a standard catalog HTML URL → append `.md.txt` and fetch.
- If the user provided just a dataset ID (e.g. `NASA/GPM_L3/IMERG_V07`) → replace `/` with `_`, prepend the base URL, append `.md.txt`.
Use `web_fetch` to retrieve the full `.md.txt` content. Do not rely on memory or training data — always fetch live.
 
---
 
## Step 3 — Extract all fields from the source
 
Parse the fetched Markdown to extract **every** piece of structured information:
 
| Field | Source location in .md.txt |
|---|---|
| `id` | Derive from the EE collection name: replace `/` with `_` and capitalize sensibly (e.g. `NASA/GPM_L3/IMERG_V07` → `NASA_GPM_L3_IMERG_V07`) |
| `name` | Dataset title from the page heading |
| `ee_collection_name` | The collection string used in `ee.ImageCollection(...)`, found in the Code Editor example |
| `description` | 1–3 sentence summary from the Description section |
| `website` | The standard catalog HTML URL (without `.md.txt`) |
| `startDate` | Start of Dataset Availability, formatted as `YYYY-MM-DD` |
| `isHourly` | Set `true` if cadence is **strictly less than 1 day** (e.g. 30 minutes, 1 hour, 3 hours). The field signals that the time-of-day component matters to the system. Omit for daily or longer cadences. |
| `cadence` | Include for all sub-daily datasets to document the actual cadence (e.g. `"30min"`, `"1h"`, `"3h"`); omit for daily or longer |
| `pixelSize` | **Pixel Size in metres** from the Bands section. Use the integer value as-is |
| `bands` | Full band table — see Band Extraction rules below |
 
### Band Extraction Rules
 
Extract **every row** from the Bands table in the `.md.txt`. For each band:
 
```json
{
  "name": "exact_band_name_from_source",
  "units": "units string or empty string if none",
  "min": 0,        // include only if the source provides a min value
  "max": 100,      // include only if the source provides a max value
  "description": "Concise but complete description in your own words."
}
```
 
- `min` and `max`: include **only** when the source table provides explicit values (even if marked with `*` as estimated). Omit both fields entirely if the source gives no range.
- `units`: always include, use `""` for dimensionless or when blank in the source.
- `description`: write a clear, self-contained sentence. For bitmask bands, list the key flag values inline.
- Do **not** skip any band, including QA flags, bitmask bands, error bands, or static/time-invariant bands.
---
 
## Step 4 — Produce the JSON entry file

Produce a single, valid JSON object matching the schema below. Do **not** wrap it in the outer `{"satellites": [...]}` structure — output only the object. **Save it as a `.json` file** (e.g. `new_dataset.json`) — the pipeline script consumes the file, not a chat paste.

### Schema

```json
{
  "id": "STRING",
  "name": "STRING",
  "ee_collection_name": "STRING",
  "description": "STRING",
  "website": "STRING",
  "startDate": "YYYY-MM-DD",
  "isHourly": true,           // optional — set true for ANY sub-daily cadence (< 1 day)
  "cadence": "STRING",        // optional — include for all sub-daily datasets (e.g. "30min", "1h")
  "pixelSize": INTEGER,
  "crs": "STRING",            // optional — VERIFIED-LIVE-ONLY, see rule below. NEVER invent.
  "transform": [NUMBER x6],   // optional — VERIFIED-LIVE-ONLY, see rule below. NEVER invent.
  "bands": [
    {
      "name": "STRING",
      "units": "STRING",
      "min": NUMBER,           // optional — only if source provides it
      "max": NUMBER,           // optional — only if source provides it
      "description": "STRING"
    }
  ]
}
```

**`crs` / `transform` rule (pixel-grid overlay):** these two keys are **co-required** (both or neither) and must come from `scripts/fetch_grid_meta.py` probing the LIVE GEE projection — **NEVER** derive, estimate, or invent them from documentation, band tables, or prior datasets. Without them the dataset simply does not get the pixel-grid toggle (the UI gate hides it — that is expected behavior, not an error); with wrong values the overlay renders a false grid, which is worse. The script refuses to overwrite existing values — removal is always manual. Rotation (`transform` b/d ≠ 0) and sub-kilometre pixels are not overlay-compatible.

### Field presence rules (summary)

| Field | Required | Notes |
|---|---|---|
| `id` | ✅ | Always; unique across satellites AND masks |
| `name` | ✅ | Always |
| `ee_collection_name` | ✅ | Always; unique |
| `description` | ✅ | Always |
| `website` | ✅ | Always |
| `startDate` | ✅ | Always, real calendar date `YYYY-MM-DD` |
| `isHourly` | ⚙️ | `true` for any sub-daily cadence (< 1 day); omit for daily or longer |
| `cadence` | ⚙️ | Include for all sub-daily datasets; omit for daily or longer |
| `pixelSize` | ✅ | Always, positive integer (metres) |
| `crs` | ⚙️ | Only via `fetch_grid_meta.py --write` (live-verified); co-required with `transform` |
| `transform` | ⚙️ | Only via `fetch_grid_meta.py --write` (live-verified); 6 numbers |
| `bands[].name` | ✅ | Always |
| `bands[].units` | ✅ | Always (use `""` if none) |
| `bands[].min` | ⚙️ | Only if source provides it |
| `bands[].max` | ⚙️ | Only if source provides it |
| `bands[].description` | ✅ | Always |

Any other top-level key (e.g. a typo like `transfrom`) is **rejected** by the validator — the whitelist is enforced, not advisory.

---

## Step 5 — Validate and insert with the pipeline

Do **not** hand-edit `config/satellites.json`. Run the safe pipeline from the repo root:

```powershell
# 1. Validate first — reports every schema/whitelist/uniqueness error, changes nothing
& ".venv/Scripts/python.exe" scripts/add_satellite.py --entry new_dataset.json --dry-run

# 2. Fix any reported errors and re-run --dry-run until it passes

# 3. Insert (appends to the "satellites" array; use --section masks for mask datasets)
& ".venv/Scripts/python.exe" scripts/add_satellite.py --entry new_dataset.json

# 4. Fetch verified grid metadata (LIVE probe; print first, then write)
& ".venv/Scripts/python.exe" scripts/fetch_grid_meta.py --id <NEW_ID>
& ".venv/Scripts/python.exe" scripts/fetch_grid_meta.py --id <NEW_ID> --write
```

Script behavior you can rely on:
- `add_satellite.py` validates real calendar dates, positive-int `pixelSize` (bools rejected), band schema, per-section optional-key whitelists, id + `ee_collection_name` uniqueness across both sections, and crs/transform co-presence + 6 finite numbers. Every failure exits non-zero naming the offending field.
- The insert is additive-only: the script proves the diff is a single contiguous insertion before writing (git-numstat + rollback otherwise) — it can never silently reformat the file.
- `fetch_grid_meta.py --write` inserts `crs` + `transform` after the entry's `pixelSize` line, then re-probes to confirm a MATCH in the same invocation. It refuses — loudly, without writing — if the probe fails, the live transform is rotated (|b| or |d| > 1e-7), or the entry already carries either key (remove manually first; never overwritten silently).

---

## Step 6 — Test

```powershell
& ".venv/Scripts/python.exe" -m pytest tests/test_satellites_catalog.py -q   # offline catalog gate
& ".venv/Scripts/python.exe" -m pytest tests/ -q                             # full suite
```

`test_satellites_catalog.py` validates the real catalog (schema, whitelists, uniqueness, dates, bands, crs/transform) and re-checks the grid gate with the REAL predicate from `src.interface.main_panel`. Both commands must be green before delivery. When presenting, briefly note:
- Total number of bands extracted.
- Any fields that were omitted and why (e.g. "`min`/`max` omitted — not provided by source").
- Any structural decisions made (e.g. `cadence` added, `isHourly` omitted, grid metadata written or intentionally left out).

---
 
## Edge cases
 
- **Multiple pixel sizes**: If different bands have different pixel sizes, use the most common value for the top-level `pixelSize` field and note the exception in the affected band's `description`.
- **Dataset with no bands table**: Some catalog pages list parameters in prose rather than a table. Extract them manually with the same schema.
- **Masks / non-satellite datasets**: The same schema applies. Do not add a `masks` wrapper in the entry file — insert into the right array with `add_satellite.py --section masks` instead. Masks accept only `endDate` and `filters` as optional keys (no `crs`/`transform`).
- **URL already ends in `.md.txt`**: Fetch directly, no modification needed.
