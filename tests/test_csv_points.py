"""CSV point import: column detection + skipping empty/invalid rows."""
import io

import pandas as pd

from src.interface.main_panel import _csv_points


def _df(text):
    return pd.read_csv(io.StringIO(text))


def test_parses_and_skips_bad_rows():
    df = _df("Latitude,lng\n45.1234567,12.5\n,\n91,0\nabc,1\n-10,-170\n")
    points, skipped = _csv_points(df)
    assert points == [{'lat': 45.123457, 'lon': 12.5}, {'lat': -10.0, 'lon': -170.0}]
    assert skipped == 3


def test_missing_columns_returns_none():
    assert _csv_points(_df("name,value\na,1\n")) is None
