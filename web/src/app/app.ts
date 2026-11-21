import { Component, computed, effect, inject, signal } from '@angular/core';
import { DatePipe, PercentPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { BatteryGauge } from './battery-gauge';
import { GroundTrack, LiveTrack } from './ground-track';
import { Ghost, LineChart, Series } from './line-chart';
import { OrbitPlane } from './orbit-plane';
import { OrbitView } from './orbit-view';
import { Playback, RATES } from './playback.service';
import { Catalog, Face, FormFactor, SimulationRequest, SimulationResult, SimulationService } from './simulation.service';

/** One computed result, shown with the relation that produced it and the model used. */
interface ResultCard {
  label: string;
  value: string;
  unit: string;
  formula: string;
  chips: { text: string; tone?: 'ok' | 'warn' | 'bad' }[];
  /** Change against the baseline, already formatted, and whether it is an improvement. */
  delta?: { text: string; good: boolean | null };
}

/** A saved run to compare against. */
interface Baseline {
  label: string;
  result: SimulationResult;
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
  readonly formFactors: FormFactor[] = ['1U', '3U', '6U'];
  readonly catalog = signal<Catalog | null>(null);
  /** 'preset' shows the list, 'custom' shows a NORAD number field. */
  readonly orbitMode = signal<'preset' | 'custom'>('preset');
  readonly baseline = signal<Baseline | null>(null);

  readonly request: SimulationRequest = {
    norad: 39444,
    form_factor: '1U',
    deployables: false,
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
    const b = this.baseline()?.result.summary;
    const delta = (now: number, then: number | undefined, unit: string, digits: number, higherIsBetter: boolean | null) => {
      if (then === undefined) return undefined;
      const d = now - then;
      if (Math.abs(d) < 0.5 * 10 ** -digits) return { text: `= baseline`, good: null };
      return { text: `${d > 0 ? '+' : '−'}${Math.abs(d).toFixed(digits)} ${unit} vs baseline`, good: higherIsBetter === null ? null : (d > 0) === higherIsBetter };
    };
    const cards: ResultCard[] = [
      {
        label: 'Mean array power', value: s.mean_power_w.toFixed(2), unit: 'W',
        formula: q.attitude === 'tumbling'
          ? `P̄ = S·η·(A${q.deployables ? ' + A_wings' : ''})/4·(1 − f_ecl)`
          : `P̄ = S·η·(A_face${q.deployables ? ' + A_wings' : ''})·(1 − f_ecl)`,
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
        formula: `${s.eclipses} complete eclipses in ${s.days} d`,
        chips: [{ text: this.modelNote() }],
      },
      {
        label: 'Mean eclipse', value: s.mean_eclipse_min.toFixed(1), unit: 'min',
        formula: T ? `per orbit of T = ${T.toFixed(1)} min` : 'complete eclipses only',
        chips: [{ text: this.modelNote() }],
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
    if (b) {
      cards[0].delta = delta(s.mean_power_w, b.mean_power_w, 'W', 2, true);
      cards[1].delta = delta(s.mean_load_w, b.mean_load_w, 'W', 2, false);
      cards[2].delta = delta(margin, b.mean_power_w - b.mean_load_w, 'W', 2, true);
      cards[3].delta = delta(s.shadow_fraction * 100, b.shadow_fraction * 100, 'pt', 1, false);
      cards[4].delta = delta(s.mean_eclipse_min, b.mean_eclipse_min, 'min', 1, false);
      cards[5].delta = delta(s.min_soc * 100, b.min_soc * 100, 'pt', 1, true);
    }
    return cards;
  });

  readonly socGhosts = computed<Ghost[]>(() => {
    const b = this.baseline();
    return b ? [{ label: `Baseline · ${b.label}`, x: b.result.series.hours, values: b.result.series.soc.map((v) => v * 100), color: 'var(--muted)' }] : [];
  });
  readonly powerGhosts = computed<Ghost[]>(() => {
    const b = this.baseline();
    return b ? [{ label: `Baseline array · ${b.label}`, x: b.result.series.hours, values: b.result.series.power_w, color: 'var(--muted)' }] : [];
  });

  /** Chart zoom: 'orbit' = one orbital period, otherwise hours; null shows the whole run. */
  readonly zoom = signal<'orbit' | 6 | 24 | null>('orbit');
  readonly zoomStart = signal(0);
  readonly zoomSpan = computed(() => {
    const z = this.zoom();
    if (z === null) return null;
    return z === 'orbit' ? (this.periodMin() ?? 95) / 60 : z;
  });
  readonly runHours = computed(() => {
    const h = this.result()?.series.hours ?? [];
    return h[h.length - 1] ?? 0;
  });
  readonly chartWindow = computed<[number, number] | null>(() => {
    const span = this.zoomSpan();
    if (span === null || span >= this.runHours()) return null;
    const start = Math.min(this.zoomStart(), this.runHours() - span);
    return [start, start + span];
  });

  setZoom(z: 'orbit' | 6 | 24 | null): void {
    this.zoom.set(z);
    const span = this.zoomSpan();
    if (span !== null) this.zoomStart.set(Math.min(this.zoomStart(), Math.max(0, this.runHours() - span)));
  }

  /** Name of the orbit the CubeSat flies: the preset name, or the TLE name for a custom NORAD number. */
  readonly orbitName = computed(() => {
    const r = this.result();
    if (!r) return 'FUNcube-1 (AO-73)';
    const norad = r.spacecraft?.norad;
    return this.catalog()?.satellites.find((s) => s.norad === norad)?.name ?? r.summary.satellite;
  });

  /** Page title: what is simulated (a CubeSat) and whose orbit it flies. */
  readonly title = computed(() => {
    const q = this.ran();
    const ff = q?.form_factor ?? '1U';
    return `${ff} CubeSat on the ${this.orbitName()} orbit`;
  });

  /** The eclipse model actually used; skyfield falls back to cylindrical without its ephemeris file. */
  readonly modelNote = computed(() => {
    const r = this.result();
    const q = this.ran();
    if (!r || !q) return '';
    return q.eclipse_model === 'skyfield' && r.summary.eclipse_model === 'cylindrical'
      ? 'cylindrical (skyfield ephemeris unavailable)'
      : r.summary.eclipse_model;
  });

  /** Short description of the current run, used as the baseline label. */
  readonly runLabel = computed(() => {
    const r = this.result();
    const q = this.ran();
    if (!r || !q) return '';
    const parts = [`${q.form_factor}${q.deployables ? ' + wings' : ''} on ${this.orbitName()} orbit`, q.attitude === 'tumbling' ? 'tumbling' : 'sun pointing'];
    if (q.failed_faces.length) parts.push(`${q.failed_faces.length} failed`);
    return parts.join(' · ');
  });

  readonly socSeries = computed<Series[]>(() => {
    const r = this.result();
    return r ? [{ label: 'State of charge', values: r.series.soc.map((s) => s * 100), color: 'var(--accent)', unit: '%' }] : [];
  });
  readonly powerSeries = computed<Series[]>(() => {
    const r = this.result();
    return r
      ? [
          { label: 'Solar array', values: r.series.power_w, color: 'var(--sun)', unit: 'W' },
          { label: 'Load', values: r.series.load_w, color: 'var(--text)', unit: 'W' },
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
      elapsed: `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.floor((h % 1) * 60)).padStart(2, '0')}`,
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
    this.api.catalog().subscribe({ next: (c) => this.catalog.set(c), error: () => {} });
    this.run();
  }

  setFormFactor(ff: FormFactor): void {
    this.request.form_factor = ff;
    const preset = this.catalog()?.form_factors[ff];
    if (preset) this.request.capacity_wh = preset.capacity_wh;
  }

  setOrbit(value: string): void {
    if (value === 'custom') {
      this.orbitMode.set('custom');
    } else {
      this.orbitMode.set('preset');
      this.request.norad = Number(value);
    }
  }

  saveBaseline(): void {
    const r = this.result();
    if (r) this.baseline.set({ label: this.runLabel(), result: r });
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
