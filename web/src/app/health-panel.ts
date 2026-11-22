import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Explanation, FaultKind, HealthReport, HealthRequest, HealthService } from './health.service';
import { LineChart, Series } from './line-chart';

const FAULTS: { kind: FaultKind; label: string; unit: string; step: number; def: number }[] = [
  { kind: 'nominal', label: 'No fault', unit: '', step: 0, def: 0 },
  { kind: 'string_loss', label: 'Lost cell string', unit: 'fraction of one face', step: 0.1, def: 1 },
  { kind: 'array_degradation', label: 'Array degradation', unit: 'output lost per day', step: 0.005, def: 0.03 },
  { kind: 'capacity_fade', label: 'Battery capacity fade', unit: 'capacity lost per day', step: 0.005, def: 0.03 },
  { kind: 'load_anomaly', label: 'Extra load (heater stuck on)', unit: 'W', step: 0.05, def: 0.3 },
];

const METHODS: Record<string, { name: string; how: string }> = {
  limit: { name: 'Limit checking', how: 'fixed limits on battery voltage and load power' },
  twin_cusum: { name: 'Twin + CUSUM', how: 'drift of the twin parameters, cumulative-sum test' },
  twin_ml: { name: 'Twin + ML', how: 'random forest on the twin features, every orbit' },
  twin_hybrid: { name: 'Twin + CUSUM + ML', how: 'CUSUM decides when, the classifier decides what' },
};

/**
 * Health monitoring: synthetic telemetry with an injected fault, the twin kept in
 * sync by Kalman filters, four detection methods side by side, the prognosis and
 * the operator assistant.
 */
@Component({
  selector: 'app-health-panel',
  imports: [FormsModule, LineChart],
  styleUrls: ['./app.css', './health-panel.css'],
  template: `
<div class="body">
  <form class="side" (ngSubmit)="run()">
    <h2>Injected fault</h2>
    <label for="fault">Fault
      <select id="fault" name="fault" [ngModel]="req.fault" (ngModelChange)="setFault($event)">
        @for (f of faults; track f.kind) { <option [value]="f.kind">{{ f.label }}</option> }
      </select>
    </label>
    @if (req.fault !== 'nominal') {
      <label for="mag">Size <span class="hint">{{ fault().unit }}</span>
        <input id="mag" type="number" name="mag" min="0" [step]="fault().step" [(ngModel)]="req.magnitude" />
      </label>
      <label for="onset">Starts at hour
        <input id="onset" type="number" name="onset" min="50" step="6" [(ngModel)]="req.onset_h" />
      </label>
    }
    <span class="hint">The spacecraft is simulated by a truth model the twin does not know: real tumbling, cells below datasheet, noisy sensors. The first 48 h calibrate the twin.</span>

    <h2 class="sub">Operations</h2>
    <label for="hdays">Telemetry received [days]
      <input id="hdays" type="number" name="hdays" min="4" max="20" [(ngModel)]="req.days" />
    </label>
    <label for="horizon">Forecast horizon [days]
      <input id="horizon" type="number" name="horizon" min="1" max="20" [(ngModel)]="req.horizon_days" />
    </label>
    <label for="duty">Transmitter duty cycle <b>{{ (req.tx_duty * 100).toFixed(0) }} %</b>
      <input id="duty" type="range" name="duty" min="0" max="1" step="0.05" [(ngModel)]="req.tx_duty" />
    </label>
    <label for="seed">Random seed
      <input id="seed" type="number" name="seed" min="0" [(ngModel)]="req.seed" />
    </label>
    <button class="run" type="submit" [disabled]="loading()">{{ loading() ? 'Running…' : 'Run health check' }}</button>
    @if (error()) { <p class="error">{{ error() }}</p> }
  </form>

  @if (report(); as r) {
    <main>
      <section>
        <h2>Detection and diagnosis</h2>
        <p class="sc-line">Truth: {{ truthLine() }}</p>
        <div class="cards">
          @for (a of r.alarms; track a.method) {
            <article class="card">
              <span class="k">{{ methods[a.method].name }}</span>
              <span class="v">{{ a.hour === null ? 'no alarm' : 'T+' + a.hour.toFixed(0) }}<small>{{ a.hour === null ? '' : 'h' }}</small></span>
              <span class="hint">{{ methods[a.method].how }}</span>
              <span class="chips">
                @for (c of alarmChips(a); track c.text) { <span class="chip" [class]="c.tone">{{ c.text }}</span> }
              </span>
            </article>
          }
        </div>
      </section>

      <section>
        <h2>What the twin estimates (not measured by any sensor)</h2>
        <div class="cards">
          @for (c of estimateCards(); track c.label) {
            <article class="card">
              <span class="k">{{ c.label }}</span>
              <span class="v">{{ c.value }}<small>{{ c.unit }}</small></span>
              <code class="eq">{{ c.note }}</code>
              <span class="chips"><span class="chip" [class]="c.tone">truth {{ c.truth }}</span></span>
            </article>
          }
        </div>
      </section>

      <section class="charts">
        <div class="chart-block">
          <h3>Array factor: measured over predicted array power (1 = as designed)</h3>
          <app-line-chart [x]="r.series.hours" [series]="arraySeries()" [yFit]="true" yLabel="factor" xLabel="time [h]" title="Array factor" />
        </div>
        <div class="chart-block">
          <h3>Battery capacity</h3>
          <app-line-chart [x]="r.series.hours" [series]="capacitySeries()" [yFit]="true" yLabel="capacity [Wh]" xLabel="time [h]" title="Battery capacity"
                          [marks]="[{ y: r.estimates_now.capacity_datasheet_wh, label: 'datasheet', color: 'var(--muted)' }]" />
        </div>
        <div class="chart-block">
          <h3>Fault probability from the classifier, per orbit</h3>
          <app-line-chart [x]="r.windows.end_h" [series]="probSeries()" [yMax]="100" yLabel="probability [%]" xLabel="time [h]" title="Fault probability"
                          [marks]="[{ y: 80, label: 'alarm level', color: 'var(--danger)' }]" />
        </div>
      </section>

      <section class="charts">
        <h2>Prognosis · next {{ r.prognosis.horizon_days }} days at {{ (r.prognosis.tx_duty * 100).toFixed(0) }} % transmitter duty cycle</h2>
        <div class="chart-block">
          <p class="cap cap-top">{{ prognosisLine() }}</p>
          <app-line-chart [x]="r.prognosis.hours" [series]="forecastSeries()" [yMax]="100" yLabel="state of charge [%]" xLabel="hours from now" title="SOC forecast"
                          [marks]="[{ y: r.prognosis.threshold_soc * 100, label: 'safe mode', color: 'var(--danger)', below: true }]" />
          <p class="cap">Monte Carlo ensemble from the synchronised state and the recent parameter trends. Band: 10th–90th percentile.</p>
        </div>
        <table>
          <thead><tr><th>Transmitter duty cycle</th><th class="num">Downlink</th><th class="num">P(safe mode)</th><th class="num">Median time to safe mode</th><th class="num">Median min SOC</th><th></th></tr></thead>
          <tbody>
            @for (a of r.actions; track a.tx_duty) {
              <tr [class.rec]="a.recommended">
                <td>{{ (a.tx_duty * 100).toFixed(0) }} %</td>
                <td class="num">{{ a.downlink_relative === null ? '-' : (a.downlink_relative * 100).toFixed(0) + ' %' }}</td>
                <td class="num">{{ (a.p_safe_mode * 100).toFixed(0) }} %</td>
                <td class="num">{{ a.safe_mode_h_p50 === null ? 'not reached' : a.safe_mode_h_p50.toFixed(0) + ' h' }}</td>
                <td class="num">{{ (a.min_soc_p50 * 100).toFixed(0) }} %</td>
                <td>{{ a.recommended ? 'recommended' : '' }}</td>
              </tr>
            }
          </tbody>
        </table>
      </section>

      <section>
        <h2>Ask the operator assistant</h2>
        <form class="ask" (ngSubmit)="ask()">
          <input name="q" [(ngModel)]="question" aria-label="Question for the assistant" />
          <button class="ghost" type="submit" [disabled]="asking()">{{ asking() ? 'Thinking…' : 'Ask' }}</button>
        </form>
        @if (answer(); as ans) {
          <div class="answer">
            <p>{{ ans.text }}</p>
            <p class="hint">
              {{ ans.source === 'model' ? 'Written by Claude from the twin report' : 'Template answer (no Anthropic credential configured on the backend)' }}
              @for (t of ans.tool_calls; track $index) { · what-if at {{ (t.input.tx_duty * 100).toFixed(0) }} % duty cycle }
            </p>
          </div>
        }
      </section>
    </main>
  } @else {
    <main><p class="empty">{{ loading() ? 'Generating telemetry, synchronising the twin, running the forecast…' : 'Choose a fault and run the health check.' }}</p></main>
  }
</div>
  `,
})
export class HealthPanel {
  private readonly api = inject(HealthService);
  readonly faults = FAULTS;
  readonly methods = METHODS;

  readonly req: HealthRequest = {
    fault: 'array_degradation', magnitude: 0.03, onset_h: 72, days: 10, horizon_days: 10, tx_duty: 0.5, seed: 7,
  };
  question = 'What is happening to the power system and what should I do?';

  readonly report = signal<HealthReport | null>(null);
  readonly ranWith = signal<HealthRequest | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly answer = signal<Explanation | null>(null);
  readonly asking = signal(false);
  private readonly faultKind = signal<FaultKind>(this.req.fault);
  readonly fault = computed(() => FAULTS.find((f) => f.kind === this.faultKind())!);

  constructor() {
    this.run();
  }

  setFault(kind: FaultKind) {
    this.req.fault = kind;
    this.req.magnitude = FAULTS.find((f) => f.kind === kind)!.def;
    this.faultKind.set(kind);
  }

  run() {
    this.loading.set(true);
    this.error.set('');
    this.answer.set(null);
    const r = { ...this.req };
    this.api.run(r).subscribe({
      next: (rep) => { this.report.set(rep); this.ranWith.set(r); this.loading.set(false); },
      error: (e) => { this.error.set(e?.error?.detail ? JSON.stringify(e.error.detail) : 'Backend not reachable'); this.loading.set(false); },
    });
  }

  ask() {
    const r = this.ranWith();
    if (!r) return;
    this.asking.set(true);
    this.api.explain(r, this.question).subscribe({
      next: (a) => { this.answer.set(a); this.asking.set(false); },
      error: () => { this.asking.set(false); },
    });
  }

  readonly truthLine = computed(() => {
    const s = this.report()?.scenario;
    if (!s) return '';
    if (s.kind === 'nominal') return 'no fault injected.';
    const f = FAULTS.find((x) => x.kind === s.kind)!;
    return `${f.label.toLowerCase()} (${s.magnitude} ${f.unit}${s.failed_face ? ', face ' + s.failed_face : ''}) from T+${s.onset_h} h, subsystem ${s.subsystem}.`;
  });

  alarmChips(a: HealthReport['alarms'][number]) {
    const s = this.report()!.scenario;
    const chips: { text: string; tone?: 'ok' | 'warn' | 'bad' }[] = [];
    if (a.hour === null) {
      chips.push(s.kind === 'nominal' ? { text: 'correct: no fault', tone: 'ok' } : { text: 'missed', tone: 'bad' });
      return chips;
    }
    if (s.kind === 'nominal' || (s.onset_h !== null && a.hour < s.onset_h)) return [{ text: 'false alarm', tone: 'bad' }];
    chips.push({ text: `${a.delay_h} h after onset` });
    chips.push({ text: a.subsystem, tone: a.subsystem === s.subsystem ? 'ok' : 'bad' });
    if (a.fault) chips.push({ text: a.fault.replace('_', ' '), tone: a.fault === s.kind ? 'ok' : 'bad' });
    else chips.push({ text: 'type unknown', tone: 'warn' });
    return chips;
  }

  readonly estimateCards = computed(() => {
    const e = this.report()?.estimates_now;
    if (!e) return [];
    const near = (a: number, b: number, tol: number) => (Math.abs(a - b) <= tol ? 'ok' : 'warn');
    return [
      { label: 'State of charge', value: (e.soc * 100).toFixed(1), unit: '%', note: `± ${(e.soc_sd * 100).toFixed(1)} %, EKF on voltage and current`,
        truth: (e.truth.soc * 100).toFixed(1) + ' %', tone: near(e.soc, e.truth.soc, 2 * e.soc_sd + 0.01) },
      { label: 'Battery capacity', value: e.capacity_wh.toFixed(1), unit: 'Wh', note: `± ${e.capacity_sd.toFixed(1)} Wh, datasheet ${e.capacity_datasheet_wh} Wh`,
        truth: e.truth.capacity_wh.toFixed(1) + ' Wh', tone: near(e.capacity_wh, e.truth.capacity_wh, 2 * e.capacity_sd) },
      { label: 'Array factor', value: e.array_factor.toFixed(2), unit: '', note: `± ${e.array_factor_sd.toFixed(2)}, 1 = as designed`,
        truth: e.truth.array_factor.toFixed(2), tone: near(e.array_factor, e.truth.array_factor, 2 * e.array_factor_sd + 0.03) },
      { label: 'Unexpected load', value: (Math.round(e.load_bias_w * 100) / 100 + 0).toFixed(2).replace('-0.00', '0.00'), unit: 'W', note: 'measured minus expected from the modes',
        truth: e.truth.extra_load_w.toFixed(2) + ' W', tone: near(e.load_bias_w, e.truth.extra_load_w, 0.05) },
    ] as { label: string; value: string; unit: string; note: string; truth: string; tone: 'ok' | 'warn' }[];
  });

  private band(est: number[], sd: number[], k: number) {
    return est.map((v, i) => v + k * sd[i]);
  }

  readonly arraySeries = computed<Series[]>(() => {
    const s = this.report()?.series;
    if (!s) return [];
    return [
      { label: 'Twin estimate', values: s.array_factor_est, color: 'var(--accent)' },
      { label: '+2σ', values: this.band(s.array_factor_est, s.array_factor_sd, 2), color: 'var(--shade)' },
      { label: '−2σ', values: this.band(s.array_factor_est, s.array_factor_sd, -2), color: 'var(--shade)' },
      { label: 'Truth', values: s.array_factor_true, color: 'var(--sun)' },
    ];
  });

  readonly capacitySeries = computed<Series[]>(() => {
    const s = this.report()?.series;
    if (!s) return [];
    return [
      { label: 'Twin estimate', values: s.capacity_est, color: 'var(--accent)', unit: 'Wh' },
      { label: '+2σ', values: this.band(s.capacity_est, s.capacity_sd, 2), color: 'var(--shade)', unit: 'Wh' },
      { label: '−2σ', values: this.band(s.capacity_est, s.capacity_sd, -2), color: 'var(--shade)', unit: 'Wh' },
      { label: 'Truth', values: s.capacity_true, color: 'var(--sun)', unit: 'Wh' },
    ];
  });

  readonly probSeries = computed<Series[]>(() => {
    const w = this.report()?.windows;
    if (!w) return [];
    const colors: Record<string, string> = {
      string_loss: '#7a4fb3', array_degradation: 'var(--sun)', capacity_fade: 'var(--accent)', load_anomaly: 'var(--danger)',
    };
    return Object.entries(w.probabilities)
      .filter(([k]) => k !== 'nominal')
      .map(([k, v]) => ({ label: k.replace('_', ' '), values: v.map((p) => p * 100), color: colors[k], unit: '%' }));
  });

  readonly forecastSeries = computed<Series[]>(() => {
    const p = this.report()?.prognosis;
    if (!p) return [];
    return [
      { label: '90th percentile', values: p.soc_p90.map((v) => v * 100), color: 'var(--shade)', unit: '%' },
      { label: 'Median', values: p.soc_p50.map((v) => v * 100), color: 'var(--accent)', unit: '%' },
      { label: '10th percentile', values: p.soc_p10.map((v) => v * 100), color: 'var(--shade)', unit: '%' },
    ];
  });

  readonly prognosisLine = computed(() => {
    const p = this.report()?.prognosis;
    if (!p) return '';
    const pred = p.p_safe_mode > 0
      ? `Probability of safe mode within the horizon: ${(p.p_safe_mode * 100).toFixed(0)} %, median in ${p.safe_mode_h_p50?.toFixed(0)} h (10–90 %: ${p.safe_mode_h_p10?.toFixed(0)}–${p.safe_mode_h_p90?.toFixed(0)} h).`
      : 'No safe mode expected within the horizon.';
    const actual = p.actual_safe_mode_h === null ? 'In the truth simulation it does not happen.' : `In the truth simulation it happens after ${p.actual_safe_mode_h.toFixed(0)} h.`;
    return `${pred} ${actual}`;
  });
}
