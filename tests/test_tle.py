from datetime import datetime, timezone

import pytest

import tle as tle_mod
from tle import Tle, get_tle, parse_tle

FUNCUBE = open("data/tle/funcube1.tle").read()


def test_parse_and_epoch():
    t = Tle(*parse_tle(FUNCUBE, 39444), source="bundled")
    assert t.norad == 39444
    assert t.inclination_deg == pytest.approx(97.8475)
    # 26324.93090496 -> 20 Nov 2026, 22:20 UTC
    assert t.epoch.replace(microsecond=0) == datetime(2026, 11, 20, 22, 20, 30, tzinfo=timezone.utc)


def test_parse_rejects_wrong_satellite_and_garbage():
    with pytest.raises(ValueError):
        parse_tle(FUNCUBE, 25544)
    with pytest.raises(ValueError):
        parse_tle("<html>error</html>")


def test_download_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(tle_mod, "CACHE_DIR", tmp_path)
    calls = []

    def fake(norad, timeout):
        calls.append(norad)
        return FUNCUBE

    assert get_tle(39444, download=fake).source == "celestrak"
    assert get_tle(39444, download=fake).source == "cache"
    assert calls == [39444]


def test_offline_falls_back_to_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(tle_mod, "CACHE_DIR", tmp_path)

    def offline(norad, timeout):
        raise OSError

    assert get_tle(39444, download=offline).source == "bundled"
    with pytest.raises(tle_mod.TleUnavailable):
        get_tle(25544, download=offline)
