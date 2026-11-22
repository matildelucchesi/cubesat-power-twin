"""Operator assistant: explains an alarm in plain language, using the twin as a tool.

The detection, diagnosis and prognosis are computed by the twin; the language
model does not invent numbers. It receives the health report, can call the
twin to run what-if forecasts (for example a lower transmitter duty cycle),
and writes a short explanation for the operator: what happened, how sure the
twin is, how much time is left and what to do.

The loop is the plain Messages API with client tools, bounded to a few
rounds, because the tool set is small and fixed. Without an Anthropic
credential (ANTHROPIC_API_KEY or an `ant auth login` profile) the endpoint
answers with a template explanation built from the same report, so the
dashboard keeps working offline.
"""
import json

MODEL = "claude-opus-5-5"
MAX_ROUNDS = 4

SYSTEM = """You are the power-system assistant of a CubeSat operations team.
You explain the output of a digital twin of the electrical power system to an operator.

The twin is synchronised with telemetry by Kalman filters, detects anomalies with a CUSUM test
on its parameter drifts, diagnoses the fault type with a random-forest classifier, and forecasts
the battery state of charge with a Monte Carlo ensemble. Classic limit checking runs alongside.

Rules:
- Use only numbers from the report or from tool results. Do not invent values.
- Say how confident the twin is (uncertainties, probabilities), and say when methods disagree.
- If a corrective action is relevant, check it with the run_what_if tool before recommending it.
- The report includes the simulation's ground truth so the twin can be evaluated. Do not use it to
  explain the alarm; mention it only to say whether the twin's estimate was right.
- Answer in the language of the operator's question. Be brief: at most about 180 words,
  with short paragraphs: what happened, evidence, time left, recommended action."""

TOOLS = [
    {
        "name": "run_what_if",
        "description": ("Forecast the battery state of charge from the current synchronised state "
                        "with a different transmitter duty cycle. Returns the probability of "
                        "entering safe mode within the horizon, the median time to safe mode in "
                        "hours (null if not reached) and the median minimum state of charge."),
        "input_schema": {
            "type": "object",
            "properties": {"tx_duty": {"type": "number", "minimum": 0, "maximum": 1,
                                       "description": "Transmitter on-time fraction, 0 to 1"}},
            "required": ["tx_duty"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


def report_digest(report: dict) -> dict:
    """The report without the long time series, as given to the model."""
    keep = ("scenario", "days", "estimates_now", "alarms", "features_now", "actions")
    digest = {k: report[k] for k in keep if k in report}
    digest["prognosis"] = {k: v for k, v in report.get("prognosis", {}).items()
                           if k not in ("hours", "soc_p10", "soc_p50", "soc_p90")}
    w = report.get("windows", {})
    if w.get("fault_probability"):
        digest["latest_fault_probability"] = w["fault_probability"][-1]
        digest["latest_class_probabilities"] = {k: v[-1] for k, v in w["probabilities"].items()}
    return digest


def template_explanation(report: dict) -> str:
    """Deterministic explanation from the report, used without a model."""
    alarms = {a["method"]: a for a in report["alarms"]}
    hyb = alarms.get("twin_hybrid", {})
    est, prog = report["estimates_now"], report["prognosis"]
    lines = []
    if hyb.get("hour") is None:
        lines.append(f"No anomaly detected by the twin after {report['days']:g} days of telemetry.")
    else:
        names = {"string_loss": "loss of a solar cell string", "array_degradation":
                 "gradual solar array degradation", "capacity_fade": "battery capacity fade",
                 "load_anomaly": "an unexpected extra load"}
        lines.append(f"The twin raised an alarm at T+{hyb['hour']:.1f} h on the {hyb['subsystem']} "
                     f"subsystem; the classifier diagnoses {names.get(hyb['fault'], hyb['fault'])}.")
    lim = alarms.get("limit", {})
    lines.append("Limit checking has not triggered." if lim.get("hour") is None
                 else f"Limit checking triggered at T+{lim['hour']:.1f} h on {lim['channel']}.")
    lines.append(f"Estimates now: array factor {est['array_factor']:.2f} ± {est['array_factor_sd']:.2f}, "
                 f"battery capacity {est['capacity_wh']:.1f} ± {est['capacity_sd']:.1f} Wh "
                 f"(datasheet {est['capacity_datasheet_wh']:g} Wh), extra load {round(est['load_bias_w'], 2) + 0.0:.2f} W.")
    if prog["p_safe_mode"] > 0:
        lines.append(f"Forecast: {100 * prog['p_safe_mode']:.0f} % probability of safe mode within "
                     f"{prog['horizon_days']:g} days, median in {prog['safe_mode_h_p50']:.0f} h.")
    else:
        lines.append(f"Forecast: no safe mode expected within {prog['horizon_days']:g} days.")
    rec = next((a for a in report.get("actions", []) if a["recommended"]), None)
    if rec and rec["tx_duty"] != report["actions"][0]["tx_duty"]:
        lines.append(f"Recommended action: reduce the transmitter duty cycle to {rec['tx_duty']:g} "
                     f"(risk {100 * rec['p_safe_mode']:.0f} %).")
    return " ".join(lines)


def explain(report: dict, question: str, what_if, client=None) -> dict:
    """Ask the model to explain the report. what_if(tx_duty) -> dict runs the
    twin's forecast. Returns {"text", "source", "tool_calls"}."""
    import anthropic

    client = client or anthropic.Anthropic()

    messages = [{"role": "user", "content": (
        f"Health report from the digital twin:\n```json\n{json.dumps(report_digest(report))}\n```\n\n"
        f"Operator question: {question}")}]
    calls = []
    try:
        for _ in range(MAX_ROUNDS):
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                return {"text": template_explanation(report), "source": "template", "tool_calls": calls}
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                return {"text": text or template_explanation(report), "source": "model",
                        "tool_calls": calls}
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "run_what_if" and isinstance(block.input.get("tx_duty"), (int, float)):
                    out = what_if(float(block.input["tx_duty"]))
                    calls.append({"tool": block.name, "input": block.input, "output": out})
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": json.dumps(out)})
                else:
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": "unknown tool or invalid input", "is_error": True})
            messages.append({"role": "user", "content": results})
    except TypeError:
        # The SDK found no credential (no API key, token or `ant auth login` profile).
        return {"text": template_explanation(report), "source": "template", "tool_calls": calls}
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.APIConnectionError):
        return {"text": template_explanation(report), "source": "template", "tool_calls": calls}
    return {"text": template_explanation(report), "source": "template", "tool_calls": calls}
