"""Local-download plot: _series_frame shapes data for st.line_chart."""
import pandas as pd

from src.interface.main_panel import _series_frame


def test_series_frame_points_hourly_and_single():
    df = pd.DataFrame({
        'date': ['2020-01-01', '2020-01-01', '2020-01-01', '2020-01-01'],
        'time': ['00:00', '00:30', '00:00', '00:30'],
        'point_id': [1, 1, 2, 2],
        'precip': [1.0, 2.0, 3.0, None],
    })

    wide = _series_frame(df, 'precip', 'point_id', [1, 2])
    assert list(wide.columns) == [1, 2]
    assert wide.index[1] == pd.Timestamp('2020-01-01 00:30')
    assert wide.loc[pd.Timestamp('2020-01-01 00:00'), 2] == 3.0

    only_one = _series_frame(df, 'precip', 'point_id', [1])
    assert list(only_one.columns) == [1]

    single = _series_frame(df.drop(columns='point_id'), 'precip')
    assert list(single.columns) == ['precip']
    assert single.iloc[0, 0] == 2.0  # mean of 1.0 and 3.0
