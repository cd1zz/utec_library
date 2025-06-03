import runpy
from datetime import datetime

module = runpy.run_path('utec/utils/data.py')
date_from_4bytes = module['date_from_4bytes']


def test_date_from_4bytes_known_timestamp():
    data = b'\x60\xdc\x92\x8b'
    expected = datetime(2024, 3, 14, 9, 10, 11)
    assert date_from_4bytes(data) == expected


def test_date_from_4bytes_short_input_returns_none():
    assert date_from_4bytes(b'\x01\x02') is None
