import { Component, computed, input, signal } from '@angular/core';

export interface Series {
  label: string;
  values: number[];
  color: string;
  unit?: string;
}

/** A series with its own x values, drawn dashed (e.g. a saved baseline). */
export interface Ghost extends Series {
  x: number[];
}

/**
 * SVG line chart with an optional time window (zoom), eclipse bands,
 * a replay cursor and a hover read-out. No chart library needed.
 */
@Component({
  selector: 'app-line-chart',
  template: `
    <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" role="img" [attr.aria-label]="title()"
         (mousemove)="hover($event)" (mouseleave)="hoverX.set(null)">
      <defs>
        <clipPath [attr.id]="clipId"><rect [attr.x]="pad.left" [attr.y]="pad.top" [attr.width]="plotW" [attr.height]="plotH" /></clipPath>
      </defs>
      <g [attr.clip-path]="'url(#' + clipId + ')'">
        @for (b of bandRects(); track $index) {
          <rect [attr.x]="b.x" [attr.y]="pad.top" [attr.width]="b.w" [attr.height]="plotH" class="band" />
        }
        @for (b of safeRects(); track $index) {
          <rect [attr.x]="b.x" [attr.y]="height - pad.bottom - 6" [attr.width]="b.w" height="6" class="safe" />
        }
      </g>
      @for (tick of yTicks(); track tick) {
        <line [attr.x1]="pad.left" [attr.x2]="width - pad.right" [attr.y1]="y(tick)" [attr.y2]="y(tick)" class="grid" />
        <text [attr.x]="pad.left - 6" [attr.y]="y(tick) + 4" text-anchor="end" class="tick">{{ tick }}</text>
      }
      @for (tick of xTicks(); track tick) {
        <line [attr.x1]="x(tick)" [attr.x2]="x(tick)" [attr.y1]="height - pad.bottom" [attr.y2]="height - pad.bottom + 4" class="axisline" />
        <text [attr.x]="x(tick)" [attr.y]="height - pad.bottom + 16" text-anchor="middle" class="tick">{{ tick }}</text>
      }
      <line [attr.x1]="pad.left" [attr.x2]="width - pad.right" [attr.y1]="height - pad.bottom" [attr.y2]="height - pad.bottom" class="axisline" />
      <g [attr.clip-path]="'url(#' + clipId + ')'">
        @for (g of ghosts(); track g.label) {
          <polyline [attr.points]="points(g.values, g.x)" [attr.stroke]="g.color" fill="none" stroke-width="1.4" stroke-dasharray="5 4" opacity="0.7" />
        }
        @for (s of series(); track s.label) {
          <polyline [attr.points]="points(s.values)" [attr.stroke]="s.color" fill="none" stroke-width="1.8" stroke-linejoin="round" />
        }
        @for (m of marks(); track m.label) {
          <line [attr.x1]="pad.left" [attr.x2]="width - pad.right" [attr.y1]="y(m.y)" [attr.y2]="y(m.y)" [attr.stroke]="m.color" stroke-dasharray="6 4" stroke-width="1.2" />
          <text [attr.x]="width - pad.right - 4" [attr.y]="y(m.y) + (m.below ? 13 : -4)" text-anchor="end" class="mark" [attr.fill]="m.color">{{ m.label }}</text>
        }
        @if (cursor() !== null) {
          <line [attr.x1]="x(cursor()!)" [attr.x2]="x(cursor()!)" [attr.y1]="pad.top" [attr.y2]="height - pad.bottom" class="cursor" />
        }
      </g>
      @if (readout(); as r) {
        <line [attr.x1]="x(r.x)" [attr.x2]="x(r.x)" [attr.y1]="pad.top" [attr.y2]="height - pad.bottom" class="hover" />
        @for (v of r.values; track v.label) {
          <circle [attr.cx]="x(r.x)" [attr.cy]="y(v.value)" r="3.5" [attr.fill]="v.color" class="dot" />
        }
        <g [attr.transform]="'translate(' + r.boxX + ' ' + (pad.top + 4) + ')'">
          <rect [attr.width]="boxW" [attr.height]="18 + r.values.length * 15" class="tip" />
          <text x="8" y="14" class="tip-h">T + {{ r.x.toFixed(2) }} h{{ r.lit === null ? '' : r.lit ? ' · sunlight' : ' · eclipse' }}</text>
          @for (v of r.values; track v.label; let k = $index) {
            <text x="8" [attr.y]="29 + k * 15" class="tip-t"><tspan [attr.fill]="v.color">■</tspan> {{ v.label }}: {{ v.value.toFixed(2) }} {{ v.unit }}</text>
          }
        </g>
      }
      <text [attr.x]="pad.left" y="13" class="axis">{{ yLabel() }}</text>
      <text [attr.x]="width - pad.right" [attr.y]="height - 4" text-anchor="end" class="axis">{{ xLabel() }}</text>
    </svg>
    <div class="legend">
      @for (s of series(); track s.label) {
        <span><i [style.background]="s.color"></i>{{ s.label }}</span>
      }
      @for (g of ghosts(); track g.label) {
        <span><i class="dash" [style.border-color]="g.color"></i>{{ g.label }}</span>
      }
      @if (bands()) {
        <span><i class="sw"></i>Eclipse</span>
      }
      @if (safeRects().length) {
        <span><i class="sf"></i>Safe mode active</span>
      }
      @if (cursor() !== null) {
        <span><i class="cur"></i>Replay instant</span>
      }
    </div>
  `,
  styles: `
    :host { display: block; }
    svg { width: 100%; height: auto; display: block; cursor: crosshair; }
    .grid { stroke: var(--grid); }
    .axisline { stroke: var(--muted); stroke-width: 1; }
    .band { fill: var(--shade); }
    .safe { fill: var(--danger); opacity: 0.8; }
    .mark { font: 10px var(--f-data); }
    .cursor { stroke: var(--sun); stroke-width: 1.5; }
    .hover { stroke: var(--text); stroke-width: 1; stroke-dasharray: 2 3; }
    .dot { stroke: var(--panel); stroke-width: 1.5; }
    .tip { fill: var(--panel); stroke: var(--border); }
    .tip-h { font: 600 11px var(--f-data); fill: var(--text); }
    .tip-t { font: 11px var(--f-data); fill: var(--text); }
    .tick { font: 10px var(--f-data); fill: var(--muted); }
    .axis { font: 11px var(--f-data); fill: var(--muted); }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--muted); margin-top: 4px; }
    .legend i { display: inline-block; width: 12px; height: 3px; margin-right: 6px; vertical-align: middle; }
    .legend i.dash { background: none !important; height: 0; border-top: 2px dashed; opacity: 0.7; }
    .legend i.sw { width: 12px; height: 10px; background: var(--shade); }
    .legend i.sf { width: 12px; height: 6px; background: var(--danger); }
    .legend i.cur { width: 2px; height: 12px; background: var(--sun); }
  `,
})
export class LineChart {
  private static next = 0;
  readonly clipId = `lc-clip-${LineChart.next++}`;

  readonly title = input('');
  readonly x_values = input.required<number[]>({ alias: 'x' });
  readonly series = input.required<Series[]>();
  readonly yMax = input<number | null>(null);
  /** Fit the y axis to the visible data instead of starting at zero. */
  readonly yFit = input(false);
  readonly yLabel = input('');
  readonly xLabel = input('');
  /** Optional sunlit flags, one per x value: eclipses are drawn as shaded bands. */
  readonly bands = input<boolean[] | null>(null);
  readonly ghosts = input<Ghost[]>([]);
  /** Optional replay instant [h], drawn as a vertical line. */
  readonly cursor = input<number | null>(null);
  /** Optional safe-mode flags, one per x value: drawn as a red strip on the time axis. */
  readonly safe = input<boolean[] | null>(null);
  /** Horizontal reference lines (e.g. controller thresholds). */
  readonly marks = input<{ y: number; label: string; color: string; below?: boolean }[]>([]);
  /** Visible x range [h]; null shows everything. */
  readonly window = input<[number, number] | null>(null);

  readonly width = 900;
  readonly height = 250;
  readonly pad = { left: 46, right: 14, top: 22, bottom: 34 };
  readonly plotW = this.width - this.pad.left - this.pad.right;
  readonly plotH = this.height - this.pad.top - this.pad.bottom;
  readonly boxW = 230;

  readonly hoverX = signal<number | null>(null);

  private readonly range = computed<[number, number]>(() => {
    const w = this.window();
    if (w) return w;
    const xs = this.x_values();
    return [0, Math.max(xs[xs.length - 1] ?? 1, ...this.ghosts().map((g) => g.x[g.x.length - 1] ?? 0), 1e-6)];
  });

  /** Index range of the samples inside the window (one extra on each side). */
  private readonly visible = computed(() => {
    const [a, b] = this.range();
    const xs = this.x_values();
    let i0 = xs.findIndex((v) => v >= a);
    if (i0 < 0) i0 = xs.length - 1;
    let i1 = xs.findIndex((v) => v > b);
    if (i1 < 0) i1 = xs.length;
    return [Math.max(0, i0 - 1), Math.min(xs.length, i1 + 1)] as const;
  });

  private readonly yRange = computed<[number, number]>(() => {
    const [i0, i1] = this.visible();
    const vals = this.series().flatMap((s) => s.values.slice(i0, i1));
    const [a, b] = this.range();
    for (const m of this.marks()) vals.push(m.y);
    for (const g of this.ghosts()) g.values.forEach((v, k) => { if (g.x[k] >= a && g.x[k] <= b) vals.push(v); });
    let hi = vals.length ? vals.reduce((m, v) => (v > m ? v : m), -Infinity) : 1;
    let lo = vals.length ? vals.reduce((m, v) => (v < m ? v : m), Infinity) : 0;
    if (this.yFit()) {
      const span = Math.max(hi - lo, 1);
      lo = Math.max(0, lo - span * 0.15);
      hi = Math.min(this.yMax() ?? Infinity, hi + span * 0.15);
      const step = niceStep((hi - lo) / 4);
      return [Math.floor(lo / step) * step, Math.ceil(hi / step) * step];
    }
    return [0, this.yMax() ?? Math.max(hi, 1) * 1.1];
  });

  readonly yTicks = computed(() => {
    const [lo, hi] = this.yRange();
    const step = niceStep((hi - lo) / 4);
    const ticks: number[] = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) ticks.push(+t.toFixed(2));
    return ticks;
  });
  readonly xTicks = computed(() => {
    const [a, b] = this.range();
    const step = niceStep((b - a) / 7);
    const ticks: number[] = [];
    for (let t = Math.ceil(a / step) * step; t <= b + 1e-9; t += step) ticks.push(+t.toFixed(2));
    return ticks;
  });

  readonly bandRects = computed(() => this.runs(this.bands(), false));
  readonly safeRects = computed(() => this.runs(this.safe(), true));

  /** Rectangles for the runs of samples where flags[i] === value, inside the window. */
  private runs(flags: boolean[] | null, value: boolean): { x: number; w: number }[] {
    const xs = this.x_values();
    if (!flags) return [];
    const lit = flags.map((f) => f !== value); // reuse the eclipse logic: runs where lit[i] is false
    const [i0, i1] = this.visible();
    const rects: { x: number; w: number }[] = [];
    let start = -1;
    for (let i = i0; i < i1; i++) {
      if (!lit[i] && start < 0) start = i;
      if ((lit[i] || i === i1 - 1) && start >= 0) {
        const x0 = this.x(xs[start]);
        rects.push({ x: x0, w: Math.max(1, this.x(xs[i]) - x0) });
        start = -1;
      }
    }
    return rects;
  }

  readonly readout = computed(() => {
    const hx = this.hoverX();
    if (hx === null) return null;
    const xs = this.x_values();
    if (!xs.length) return null;
    // Nearest sample (xs is sorted).
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] < hx) lo = m; else hi = m; }
    const i = Math.abs(xs[lo] - hx) < Math.abs(xs[hi] - hx) ? lo : hi;
    const px = this.x(xs[i]);
    return {
      x: xs[i],
      lit: this.bands()?.[i] ?? null,
      values: this.series().map((s) => ({ label: s.label, value: s.values[i], color: s.color, unit: s.unit ?? '' })),
      boxX: px + this.boxW + 12 > this.width - this.pad.right ? px - this.boxW - 10 : px + 10,
    };
  });

  hover(ev: MouseEvent): void {
    const svg = ev.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * this.width;
    if (px < this.pad.left || px > this.width - this.pad.right) { this.hoverX.set(null); return; }
    const [a, b] = this.range();
    this.hoverX.set(a + ((px - this.pad.left) / this.plotW) * (b - a));
  }

  x(v: number): number {
    const [a, b] = this.range();
    return this.pad.left + ((v - a) / (b - a)) * this.plotW;
  }
  y(v: number): number {
    const [lo, hi] = this.yRange();
    return this.height - this.pad.bottom - ((v - lo) / (hi - lo)) * this.plotH;
  }
  points(values: number[], xs: number[] = this.x_values()): string {
    const [a, b] = this.range();
    const out: string[] = [];
    for (let i = 0; i < values.length; i++) {
      const prevIn = i > 0 && xs[i - 1] >= a;
      const nextIn = i < values.length - 1 && xs[i + 1] <= b;
      if ((xs[i] >= a && xs[i] <= b) || (xs[i] < a && nextIn) || (xs[i] > b && prevIn)) {
        out.push(`${this.x(xs[i]).toFixed(1)},${this.y(values[i]).toFixed(1)}`);
      }
    }
    return out.join(' ');
  }
}

function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
