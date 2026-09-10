"""Offline validation of the real config/satellites.json catalog.

Fast, no network, no GEE init: pure schema/whitelist/uniqueness checks plus
the real gate predicate imported from src.interface.main_panel (no
reimplementation — a drift between catalog and gate fails loudly here).

Run from repo root: python -m pytest tests/test_satellites_catalog.py -q
"""
import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from src.interface.main_panel import active_satellite_supports_grid

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "config" / "satellites.json"

REQUIRED = {"id", "name", "ee_collection_name", "description", "website",
            "startDate", "pixelSize", "bands"}
SECTION_OPTIONAL = {
    "satellites": {"isHourly", "cadence", "crs", "transform", "endDate", "filters"},
    "masks": {"endDate", "filters"},
}
BAND_KEYS = {"name", "units", "min", "max", "description"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_date(v):
    return isinstance(v, str) and DATE_RE.match(v) and (
        (lambda y, m, d: True if date(int(y), int(m), int(d)) else False)(*v.split("-"))
    )


CATALOG_DATA = json.loads(CATALOG.read_text(encoding="utf-8"))


def test_top_level_shape():
    assert isinstance(CATALOG_DATA.get("satellites"), list) and CATALOG_DATA["satellites"]
    assert isinstance(CATALOG_DATA.get("masks"), list) and CATALOG_DATA["masks"]


def test_ids_and_collections_unique_across_sections():
    ids = [e["id"] for sec in ("satellites", "masks") for e in CATALOG_DATA[sec] if isinstance(e, dict)]
    collections = [e.get("ee_collection_name") for sec in ("satellites", "masks")
                   for e in CATALOG_DATA[sec] if isinstance(e, dict)]
    assert len(ids) == len(set(ids)), "duplicate id across satellites+masks"
    assert len(collections) == len(set(collections)), "duplicate ee_collection_name"


@pytest.mark.parametrize(("section", "idx"),
                         [(s, i) for s in ("satellites", "masks")
                          for i in range(len(CATALOG_DATA[s]))])
def test_entry_schema(section, idx):
    e = CATALOG_DATA[section][idx]
    ctx = f"{section}[{idx}] {e.get('id', '<no id>')}"

    unknown = set(e) - REQUIRED - SECTION_OPTIONAL[section]
    assert not unknown, f"{ctx}: unknown key(s) {unknown}"
    missing = REQUIRED - set(e)
    assert not missing, f"{ctx}: missing required key(s) {missing}"

    for key in ("id", "name", "ee_collection_name", "description", "website"):
        assert isinstance(e[key], str) and e[key], f"{ctx}: {key} must be non-empty str"
    assert is_date(e["startDate"]), f"{ctx}: startDate not a real YYYY-MM-DD date"
    assert is_int(e["pixelSize"]) and e["pixelSize"] > 0, f"{ctx}: pixelSize must be positive int"
    assert isinstance(e.get("isHourly", True), bool), f"{ctx}: isHourly must be bool"
    assert isinstance(e.get("cadence", "x"), str) and e.get("cadence", "x"), \
        f"{ctx}: cadence must be non-empty str"
    if "endDate" in e:
        assert is_date(e["endDate"]), f"{ctx}: endDate not a real YYYY-MM-DD date"
    if "filters" in e:
        assert isinstance(e["filters"], list) and all(isinstance(f, dict) for f in e["filters"]), \
            f"{ctx}: filters must be a list of objects"

    bands = e["bands"]
    assert isinstance(bands, list) and bands, f"{ctx}: bands must be non-empty list"
    for i, band in enumerate(bands):
        assert set(band) <= BAND_KEYS, f"{ctx}: bands[{i}] unknown keys {set(band) - BAND_KEYS}"
        for key in ("name", "description"):
            assert isinstance(band.get(key), str) and band[key], \
                f"{ctx}: bands[{i}].{key} must be non-empty str"
        assert isinstance(band.get("units"), str), f"{ctx}: bands[{i}].units must be str ('' allowed)"
        for key in ("min", "max"):
            if key in band:
                assert is_num(band[key]), f"{ctx}: bands[{i}].{key} must be numeric"

    has_crs, has_tf = "crs" in e, "transform" in e
    assert has_crs == has_tf, f"{ctx}: crs/transform co-required (got only one)"
    if has_tf:
        assert isinstance(e["crs"], str) and e["crs"], f"{ctx}: crs must be non-empty str"
        tf = e["transform"]
        assert isinstance(tf, list) and len(tf) == 6, f"{ctx}: transform must have 6 values"
        assert all(is_num(v) and math.isfinite(v) for v in tf), \
            f"{ctx}: transform values must be finite numbers (int or float, not bool)"


def test_grid_gate_predicate():
    """Every entry satisfying crs + 6-transform + pixelSize>=1000 must pass the
    REAL predicate; entries lacking the keys must not (toggle hidden, not error)."""
    for e in CATALOG_DATA["satellites"]:
        gated = bool(e.get("crs") and isinstance(e.get("transform"), list)
                     and len(e["transform"]) == 6 and e.get("pixelSize", 0) >= 1000)
        assert active_satellite_supports_grid(e) is gated, \
            f"{e['id']}: gate disagreement (gated={gated})"
    for e in CATALOG_DATA["masks"]:
        assert active_satellite_supports_grid(e) is False, f"mask {e['id']} must never gate"


# --- regression: scripts/add_satellite.py must accept UTF-8 BOM files (Windows
# editors/Write tools emit a BOM by default; utf-8-sig reads BOM and BOM-less
# identically). Drives the real CLI via subprocess, read-only (--dry-run). ---

BOM_ENTRY = {
    "id": "BOM_REGRESSION_TEST_ENTRY",
    "name": "BOM Regression Test Entry",
    "ee_collection_name": "BOM/REGRESSION/TEST",
    "description": "Proves the entry-file reader tolerates a leading UTF-8 BOM.",
    "website": "https://example.org/bom-regression",
    "startDate": "2020-01-01",
    "pixelSize": 1000,
    "bands": [{"name": "b", "units": "", "description": "Band."}],
}


def _run_add_satellite(*argv):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "add_satellite.py"), *argv],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_add_satellite_accepts_bom_entry_file(tmp_path):
    entry_path = tmp_path / "entry.json"
    entry_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(BOM_ENTRY).encode("utf-8"))
    r = _run_add_satellite("--entry", str(entry_path), "--config", str(CATALOG), "--dry-run")
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "VALIDATION OK" in r.stdout


def test_add_satellite_accepts_bom_catalog_file(tmp_path):
    entry_path = tmp_path / "entry.json"
    entry_path.write_text(json.dumps(BOM_ENTRY), encoding="utf-8")
    bom_catalog = tmp_path / "catalog.json"
    bom_catalog.write_bytes(b"\xef\xbb\xbf" + CATALOG.read_bytes())
    r = _run_add_satellite("--entry", str(entry_path), "--config", str(bom_catalog), "--dry-run")
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "VALIDATION OK" in r.stdout
