import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type Face = '+X' | '-X' | '+Y' | '-Y' | '+Z' | '-Z';

export interface SimulationRequest {
  days: number;
  step_s: number;
  attitude: 'tumbling' | 'sun_pointing';
  failed_faces: Face[];
  capacity_wh: number;
  initial_soc: number;
  tx_duty_cycle: number;
  eclipse_model: 'skyfield' | 'cylindrical';
}

export interface SimulationSummary {
  satellite: string;
  eclipse_model: string;
  days: number;
  eclipses: number;
  mean_eclipse_min: number;
  shadow_fraction: number;
  mean_power_w: number;
  mean_load_w: number;
  min_soc: number;
  final_soc: number;
}

export interface SimulationResult {
  summary: SimulationSummary;
  series: { hours: number[]; power_w: number[]; load_w: number[]; soc: number[]; sunlit: boolean[] };
  ground_track: { lat: number[]; lon: number[] };
  orbit3d: {
    hours: number[];
    x_km: number[];
    y_km: number[];
    z_km: number[];
    sunlit: boolean[];
    power_w: number[];
    soc: number[];
    sun_dir: [number, number, number];
  };
  eclipses: { start: string; duration_min: number }[];
}

/** Talks to the FastAPI backend (proxied to /api by the dev server). */
@Injectable({ providedIn: 'root' })
export class SimulationService {
  private readonly http = inject(HttpClient);

  simulate(request: SimulationRequest): Observable<SimulationResult> {
    return this.http.post<SimulationResult>('/api/simulate', request);
  }
}
