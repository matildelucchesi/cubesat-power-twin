import { HttpClient } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { LineChart, Series } from './line-chart';

export interface RealPass {
  id: number; start: string; minutes: number; eclipse_minutes: number; validation: boolean; i0: number; i1: number;
}

export interface RealReport {
  summary: Record<string, number | string | null>;
  passes: RealPass[];
  series: {
    start: string; hours: number[];
    p_array_w: number[]; p_twin_w: number[]; p_twin_synced_w: number[]; p_load_w: number[];
    v_batt_v: number[]; v_pred_synced_v: (number | null)[]; v_pred_datasheet_v: (number | null)[];
    lit_meas: boolean[]; twin_sunlit: boolean[];
    array_factor: number[]; array_factor_sd: number[]; battery_r_ohm: number[]; battery_capacity_ah: (number | null)[];
    load_sun_w: number[]; load_eclipse_w: number[];
  };
}

interface Card {
  label: string; value: string; unit: string; note: string; chips: { text: string; tone?: 'ok' | 'warn' | 'bad' }[];
}

/** Replace nulls with the previous value so a polyline stays continuous. */
function fill(v: (number | null)[]): number[] {
  let last = NaN;
  return v.map((x) => (x === null ? last : (last = x)));
}

/**
 * Real-data mode: the twin compared with, and synchronised to, the telemetry
 * FUNcube-1 (AO-73) actually sent, as received by SatNOGS ground stations.
 */
@Component({
  selector: 'app-real-panel',
  imports: [DatePipe, FormsModule, LineChart],
  styleUrls: ['./app.css'],
  template: `
<div class="body">
  <aside class="side">
    <h2>Real spacecraft</h2>
    <p class="hint">FUNcube-1 (AO-73), a 1U CubeSat launched in 2013 and still transmitting. Its orbit is the default orbit of the simulated mode.</p>
    <p class="hint">Telemetry: whole-orbit buffer (one sample per minute, the last 104 minutes) decoded from the raw frames that SatNOGS Network stations uploaded. Public data, no account needed.</p>
    @if (report(); as r) {
      <p class="hint">{{ r.summary['minutes'] }} minutes in {{ r.summary['passes'] }} passes, {{ r.series.start | date: 'MMM d' }} to {{ r.summary['last_sample'] | date: 'MMM d, y' }}.</p>
      <label for="pass">Pass to compare
        <select id="pass" name="pass" [ngModel]="passId()" (ngModelChange)="passId.set(+$event)">
          @for (p of longPasses(); track p.id) {
            <option [value]="p.id">{{ p.start | date: 'MMM d, HH:mm' : 'UTC' }} · {{ p.minutes }} min{{ p.validation ? ' · validation' : '' }}</option>
          }
        </select>
        <span class="hint">Passes marked "validation" come after the calibration period: there the twin predicts the battery voltage from the orbit alone.</span>
      </label>
    }
    <span class="hint">Refresh the data with <code>python src/cubesat_twin/satnogs.py --since 2026-11-10</code>.</span>
    @if (error()) { <p class="error">{{ error() }}</p> }
  </aside>

  @if (report(); as r) {
    <main>
      <section>
        <h2>Is the twin right? Twin vs FUNcube-1</h2>
        <div class="cards">
          @for (c of checkCards(); track c.label) {
            <article class="card">
              <span class="k">{{ c.label }}</span>
              <span class="v">{{ c.value }}<small>{{ c.unit }}</small></span>
              <code class="eq">{{ c.note }}</code>
              <span class="chips">@for (ch of c.chips; track ch.text) { <span class="chip" [class]="ch.tone ?? ''">{{ ch.text }}</span> }</span>
            </article>
          }
        </div>
      </section>

      <section>
        <h2>What the twin estimates (not measured on board)</h2>
        <div class="cards">
          @for (c of estimateCards(); track c.label) {
            <article class="card">
              <span class="k">{{ c.label }}</span>
              <span class="v">{{ c.value }}<small>{{ c.unit }}</small></span>
              <code class="eq">{{ c.note }}</code>
              <span class="chips">@for (ch of c.chips; track ch.text) { <span class="chip" [class]="ch.tone ?? ''">{{ ch.text }}</span> }</span>
            </article>
          }
        </div>
      </section>

      @if (pass(); as p) {
        <section class="charts">
          <h2>Pass of {{ p.start | date: 'MMM d, HH:mm' : 'UTC' }} UTC</h2>
          <div class="chart-block">
            <h3>Solar array power</h3>
            <app-line-chart [x]="passX()" [series]="powerSeries()" [bands]="passLit()" yLabel="W" xLabel="time from start of pass [h]" title="Array power" />
          </div>
          <div class="chart-block">
            <h3>Battery voltage{{ p.validation ? ', predicted from the orbit alone' : '' }}</h3>
            <app-line-chart [x]="passX()" [series]="voltageSeries()" [bands]="passLit()" [yFit]="true" yLabel="V" xLabel="time from start of pass [h]" title="Battery voltage" />
            @if (!p.validation) { <p class="cap">Calibration pass: the filters learn from it, so no prediction is shown. Pick a validation pass to see the prediction.</p> }
          </div>
        </section>
      }

      <section class="charts">
        <h2>Kalman estimates over the whole period</h2>
        <div class="two">
          <div class="chart-block">
            <h3>Array factor: measured over as-designed power</h3>
            <app-line-chart [x]="days()" [series]="factorSeries()" [yFit]="true" yLabel="factor" xLabel="days" title="Array factor" />
          </div>
          <div class="chart-block">
            <h3>Battery capacity (typical Li-ion curve)</h3>
            <app-line-chart [x]="days()" [series]="capacitySeries()" [yFit]="true" yLabel="mAh" xLabel="days" title="Capacity" />
          </div>
        </div>
      </section>
    </main>
  } @else {
    <main><p class="empty">{{ loading() ? 'Loading FUNcube-1 telemetry and aligning the twin…' : '' }}</p></main>
  }
</div>
  `,
})
export class RealPanel {
  private readonly http = inject(HttpClient);
  readonly report = signal<RealReport | null>(null);
  readonly loading = signal(true);
  readonly error = signal('');
  readonly passId = signal<number | null>(null);

  constructor() {
    this.http.get<RealReport>('/api/real').subscribe({
      next: (r) => {
        this.report.set(r);
        const best = r.passes.filter((p) => p.validation).sort((a, b) => b.eclipse_minutes - a.eclipse_minutes || b.minutes - a.minutes)[0];
        this.passId.set(best?.id ?? r.passes[0].id);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.status === 0 ? 'Backend not reachable. Start it with: uvicorn api:app --app-dir src/cubesat_twin'
          : `The backend returned an error (${e?.status}).`);
        this.loading.set(false);
      },
    });
  }

  readonly longPasses = computed(() => (this.report()?.passes ?? []).filter((p) => p.minutes >= 60));
  readonly pass = computed(() => this.report()?.passes.find((p) => p.id === this.passId()) ?? null);

  private slice<T>(v: T[]): T[] {
    const p = this.pass();
    return p ? v.slice(p.i0, p.i1) : [];
  }

  readonly passX = computed(() => {
    const xs = this.slice(this.report()?.series.hours ?? []);
    return xs.map((x) => x - xs[0]);
  });
  readonly passLit = computed(() => this.slice(this.report()?.series.twin_sunlit ?? []));

  readonly powerSeries = computed<Series[]>(() => {
    const s = this.report()?.series;
    if (!s) return [];
    const af = Number(this.report()!.summary['array_factor']).toFixed(2);
    return [
      { label: 'FUNcube-1 telemetry', values: this.slice(s.p_array_w), color: 'var(--text)', unit: 'W' },
      { label: 'Generic twin (as designed)', values: this.slice(s.p_twin_w), color: 'var(--sun)', unit: 'W' },
      { label: `Synchronised twin (× ${af})`, values: this.slice(s.p_twin_synced_w), color: 'var(--accent)', unit: 'W' },
    ];
  });

  readonly voltageSeries = computed<Series[]>(() => {
    const s = this.report()?.series;
    const p = this.pass();
    if (!s || !p) return [];
    const out: Series[] = [{ label: 'FUNcube-1 telemetry', values: this.slice(s.v_batt_v), color: 'var(--text)', unit: 'V' }];
    if (p.validation) {
      out.push({ label: 'Generic twin (datasheet)', values: fill(this.slice(s.v_pred_datasheet_v)), color: 'var(--sun)', unit: 'V' });
      out.push({ label: 'Synchronised twin', values: fill(this.slice(s.v_pred_synced_v)), color: 'var(--accent)', unit: 'V' });
    }
    return out;
  });

  readonly days = computed(() => (this.report()?.series.hours ?? []).map((h) => h / 24));

  readonly factorSeries = computed<Series[]>(() => {
    const s = this.report()?.series;
    if (!s) return [];
    // Hold the first converged value over the first hours, while the filter settles.
    const k0 = Math.max(0, s.hours.findIndex((h) => h >= 6));
    const held = (v: number[]) => v.map((x, i) => (i < k0 ? v[k0] : x));
    const af = held(s.array_factor), sd = held(s.array_factor_sd);
    return [
      { label: 'Estimate', values: af, color: 'var(--accent)' },
      { label: '+2σ', values: af.map((v, i) => v + 2 * sd[i]), color: 'var(--shade)' },
      { label: '−2σ', values: af.map((v, i) => v - 2 * sd[i]), color: 'var(--shade)' },
    ];
  });

  readonly capacitySeries = computed<Series[]>(() => {
    const s = this.report()?.series;
    if (!s) return [];
    // Skip the first hours, while the filter is still converging.
    const vals = fill(s.battery_capacity_ah).map((v, i) => (s.hours[i] < 6 ? NaN : v * 1000));
    const first = vals.find((v) => !isNaN(v)) ?? 0;
    return [{ label: 'Capacity estimate', values: vals.map((v) => (isNaN(v) ? first : v)), color: 'var(--good)', unit: 'mAh' }];
  });

  readonly checkCards = computed<Card[]>(() => {
    const s = this.report()?.summary;
    if (!s) return [];
    const n = (k: string) => Number(s[k]);
    return [
      { label: 'Eclipse duration', value: n('eclipse_measured_min').toFixed(1), unit: 'min',
        note: `twin from the TLE: ${n('eclipse_predicted_min').toFixed(1)} min (${s['eclipses_timed']} eclipses)`,
        chips: [{ text: `${(n('sunlit_agreement') * 100).toFixed(1)} % of minutes agree on sun/shadow`, tone: 'ok' }] },
      { label: 'Eclipse edges', value: `±${n('eclipse_edge_scatter_s').toFixed(0)}`, unit: 's',
        note: `scatter of ${s['eclipse_edges']} edges, 1-min samples; clock offset ${n('time_offset_s').toFixed(0)} s corrected`,
        chips: [{ text: 'orbit and shadow model confirmed', tone: 'ok' }] },
      { label: 'Battery voltage error', value: n('v_rmse_synced_mv').toFixed(0), unit: 'mV',
        note: `RMS over ${s['validation_minutes']} min not used for calibration; generic twin ${n('v_rmse_datasheet_mv').toFixed(0)} mV`,
        chips: [{ text: `${(n('v_rmse_datasheet_mv') / n('v_rmse_synced_mv')).toFixed(1)}× better than datasheet`, tone: 'ok' }] },
      { label: 'Lowest voltage per eclipse', value: `±${n('eclipse_min_err_synced_mv').toFixed(0)}`, unit: 'mV',
        note: `mean error over ${s['eclipses_validated']} eclipses; generic twin ±${n('eclipse_min_err_datasheet_mv').toFixed(0)} mV`,
        chips: [{ text: 'what an operator watches' }] },
    ];
  });

  readonly estimateCards = computed<Card[]>(() => {
    const s = this.report()?.summary;
    if (!s) return [];
    const n = (k: string) => Number(s[k]);
    return [
      { label: 'Array factor', value: n('array_factor').toFixed(2), unit: '',
        note: `± ${n('array_factor_sd').toFixed(3)}; ${n('array_power_measured_w').toFixed(2)} W measured vs ${n('array_power_design_w').toFixed(2)} W designed in sunlight`,
        chips: [{ text: 'design, attitude and 13 years of ageing in one number', tone: 'warn' }] },
      { label: 'Loads', value: n('load_sun_w').toFixed(2), unit: 'W',
        note: `in sunlight; ${n('load_eclipse_w').toFixed(2)} W in eclipse; twin assumes ${n('load_datasheet_w').toFixed(2)} W`,
        chips: [{ text: 'beacon off in eclipse' }] },
      { label: 'Battery capacity', value: (n('battery_capacity_ah') * 1000).toFixed(0), unit: 'mAh',
        note: `${n('battery_k_v_per_ah').toFixed(2)} V per Ah drawn, typical Li-ion curve; twin datasheet ${s['battery_capacity_datasheet_wh']} Wh`,
        chips: [{ text: `deepest eclipse uses ${(n('max_deficit_ah') * 1000).toFixed(0)} mAh`, tone: 'ok' }] },
      { label: 'Internal resistance', value: (n('battery_r_ohm') * 1000).toFixed(0), unit: 'mΩ',
        note: `full-charge voltage ${n('battery_v_full_v').toFixed(2)} V (charger limit)`,
        chips: [{ text: 'V = V_full − k·D + R·I' }] },
    ];
  });
}
