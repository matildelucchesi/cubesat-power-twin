"""Decoder for the FUNcube-1 (AO-73) telemetry frames.

FUNcube-1 transmits a 256-byte frame every 5 seconds. Each frame holds:

* a 1-byte header: satellite id (2 bits) and frame type (6 bits);
* 55 bytes of *real-time* telemetry: the EPS values at that moment, the
  board temperatures, the radio and software status;
* a 200-byte payload. Frame types 0 to 11 (WO1 to WO12) carry the
  *whole-orbit* buffer, types 12+ carry high-resolution data or fitter
  messages.

The whole-orbit buffer is the interesting part for a power twin: one 23-byte
sample per minute for the last 104 minutes, i.e. a full orbit with its
eclipse, spread over the 12 WO frames (12 x 200 bytes = 104 x 23 bytes plus
an 8-byte callsign). A ground station that hears a 10-minute pass therefore
receives the whole previous orbit.

The bit layout follows the FUNcube telemetry specification as implemented
in gr-satellites (Daniel Estévez). Only the fields the power twin uses are
decoded here. All fields are big-endian, bit-packed.
"""
from dataclasses import dataclass

FRAME_LEN = 256
PAYLOAD_OFFSET = 56            # 1 header byte + 55 bytes of real-time telemetry
PAYLOAD_LEN = 200
WO_SAMPLE_LEN = 23
WO_SAMPLES = 104               # one per minute
WO_PERIOD_S = 60
FRAME_PERIOD_S = 5
SATID_FC1FM = 2                # flight model of FUNcube-1

WO_FIELDS = ("photovoltage_x_mv", "photovoltage_y_mv", "photovoltage_z_mv",
             "photocurrent_ma", "battery_mv", "system_ma", "temp_black_chassis_raw",
             "temp_xp_raw", "temp_xm_raw", "temp_yp_raw", "temp_ym_raw")
RT_FIELDS = ("photovoltage_x_mv", "photovoltage_y_mv", "photovoltage_z_mv",
             "photocurrent_ma", "battery_mv", "system_ma", "eclipse", "safemode",
             "battery_temp_raw")


def bits(data: bytes, offset: int, n: int) -> int:
    """Unsigned integer of n bits starting at bit `offset` (MSB first)."""
    value = 0
    for k in range(offset, offset + n):
        value = (value << 1) | ((data[k >> 3] >> (7 - (k & 7))) & 1)
    return value


@dataclass
class Frame:
    satid: int
    frame_type: int
    realtime: dict
    payload: bytes

    @property
    def wo_chunk(self) -> int | None:
        """Index 1..12 of the whole-orbit chunk carried, or None."""
        return self.frame_type + 1 if self.frame_type < 12 else None


def parse_frame(raw: bytes) -> Frame | None:
    """Parse one 256-byte frame; None if it is not a FUNcube-1 flight-model frame."""
    if len(raw) != FRAME_LEN or raw[0] >> 6 != SATID_FC1FM:
        return None
    rt = raw[1:PAYLOAD_OFFSET]
    realtime = {
        "photovoltage_x_mv": bits(rt, 0, 16),
        "photovoltage_y_mv": bits(rt, 16, 16),
        "photovoltage_z_mv": bits(rt, 32, 16),
        "photocurrent_ma": bits(rt, 48, 16),
        "battery_mv": bits(rt, 64, 16),
        "system_ma": bits(rt, 80, 16),
        "battery_temp_raw": rt[19],
        # Software status block: bits 392-439 of the real-time telemetry.
        "eclipse": bits(rt, 435, 1),
        "safemode": bits(rt, 436, 1),
    }
    return Frame(raw[0] >> 6, raw[0] & 0x3F, realtime,
                 raw[PAYLOAD_OFFSET:PAYLOAD_OFFSET + PAYLOAD_LEN])


def wo_samples(frame: Frame) -> list[tuple[int, dict]]:
    """Whole-orbit samples fully contained in this frame's chunk.

    Returns (index, values) pairs; index 0 is the oldest sample of the
    104-minute buffer and 103 the newest. A sample that straddles two chunks
    is skipped (it is complete in neither frame on its own)."""
    chunk = frame.wo_chunk
    if chunk is None:
        return []
    start = PAYLOAD_LEN * (chunk - 1)               # byte offset of this chunk in the buffer
    first = -(-start // WO_SAMPLE_LEN)              # first sample that starts inside it
    out = []
    i = first
    while (i + 1) * WO_SAMPLE_LEN <= start + PAYLOAD_LEN and i < WO_SAMPLES:
        s = frame.payload[i * WO_SAMPLE_LEN - start:(i + 1) * WO_SAMPLE_LEN - start]
        out.append((i, {
            "temp_black_chassis_raw": bits(s, 0, 12),
            # Side-panel temperatures (raw 10-bit), after the four 12-bit chassis/panel ones.
            "temp_xp_raw": bits(s, 48, 10),
            "temp_xm_raw": bits(s, 58, 10),
            "temp_yp_raw": bits(s, 68, 10),
            "temp_ym_raw": bits(s, 78, 10),
            "photovoltage_x_mv": bits(s, 88, 16),
            "photovoltage_y_mv": bits(s, 104, 16),
            "photovoltage_z_mv": bits(s, 120, 16),
            "photocurrent_ma": bits(s, 136, 16),
            "battery_mv": bits(s, 152, 16),
            "system_ma": bits(s, 168, 16),
        }))
        i += 1
    return out


def wo_sample_time(frame_time_s: float, frame: Frame, index: int) -> float:
    """Approximate UTC time [s] of a whole-orbit sample.

    The WO frames are sent in order, one every 5 s, starting with WO1; the
    newest sample (index 103) is taken as the time of WO1. The exact phase
    of the on-board sampling is unknown to within a minute, which is why
    the twin estimates a constant time offset from the eclipse edges."""
    t_wo1 = frame_time_s - FRAME_PERIOD_S * (frame.wo_chunk - 1)
    return t_wo1 - (WO_SAMPLES - 1 - index) * WO_PERIOD_S
