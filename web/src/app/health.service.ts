import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type FaultKind = 'nominal' | 'string_loss' | 'array_degradation' | 'capacity_fade' | 'load_anomaly';

export interface HealthRequest {
  fault: FaultKind;
  magnitude: number | null;
  onset_h: number;
  days: number;
  horizon_days: number;
  tx_duty: number;
  seed: number;
}

export interface Alarm {
  method: 'limit' | 'twin_cusum' | 'twin_ml' | 'twin_hybrid';
  hour: number | null;
  subsystem: string;
  fault: FaultKind | null;
  channel: string | null;
  delay_h: number | null;
}

export interface Action {
  tx_duty: number;
  p_safe_mode: number;
  safe_mode_h_p50: number | null;
  min_soc_p50: number;
  downlink_relative: number | null;
  recommended: boolean;
}

export interface HealthReport {
  scenario: { kind: FaultKind; onset_h: number | null; magnitude: number; seed: number; failed_face: string | null; subsystem: string };
  days: number;
  series: Record<
    | 'hours' | 'p_array_w' | 'p_twin_w' | 'p_load_w' | 'v_batt_v' | 'soc_est' | 'soc_sd' | 'soc_true'
    | 'capacity_est' | 'capacity_sd' | 'capacity_true' | 'array_factor_est' | 'array_factor_sd'
    | 'array_factor_true' | 'load_bias_est' | 'load_bias_true',
    number[]
  > & { safe: boolean[] };
  windows: { end_h: number[]; fault_probability: number[]; probabilities: Record<FaultKind, number[]> };
  estimates_now: {
    soc: number; soc_sd: number; capacity_wh: number; capacity_sd: number; capacity_datasheet_wh: number;
    array_factor: number; array_factor_sd: number; load_bias_w: number;
    truth: { soc: number; capacity_wh: number; array_factor: number; extra_load_w: number };
  };
  alarms: Alarm[];
  prognosis: {
    horizon_days: number; tx_duty: number; hours: number[]; soc_p10: number[]; soc_p50: number[]; soc_p90: number[];
    threshold_soc: number; p_safe_mode: number; safe_mode_h_p10: number | null; safe_mode_h_p50: number | null;
    safe_mode_h_p90: number | null; actual_safe_mode_h: number | null;
    inputs: { array_factor: number; array_trend_per_day: number; capacity_wh: number; capacity_trend_per_day: number; load_w: number; soc: number };
  };
  actions: Action[];
}

export interface Explanation {
  text: string;
  source: 'model' | 'template';
  tool_calls: { tool: string; input: { tx_duty: number }; output: Record<string, number | null> }[];
}

/** Health monitoring endpoints of the FastAPI backend. */
@Injectable({ providedIn: 'root' })
export class HealthService {
  private readonly http = inject(HttpClient);

  run(request: HealthRequest): Observable<HealthReport> {
    return this.http.post<HealthReport>('/api/health', request);
  }

  explain(request: HealthRequest, question: string): Observable<Explanation> {
    return this.http.post<Explanation>('/api/health/explain', { ...request, question });
  }
}
