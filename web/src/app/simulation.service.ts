import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export type Face = '+X' | '-X' | '+Y' | '-Y' | '+Z' | '-Z';

export type FormFactor = '1U' | '3U' | '6U';

export interface SimulationRequest {
  norad: number;
  form_factor: FormFactor;
  deployables: boolean;
  days: number;
  step_s: number;
  attitude: 'tumbling' | 'sun_pointing';
  failed_faces: Face[];
  capacity_wh: number;
  initial_soc: number;
  tx_duty_cycle: number;
  eclipse_model: 'skyfield' | 'cylindrical';
  safe_mode: boolean;
  safe_enter_soc: number;
  safe_exit_soc: number;
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
  safe_fraction: number;
  safe_entries: number;
}

export interface Spacecraft {
  norad: number;
  tle_source: 'celestrak' | 'cache' | 'bundled';
  tle_epoch: string;
  inclination_deg: number;
  form_factor: FormFactor;
  size_u: number[];
  deployables: boolean;
  cell_area_m2: number;
}

export interface Catalog {
  satellites: { norad: number; name: string; orbit: string }[];
  form_factors: Record<FormFactor, { size_u: number[]; capacity_wh: number; deployable: number }>;
}

export interface SimulationResult {
  summary: SimulationSummary;
  spacecraft?: Spacecraft;
  series: { hours: number[]; power_w: number[]; load_w: number[]; soc: number[]; sunlit: boolean[]; safe: boolean[] };
  ground_track: { lat: number[]; lon: number[] };
  orbit3d: {
    start: string;
    hours: number[];
    lat_deg: number[];
    lon_deg: number[];
    alt_km: number[];
    x_km: number[];
    y_km: number[];
    z_km: number[];
    sunlit: boolean[];
    power_w: number[];
    soc: number[];
    load_w: number[];
    safe: boolean[];
    sun_dir: [number, number, number];
  };
  eclipses: { start: string; duration_min: number }[];
}

/** Talks to the FastAPI backend (proxied to /api by the dev server). */
@Injectable({ providedIn: 'root' })
export class SimulationService {
  private readonly http = inject(HttpClient);

  catalog(): Observable<Catalog> {
    return this.http.get<Catalog>('/api/catalog');
  }

  simulate(request: SimulationRequest): Observable<SimulationResult> {
    return this.http.post<SimulationResult>('/api/simulate', request);
  }
}
