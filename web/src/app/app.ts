import { Component, computed, effect, inject, signal } from '@angular/core';
import { DatePipe, PercentPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { BatteryGauge } from './battery-gauge';
import { GroundTrack, LiveTrack } from './ground-track';
import { LineChart, Series } from './line-chart';
import { OrbitPlane } from './orbit-plane';
import { OrbitView } from './orbit-view';
import { Playback, RATES } from './playback.service';
import { Face, SimulationRequest, SimulationResult, SimulationService } from './simulation.service';

/** One computed result, shown with the relation that produced it and the model used. */
interface ResultCard {
  label: string;
  value: string;
  unit: string;
  formula: string;
  chips: { text: string; tone?: 'ok' | 'warn' | 'bad' }[];
}

@Component({
  selector: 'app-root',
  imports: [FormsModule, PercentPipe, DatePipe, LineChart, GroundTrack, OrbitView, OrbitPlane, BatteryGauge],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly api = inject(SimulationService);
  readonly playback = inject(Playback);
  readonly rates = RATES;

  readonly faces: Face[] = ['+X', '-X', '+Y', '-Y', '+Z', '-Z'];
  readonly request: SimulationRequest = {
    days: 3,
    step_s: 60,
    attitude: 'tumbling',
    failed_faces: [],
    capacity_wh: 20,
    initial_soc: 0.8,
    tx_duty_cycle: 0.5,
    eclipse_model: 'skyfield',
  };

  readonly result = signal<SimulationResult | null>(null);
  /** Scenario that produced the current result (the form may change after a run). */
  readonly ran = signal<SimulationRequest | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly ranAt = signal<Date | null>(null);

  /** Orbital period from the spacing of consecutive eclipses [min]. */
  private readonly periodMin = computed(() => {
    const e = this.result()?.eclipses ?? [];
    if (e.length < 2) return null;
    return (Date.parse(e[1].start) - Date.parse(e[0].start)) / 60000;
  });

  readonly cards = computed<ResultCard[]>(() => {
    const r = this.result();
    const q = this.ran();
    if (!r || !q) return [];
    const s = r.summary;
    const margin = s.mean_power_w - s.mean_load_w;
    const working = 6 - q.failed_faces.length;
    const T = this.periodMin();
    return [
      {
        label: 'Mean array power', value: s.mean_power_w.toFixed(2), unit: 'W',
        formula: q.attitude === 'tumbling' ? 'P̄ = S·η·(A/4)·(1 − f_ecl)' : 'P̄ = S·η·A_face·(1 − f_ecl)',
        chips: [{ text: q.attitude === 'tumbling' ? 'tumbling' : 'sun pointing' },
                { text: `${working}/6 faces`, tone: working < 6 ? 'warn' : undefined }],
      },
      {
        label: 'Mean load', value: s.mean_load_w.toFixed(2), unit: 'W',
        formula: 'P̄_load = Σ Pᵢ · dutyᵢ',
        chips: [{ text: `TX ${Math.round(q.tx_duty_cycle * 100)} %` }],
      },
      {
        label: 'Energy margin', value: (margin >= 0 ? '+' : '') + margin.toFixed(2), unit: 'W',
        formula: 'P̄_array − P̄_load',
        chips: [{ text: margin >= 0 ? 'positive' : 'negative', tone: margin >= 0 ? 'ok' : 'bad' }],
      },
      {
        label: 'Time in shadow', value: (s.shadow_fraction * 100).toFixed(1), unit: '%',
        formula: `${s.eclipses} eclipses / ${s.days} d`,
        chips: [{ text: s.eclipse_model }],
      },
      {
        label: 'Mean eclipse', value: s.mean_eclipse_min.toFixed(1), unit: 'min',
        formula: T ? `per orbit of T = ${T.toFixed(1)} min` : 'complete eclipses only',
        chips: [{ text: s.eclipse_model }],
      },
      {
        label: 'Minimum state of charge', value: (s.min_soc * 100).toFixed(1), unit: '%',
        formula: `min SOC(t), C = ${q.capacity_wh} Wh`,
        chips: [{
          text: s.min_soc < 0.3 ? 'below 30 %' : s.min_soc < 0.6 ? 'below 60 %' : 'healthy',
          tone: s.min_soc < 0.3 ? 'bad' : s.min_soc < 0.6 ? 'warn' : 'ok',
        }],
      },
    ];
  });

  readonly socSeries = computed<Series[]>(() => {
    const r = this.result();
    return r ? [{ label: 'State of charge', values: r.series.soc.map((s) => s * 100), color: 'var(--accent)' }] : [];
  });
  readonly powerSeries = computed<Series[]>(() => {
    const r = this.result();
    return r
      ? [
          { label: 'Solar array', values: r.series.power_w, color: 'var(--sun)' },
          { label: 'Load', values: r.series.load_w, color: 'var(--text)' },
        ]
      : [];
  });

  /** Values at the current replay instant: drives the telemetry strip, map, battery and 3D view. */
  readonly now = computed(() => {
    const r = this.result();
    if (!r) return null;
    const o = r.orbit3d;
    const i = Math.min(this.playback.index(), o.hours.length - 1);
    const h = o.hours[i] ?? 0;
    const t = new Date(Date.parse(o.start) + h * 3600_000);

    // Time to the next sunlight/eclipse change, searched over the whole run.
    const s = r.series;
    let k = s.hours.findIndex((x) => x >= h);
    if (k < 0) k = s.hours.length - 1;
    let j = k;
    while (j < s.hours.length && s.sunlit[j] === s.sunlit[k]) j++;
    const nextMin = j < s.hours.length ? (s.hours[j] - h) * 60 : null;

    return {
      i,
      last: o.hours.length - 1,
      met: `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.floor((h % 1) * 60)).padStart(2, '0')}`,
      utc: t.toISOString().slice(11, 16),
      lit: o.sunlit[i],
      soc: o.soc[i],
      power: o.power_w[i],
      net: o.power_w[i] - r.summary.mean_load_w,
      lat: o.lat_deg[i],
      lon: o.lon_deg[i],
      alt: o.alt_km[i],
      next: nextMin === null ? '—' : `${String(Math.floor(nextMin)).padStart(2, '0')}:${String(Math.round((nextMin % 1) * 60) % 60).padStart(2, '0')}`,
      nextLabel: o.sunlit[i] ? 'Eclipse in' : 'Sunlight in',
      t,
    };
  });

  /** Map overlay: satellite, 30-minute trail, sub-solar point. */
  readonly live = computed<LiveTrack | null>(() => {
    const r = this.result();
    const n = this.now();
    if (!r || !n) return null;
    const o = r.orbit3d;
    const from = Math.max(0, n.i - 30);
    const [sx, sy, sz] = o.sun_dir;
    // Sub-solar longitude = Sun right ascension - Greenwich sidereal time.
    const jd = n.t.getTime() / 86400_000 + 2440587.5;
    const gmst = 280.46061837 + 360.98564736629 * (jd - 2451545.0);
    return {
      lat: n.lat,
      lon: n.lon,
      altKm: n.alt,
      trailLat: o.lat_deg.slice(from, n.i + 1),
      trailLon: o.lon_deg.slice(from, n.i + 1),
      sunLat: (Math.asin(sz / Math.hypot(sx, sy, sz)) * 180) / Math.PI,
      sunLon: (Math.atan2(sy, sx) * 180) / Math.PI - gmst,
      lit: n.lit,
    };
  });

  constructor() {
    effect(() => {
      const o = this.result()?.orbit3d;
      if (o && o.hours.length > 1) this.playback.load(o.hours.length, (o.hours[1] - o.hours[0]) * 3600);
    });
    this.run();
  }

  toggleFace(face: Face, failed: boolean): void {
    const set = new Set(this.request.failed_faces);
    failed ? set.add(face) : set.delete(face);
    this.request.failed_faces = [...set];
  }

  run(): void {
    this.loading.set(true);
    this.error.set('');
    const request = structuredClone(this.request);
    this.api.simulate(request).subscribe({
      next: (r) => {
        this.result.set(r);
        this.ran.set(request);
        this.ranAt.set(new Date());
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e.error?.detail ? JSON.stringify(e.error.detail) : 'Backend not reachable. Start it with: uvicorn api:app --app-dir src/cubesat_twin');
        this.loading.set(false);
      },
    });
  }
}
