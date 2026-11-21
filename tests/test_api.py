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


def test_failed_faces_reduce_power():
    base = {"days": 0.5, "step_s": 120, "eclipse_model": "cylindrical"}
    full = client.post("/api/simulate", json=base).json()["summary"]["mean_power_w"]
    half = client.post("/api/simulate", json={**base, "failed_faces": ["+X", "-X", "+Y"]}
                       ).json()["summary"]["mean_power_w"]
    assert half < full


def test_invalid_request_is_rejected():
    r = client.post("/api/simulate", json={"days": -1})
    assert r.status_code == 422
