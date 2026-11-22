from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from assistant import explain, template_explanation
from detection import cusum, cusum_check, limit_check, window_features
from estimation import synchronise
from health import base_config, twin_run
from prognosis import forecast
from telemetry import Scenario, docv_dsoc, generate, ocv


@pytest.fixture(scope="module")
def setup():
    cfg = base_config(8)
    df = twin_run(cfg)
    scenarios = [Scenario("nominal", float("inf"), seed=1),
                 Scenario("string_loss", 60, 1.0, seed=2),
                 Scenario("array_degradation", 60, 0.04, seed=3),
                 Scenario("capacity_fade", 60, 0.04, seed=4),
                 Scenario("load_anomaly", 60, 0.3, seed=5)]
    tm = generate(df, cfg, scenarios)
    est = synchronise(tm, df.power_w.to_numpy(), df.sunlit, cfg)
    f = window_features(tm, est, df.power_w.to_numpy(), cfg["battery"]["capacity_wh"],
                        cfg["simulation"]["step_s"])
    return cfg, df, scenarios, tm, est, f


def test_ocv_curve_is_monotonic():
    soc = np.linspace(0, 1, 50)
    assert np.all(np.diff(ocv(soc)) > 0)
    assert np.all(docv_dsoc(soc) > 0)
    assert ocv(1.0) == pytest.approx(8.4)


def test_unknown_fault_is_rejected():
    with pytest.raises(ValueError):
        Scenario("meteor")


def test_tumbling_truth_matches_twin_on_average(setup):
    _, df, _, tm, _, _ = setup
    # The twin's tumbling model is the orbit average of the real tumbling.
    ratio = tm["truth_p_available_w"][0].mean() / df.power_w.mean()
    assert 0.8 < ratio < 1.2
    # A lost face removes power only after the onset.
    assert tm["truth_p_available_w"][1].mean() < tm["truth_p_available_w"][0].mean()


def test_filters_estimate_unmeasured_parameters(setup):
    _, _, _, tm, est, _ = setup
    end = -1
    assert est["load_bias_w"][4, end] == pytest.approx(0.3, abs=0.03)
    assert abs(est["load_bias_w"][0, end]) < 0.03
    # SOC is not measured: the EKF recovers it from voltage and current.
    assert np.abs(est["soc"] - tm["truth_soc"]).mean() < 0.02
    # The degrading array is tracked downwards.
    assert est["array_factor"][2, end] < est["array_factor"][0, end] - 0.08
    # The fading battery is seen as losing capacity.
    assert est["capacity_wh"][3, end] < est["capacity_wh"][0, end] - 1.0


def test_cusum_statistic():
    z = np.array([[0.0, 0.2, 3.0, 3.0, -5.0]])
    assert cusum(z, k=0.5).tolist() == [[0.0, 0.0, 2.5, 5.0, 0.0]]


def test_cusum_detects_load_anomaly_and_spares_nominal(setup):
    _, _, scenarios, _, _, f = setup
    alarms = cusum_check(f)
    assert alarms[0].hour is None
    assert alarms[4].hour is not None and alarms[4].subsystem == "loads"
    assert alarms[4].hour - scenarios[4].onset_h < 6


def test_limit_check_misses_gradual_faults(setup):
    *_, f = setup
    alarms = limit_check(f)
    assert alarms[0].hour is None
    assert alarms[2].hour is None  # array degradation hidden by the power margin


def test_prognosis_sees_degradation_coming(setup):
    cfg = base_config(16)
    df = twin_run(cfg)
    tm = generate(df, cfg, [Scenario("array_degradation", 60, 0.04, seed=3)])
    est = synchronise(tm, df.power_w.to_numpy(), df.sunlit, cfg)
    n_now = 8 * 24 * 60
    past = {k: v[:, :n_now] for k, v in est.items()}
    fc = forecast(df, cfg, past, n_now, 8, n_samples=100)
    assert fc["p_safe_mode"] > 0.2
    calm = forecast(df, cfg, past, n_now, 8, n_samples=100, tx_duty=0.0)
    assert calm["p_safe_mode"] < fc["p_safe_mode"]


REPORT = {
    "days": 6, "scenario": {"kind": "load_anomaly"},
    "alarms": [{"method": "limit", "hour": None, "channel": None},
               {"method": "twin_hybrid", "hour": 74.0, "subsystem": "loads", "fault": "load_anomaly"}],
    "estimates_now": {"array_factor": 1.0, "array_factor_sd": 0.03, "capacity_wh": 18.4,
                      "capacity_sd": 0.4, "capacity_datasheet_wh": 19.2, "load_bias_w": 0.3},
    "prognosis": {"p_safe_mode": 0.0, "horizon_days": 4, "safe_mode_h_p50": None},
    "actions": [{"tx_duty": 0.5, "p_safe_mode": 0.0, "recommended": True}],
}


def test_template_explanation():
    text = template_explanation(REPORT)
    assert "extra load" in text and "74.0" in text and "no safe mode" in text


class FakeClient:
    """Stands in for anthropic.Anthropic: first asks for a what-if, then answers."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            block = SimpleNamespace(type="tool_use", id="t1", name="run_what_if", input={"tx_duty": 0.25})
            return SimpleNamespace(stop_reason="tool_use", content=[block])
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text="Heater stuck on.")])


def test_assistant_uses_the_twin_as_a_tool():
    fake = FakeClient()
    seen = []
    out = explain(REPORT, "What happened?", lambda d: seen.append(d) or {"p_safe_mode": 0.0}, client=fake)
    assert out == {"text": "Heater stuck on.", "source": "model",
                   "tool_calls": [{"tool": "run_what_if", "input": {"tx_duty": 0.25},
                                   "output": {"p_safe_mode": 0.0}}]}
    assert seen == [0.25]
    assert fake.calls[0]["model"] == "claude-opus-5-5"
    assert fake.calls[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


def test_health_api(monkeypatch):
    from api import app

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(app)
    body = {"fault": "load_anomaly", "days": 5, "horizon_days": 3}
    r = client.post("/api/health", json=body)
    assert r.status_code == 200
    rep = r.json()
    assert {a["method"] for a in rep["alarms"]} == {"limit", "twin_cusum", "twin_ml", "twin_hybrid"}
    assert len(rep["series"]["hours"]) == len(rep["series"]["soc_est"]) > 0
    assert rep["actions"] and sum(a["recommended"] for a in rep["actions"]) == 1
    assert client.post("/api/health", json={**body, "onset_h": 500}).status_code == 422
