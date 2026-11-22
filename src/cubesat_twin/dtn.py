"""Store-and-forward data flow, coupled with the power budget.

The spacecraft produces data all the time but can only send it while a
ground station is in view. A DTN node bridges the gaps: it packs the data in
*bundles* (RFC 9171), keeps them in on-board storage and forwards them during
the next contact. Each bundle carries a lifetime; one still on board when its
lifetime ends is discarded.

On a small CubeSat the transmitter is one of the largest loads, so the data
flow and the battery depend on each other:

* the transmitter draws power only while it is actually sending bundles;
* in safe mode the transmitter and the payload are off, so no science data is
  produced and nothing is sent until the battery recovers;
* with the `energy_aware` policy the spacecraft also skips a contact when the
  state of charge is below `tx_min_soc`, keeping a margin before safe mode.

The simulation is a closed loop solved step by step:
measure SOC -> choose mode -> generate, expire, transmit bundles -> apply load
-> update stored energy.
"""
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from loads import SHED_IN_SAFE_MODE
from safe_mode import SafeModeController

POLICIES = ("energy_aware", "always")


@dataclass
class Bundle:
    created_s: float        # creation time, seconds from the simulation start
    size: int               # bytes
    kind: str               # housekeeping | payload
    expires_s: float
    log_index: int = -1     # row of this bundle in the simulation log


@dataclass
class BundleStore:
    """On-board bundle storage, served oldest first."""
    capacity: int
    queue: deque = field(default_factory=deque)
    used: int = 0

    def add(self, b: Bundle) -> bool:
        if self.used + b.size > self.capacity:
            return False  # storage full: the new bundle is dropped
        self.queue.append(b)
        self.used += b.size
        return True

    def expire(self, now_s: float) -> list[Bundle]:
        expired = [b for b in self.queue if b.expires_s <= now_s]
        if expired:
            self.queue = deque(b for b in self.queue if b.expires_s > now_s)
            self.used -= sum(b.size for b in expired)
        return expired

    def pop(self) -> Bundle:
        b = self.queue.popleft()
        self.used -= b.size
        return b


def simulate_dtn(power_in_w, station_idx, step_s: float, loads: dict, battery: dict,
                 dtn: dict, safe: dict | None = None):
    """Closed-loop simulation of battery, safe mode and bundle flow.

    power_in_w: generated power at each step [W].
    station_idx: station in view at each step, -1 when there is none.
    Returns (series, bundles): a DataFrame with one row per step and a
    DataFrame with one row per bundle (its fate and delivery latency).
    """
    policy = dtn.get("policy", "energy_aware")
    if policy not in POLICIES:
        raise ValueError(f"unknown DTN policy {policy!r}, expected one of {POLICIES}")
    p_in = np.asarray(power_in_w, dtype=float)
    station_idx = np.asarray(station_idx)
    n = len(p_in)

    safe = safe or {}
    ctrl = SafeModeController(safe.get("enter_soc", 0.4), safe.get("exit_soc", 0.6)) \
        if safe.get("enabled") else None
    base_w = sum(v for k, v in loads.items() if k not in SHED_IN_SAFE_MODE)
    payload_w = loads.get("payload", 0.0)
    tx_w = loads.get("radio_tx", 0.0)

    cap = battery["capacity_wh"]
    energy = battery["initial_soc"] * cap
    ch_eff, dis_eff = battery.get("charge_efficiency", 0.9), battery.get("discharge_efficiency", 0.95)
    dt_h = step_s / 3600.0

    bundle_bytes = int(dtn["bundle_bytes"])
    lifetime_s = dtn["bundle_lifetime_h"] * 3600
    link_bytes_step = dtn["downlink_bps"] / 8 * dtn.get("link_efficiency", 1.0) * step_s
    store = BundleStore(int(dtn["storage_bytes"]))
    pending = {"housekeeping": 0.0, "payload": 0.0}  # bytes not yet packed in a bundle
    rates = {"housekeeping": dtn["housekeeping_bps"] / 8, "payload": dtn["payload_bps"] / 8}
    head_sent = 0.0  # bytes of the oldest bundle already sent in the current contact
    log = []

    soc, load, in_safe = np.empty(n), np.empty(n), np.zeros(n, dtype=bool)
    tx_frac, stored, delivered_bytes = np.zeros(n), np.empty(n), np.zeros(n)
    for k in range(n):
        now = k * step_s
        in_safe[k] = ctrl.update(energy / cap) if ctrl else False

        # 1. Data generation: the payload is off in safe mode.
        for kind, rate in rates.items():
            if kind == "payload" and in_safe[k]:
                continue
            pending[kind] += rate * step_s
            while pending[kind] >= bundle_bytes:
                pending[kind] -= bundle_bytes
                b = Bundle(now, bundle_bytes, kind, now + lifetime_s, log_index=len(log))
                log.append({"created_s": now, "kind": kind, "size": bundle_bytes,
                            "fate": "stored" if store.add(b) else "dropped",
                            "delivered_s": np.nan})

        # 2. Bundles whose lifetime ended are discarded.
        for b in store.expire(now):
            log[b.log_index]["fate"] = "expired"
            head_sent = 0.0

        # 3. Transmission during a contact.
        can_tx = station_idx[k] >= 0 and not in_safe[k] and store.queue
        if can_tx and policy == "energy_aware":
            can_tx = energy / cap >= dtn.get("tx_min_soc", 0.0)
        if can_tx:
            budget = link_bytes_step
            while store.queue and budget > 0:
                need = store.queue[0].size - head_sent
                if need > budget:
                    head_sent += budget
                    budget = 0
                    break
                budget -= need
                head_sent = 0.0
                b = store.pop()
                done_s = now + step_s * (1 - budget / link_bytes_step)
                log[b.log_index].update(fate="delivered", delivered_s=done_s)
                delivered_bytes[k] += b.size
            tx_frac[k] = 1 - budget / link_bytes_step
        else:
            # A bundle cut off when a contact ends is sent again from the start.
            head_sent = 0.0

        # 4. Power and battery.
        load[k] = base_w + (0.0 if in_safe[k] else payload_w) + tx_w * tx_frac[k]
        net = p_in[k] - load[k]
        energy += net * ch_eff * dt_h if net >= 0 else net / dis_eff * dt_h
        energy = min(max(energy, 0.0), cap)
        soc[k] = energy / cap
        stored[k] = store.used

    series = pd.DataFrame({"soc": soc, "load_w": load, "safe": in_safe, "tx_frac": tx_frac,
                           "stored_bytes": stored, "delivered_bytes": delivered_bytes})
    bundles = pd.DataFrame(log, columns=["created_s", "kind", "size", "fate", "delivered_s"])
    bundles["latency_h"] = (bundles.delivered_s - bundles.created_s) / 3600
    return series, bundles


def dtn_summary(series: pd.DataFrame, bundles: pd.DataFrame, step_s: float,
                tx_power_w: float) -> dict:
    """Key figures of the data flow."""
    fates = bundles.fate.value_counts().to_dict()
    created = len(bundles)
    delivered = bundles[bundles.fate == "delivered"]
    tx_wh = float(series.tx_frac.sum()) * step_s / 3600 * tx_power_w
    delivered_kb = float(delivered["size"].sum()) / 1000
    return {
        "bundles_created": created,
        "bundles_delivered": fates.get("delivered", 0),
        "bundles_expired": fates.get("expired", 0),
        "bundles_dropped": fates.get("dropped", 0),
        "bundles_on_board": fates.get("stored", 0),
        "delivery_ratio": round(fates.get("delivered", 0) / created, 3) if created else 0.0,
        "delivered_kb": round(delivered_kb, 1),
        "median_latency_h": round(float(delivered.latency_h.median()), 2) if len(delivered) else None,
        "p95_latency_h": round(float(delivered.latency_h.quantile(0.95)), 2) if len(delivered) else None,
        "max_stored_kb": round(float(series.stored_bytes.max()) / 1000, 1),
        "tx_energy_wh": round(tx_wh, 2),
        "kb_per_tx_wh": round(delivered_kb / tx_wh, 1) if tx_wh > 0 else None,
    }
