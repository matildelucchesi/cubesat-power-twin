import pytest
from fastapi.testclient import TestClient

from api import app

client = TestClient(app)


def test_config_endpoint():
    r = client.get("/api/config")
    assert r.status_code == 200
    assert "battery" in r.json()


def test_simulate_returns_series_and_summary():
    r = client.post("/api/simulate", json={"days": 0.5, "step_s": 120,
                                           "eclipse_model": "cylindrical"})
    assert r.status_code == 200
    body = r.json()
    n = len(body["series"]["hours"])
    assert n == 360
    assert len(body["series"]["soc"]) == n
    assert 0 <= body["summary"]["min_soc"] <= 1
    assert body["ground_track"]["lat"]
    assert len(body["orbit3d"]["x_km"]) == len(body["orbit3d"]["sunlit"]) > 0
    assert len(body["orbit3d"]["sun_dir"]) == 3
    o = body["orbit3d"]
    assert len(o["lat_deg"]) == len(o["lon_deg"]) == len(o["alt_km"]) == len(o["hours"])
    assert all(-90 <= v <= 90 for v in o["lat_deg"]) and "T" in o["start"]


def test_failed_faces_reduce_power():
    base = {"days": 0.5, "step_s": 120, "eclipse_model": "cylindrical"}
    full = client.post("/api/simulate", json=base).json()["summary"]["mean_power_w"]
    half = client.post("/api/simulate", json={**base, "failed_faces": ["+X", "-X", "+Y"]}
                       ).json()["summary"]["mean_power_w"]
    assert half < full


def test_failure_scenarios_scale_power():
    base = {"days": 0.5, "step_s": 120, "eclipse_model": "cylindrical"}
    power = lambda **kw: client.post("/api/simulate", json={**base, **kw}).json()["summary"]["mean_power_w"]
    full = power()
    # Radiation degradation scales every cell.
    assert power(array_degradation=0.2) == pytest.approx(0.8 * full, rel=0.02)
    # One string of two lost on one face of six (tumbling, 1U): 1/12 of the area.
    assert power(face_loss={"+X": 0.5}) == pytest.approx(11 / 12 * full, rel=0.02)
    # A stuck wing removes half of the deployable area.
    two, one, none = (power(deployables=True, wings_deployed=w) for w in (2, 1, 0))
    assert none == pytest.approx(full, rel=0.02)
    assert one == pytest.approx((two + none) / 2, rel=0.02)


def test_invalid_request_is_rejected():
    r = client.post("/api/simulate", json={"days": -1})
    assert r.status_code == 422


def test_catalog_lists_presets_and_form_factors():
    body = client.get("/api/catalog").json()
    assert any(s["norad"] == 39444 for s in body["satellites"])
    assert set(body["form_factors"]) == {"1U", "3U", "6U"}


def test_larger_form_factor_and_deployables_give_more_power():
    base = {"days": 0.5, "step_s": 120, "eclipse_model": "cylindrical"}
    p1 = client.post("/api/simulate", json=base).json()["summary"]["mean_power_w"]
    p3 = client.post("/api/simulate", json={**base, "form_factor": "3U"}).json()["summary"]["mean_power_w"]
    p3d = client.post("/api/simulate", json={**base, "form_factor": "3U", "deployables": True}
                      ).json()["summary"]["mean_power_w"]
    assert p1 < p3 < p3d


def test_unknown_satellite_without_network_is_a_clear_error(monkeypatch):
    import tle

    def offline(norad, timeout):
        raise OSError("no network")

    monkeypatch.setattr(tle, "_download", offline)
    r = client.post("/api/simulate", json={"norad": 99999, "days": 0.5})
    assert r.status_code == 502
    assert "99999" in r.json()["detail"]


def test_safe_mode_keeps_battery_alive_with_failed_faces():
    req = {"days": 5, "step_s": 120, "eclipse_model": "cylindrical",
           "failed_faces": ["+X", "-Y", "+Z"]}
    off = client.post("/api/simulate", json=req).json()["summary"]
    on = client.post("/api/simulate", json={**req, "safe_mode": True}).json()
    assert off["min_soc"] == 0.0
    assert on["summary"]["min_soc"] > 0.3
    assert on["summary"]["safe_entries"] >= 1 and 0 < on["summary"]["safe_fraction"] < 1
    assert len(on["series"]["safe"]) == len(on["series"]["hours"])


def test_safe_thresholds_must_be_ordered():
    r = client.post("/api/simulate", json={"safe_mode": True, "safe_enter_soc": 0.7, "safe_exit_soc": 0.5})
    assert r.status_code == 422
