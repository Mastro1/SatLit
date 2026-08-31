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
 
Produces a single JSON object ready to paste into the `satellites` array of `satellites.json`
for the [SatLit](https://github.com/Mastro1/SatLit) project.
 
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
 
## Step 4 — Produce the JSON entry
 
Output a single, valid JSON object matching the schema below. Do **not** wrap it in the outer `{"satellites": [...]}` structure — output only the object to paste into the array.
 
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
 
### Field presence rules (summary)
 
| Field | Required | Notes |
|---|---|---|
| `id` | ✅ | Always |
| `name` | ✅ | Always |
| `ee_collection_name` | ✅ | Always |
| `description` | ✅ | Always |
| `website` | ✅ | Always |
| `startDate` | ✅ | Always |
| `isHourly` | ⚙️ | `true` for any sub-daily cadence (< 1 day); omit for daily or longer |
| `cadence` | ⚙️ | Include for all sub-daily datasets; omit for daily or longer |
| `pixelSize` | ✅ | Always |
| `bands[].name` | ✅ | Always |
| `bands[].units` | ✅ | Always (use `""` if none) |
| `bands[].min` | ⚙️ | Only if source provides it |
| `bands[].max` | ⚙️ | Only if source provides it |
| `bands[].description` | ✅ | Always |
 
---
 
## Step 5 — Validate and deliver
 
Before outputting:
1. Confirm the JSON is syntactically valid (no trailing commas, all strings quoted).
2. Confirm band count matches the source table.
3. Save the output as a `.json` file and present it to the user with `present_files`.
After presenting, briefly note:
- Total number of bands extracted.
- Any fields that were omitted and why (e.g. "`min`/`max` omitted — not provided by source").
- Any structural decisions made (e.g. `cadence` added, `isHourly` omitted).
---
 
## Edge cases
 
- **Multiple pixel sizes**: If different bands have different pixel sizes, use the most common value for the top-level `pixelSize` field and note the exception in the affected band's `description`.
- **Dataset with no bands table**: Some catalog pages list parameters in prose rather than a table. Extract them manually with the same schema.
- **Masks / non-satellite datasets**: The same schema applies. Do not add a `masks` wrapper — that is handled separately in the user's file.
- **URL already ends in `.md.txt`**: Fetch directly, no modification needed.
