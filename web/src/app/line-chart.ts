import { Component, computed, input } from '@angular/core';

export interface Series {
  label: string;
  values: number[];
  color: string;
}

/** A series with its own x values, drawn dashed (e.g. a saved baseline). */
export interface Ghost extends Series {
  x: number[];
}

/** Minimal SVG line chart: no chart library needed. */
@Component({
  selector: 'app-line-chart',
  template: `
    <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" role="img" [attr.aria-label]="title()">
      @for (b of bandRects(); track $index) {
        <rect [attr.x]="b.x" [attr.y]="pad.top" [attr.width]="b.w" [attr.height]="height - pad.top - pad.bottom" class="band" />
      }
      @for (tick of yTicks(); track tick) {
        <line [attr.x1]="pad.left" [attr.x2]="width - pad.right"
              [attr.y1]="y(tick)" [attr.y2]="y(tick)" class="grid" />
        <text [attr.x]="pad.left - 6" [attr.y]="y(tick) + 4" text-anchor="end" class="tick">{{ tick }}</text>
      }
      @for (tick of xTicks(); track tick) {
        <text [attr.x]="x(tick)" [attr.y]="height - 8" text-anchor="middle" class="tick">{{ tick }}</text>
      }
      @for (g of ghosts(); track g.label) {
        <polyline [attr.points]="points(g.values, g.x)" [attr.stroke]="g.color" fill="none" stroke-width="1.4" stroke-dasharray="5 4" opacity="0.6" />
      }
      @for (s of series(); track s.label) {
        <polyline [attr.points]="points(s.values)" [attr.stroke]="s.color" fill="none" stroke-width="1.6" />
      }
      @if (cursor() !== null) {
        <line [attr.x1]="x(cursor()!)" [attr.x2]="x(cursor()!)" [attr.y1]="pad.top" [attr.y2]="height - pad.bottom" class="cursor" />
      }
      <text [attr.x]="pad.left" y="14" class="axis">{{ yLabel() }}</text>
      <text [attr.x]="width - pad.right" [attr.y]="height - 8" text-anchor="end" class="axis">{{ xLabel() }}</text>
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
    </div>
  `,
  styles: `
    svg { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--grid); }
    .band { fill: var(--shade); }
    .cursor { stroke: var(--sun); stroke-width: 1.5; }
    .tick { font: 10px var(--f-data); fill: var(--muted); }
    .axis { font: 11px var(--f-data); fill: var(--muted); }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--muted); margin-top: 4px; }
    .legend i.dash { background: none !important; height: 0; border-top: 2px dashed; opacity: 0.7; }
    .legend i.sw { width: 12px; height: 10px; background: var(--shade); }
    .legend i { display: inline-block; width: 12px; height: 3px; margin-right: 6px; vertical-align: middle; }
  `,
})
export class LineChart {
  readonly title = input('');
  readonly x_values = input.required<number[]>({ alias: 'x' });
  readonly series = input.required<Series[]>();
  readonly yMax = input<number | null>(null);
  readonly yLabel = input('');
  readonly xLabel = input('');
  /** Optional sunlit flags, one per x value: eclipses are drawn as shaded bands. */
  readonly bands = input<boolean[] | null>(null);
  readonly ghosts = input<Ghost[]>([]);
  /** Optional replay instant [h], drawn as a vertical line. */
  readonly cursor = input<number | null>(null);

  readonly width = 720;
  readonly height = 240;
  readonly pad = { left: 44, right: 12, top: 24, bottom: 28 };

  private readonly xMax = computed(() => Math.max(...this.x_values(), ...this.ghosts().flatMap((g) => g.x), 1));
  private readonly top = computed(
    () => this.yMax() ?? Math.max(...this.series().flatMap((s) => s.values), ...this.ghosts().flatMap((g) => g.values), 1) * 1.1,
  );

  readonly yTicks = computed(() => {
    const top = this.top();
    const step = niceStep(top / 4);
    return Array.from({ length: Math.floor(top / step) + 1 }, (_, i) => +(i * step).toFixed(2));
  });
  readonly xTicks = computed(() => {
    const step = niceStep(this.xMax() / 6);
    return Array.from({ length: Math.floor(this.xMax() / step) + 1 }, (_, i) => +(i * step).toFixed(1));
  });

  readonly bandRects = computed(() => {
    const lit = this.bands();
    const xs = this.x_values();
    if (!lit) return [];
    const rects: { x: number; w: number }[] = [];
    let start = -1;
    lit.forEach((l, i) => {
      if (!l && start < 0) start = i;
      if ((l || i === lit.length - 1) && start >= 0) {
        const x0 = this.x(xs[start]);
        rects.push({ x: x0, w: Math.max(1, this.x(xs[i]) - x0) });
        start = -1;
      }
    });
    return rects;
  });

  x(v: number): number {
    return this.pad.left + (v / this.xMax()) * (this.width - this.pad.left - this.pad.right);
  }
  y(v: number): number {
    return this.height - this.pad.bottom - (v / this.top()) * (this.height - this.pad.top - this.pad.bottom);
  }
  points(values: number[], xs: number[] = this.x_values()): string {
    return values.map((v, i) => `${this.x(xs[i]).toFixed(1)},${this.y(v).toFixed(1)}`).join(' ');
  }
}

function niceStep(raw: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
