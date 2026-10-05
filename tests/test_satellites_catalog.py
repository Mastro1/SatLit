"""Offline validation of the real config/satellites.json catalog.

Fast, no network, no GEE init: the REAL validate_entry imported from
scripts/add_satellite (no reimplementation — a drift between catalog and
validator fails loudly here) plus the real gate predicate imported from
src.interface.main_panel.

Run from repo root: python -m pytest tests/test_satellites_catalog.py -q
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.interface.main_panel import active_satellite_supports_grid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from add_satellite import validate_entry  # noqa: E402

CATALOG = ROOT / "config" / "satellites.json"
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
    # validate_entry also checks cross-section uniqueness; drop the entry by
    # identity from the scan so it is not compared against itself.
    rest = dict(CATALOG_DATA)
    rest[section] = [x for x in CATALOG_DATA[section] if x is not e]
    errors = validate_entry(e, section, rest)
    assert not errors, f"{ctx}: {errors}"


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
