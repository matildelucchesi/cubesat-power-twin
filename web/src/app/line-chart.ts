import { Component, computed, input } from '@angular/core';

export interface Series {
  label: string;
  values: number[];
  color: string;
}

/** Minimal SVG line chart: no chart library needed. */
@Component({
  selector: 'app-line-chart',
  template: `
    <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" role="img" [attr.aria-label]="title()">
      @for (tick of yTicks(); track tick) {
        <line [attr.x1]="pad.left" [attr.x2]="width - pad.right"
              [attr.y1]="y(tick)" [attr.y2]="y(tick)" class="grid" />
        <text [attr.x]="pad.left - 6" [attr.y]="y(tick) + 4" text-anchor="end" class="tick">{{ tick }}</text>
      }
      @for (tick of xTicks(); track tick) {
        <text [attr.x]="x(tick)" [attr.y]="height - 8" text-anchor="middle" class="tick">{{ tick }}</text>
      }
      @for (s of series(); track s.label) {
        <polyline [attr.points]="points(s.values)" [attr.stroke]="s.color" fill="none" stroke-width="1.5" />
      }
      <text [attr.x]="pad.left" y="14" class="axis">{{ yLabel() }}</text>
      <text [attr.x]="width - pad.right" [attr.y]="height - 8" text-anchor="end" class="axis">{{ xLabel() }}</text>
    </svg>
    <div class="legend">
      @for (s of series(); track s.label) {
        <span><i [style.background]="s.color"></i>{{ s.label }}</span>
      }
    </div>
  `,
  styles: `
    svg { width: 100%; height: auto; display: block; }
    .grid { stroke: var(--grid); }
    .tick { font-size: 11px; fill: var(--muted); }
    .axis { font-size: 12px; fill: var(--muted); }
    .legend { display: flex; gap: 16px; font-size: 13px; color: var(--muted); margin-top: 4px; }
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

  readonly width = 720;
  readonly height = 240;
  readonly pad = { left: 44, right: 12, top: 24, bottom: 28 };

  private readonly xMax = computed(() => Math.max(...this.x_values(), 1));
  private readonly top = computed(
    () => this.yMax() ?? Math.max(...this.series().flatMap((s) => s.values), 1) * 1.1,
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

  x(v: number): number {
    return this.pad.left + (v / this.xMax()) * (this.width - this.pad.left - this.pad.right);
  }
  y(v: number): number {
    return this.height - this.pad.bottom - (v / this.top()) * (this.height - this.pad.top - this.pad.bottom);
  }
  points(values: number[]): string {
    const xs = this.x_values();
    return values.map((v, i) => `${this.x(xs[i]).toFixed(1)},${this.y(v).toFixed(1)}`).join(' ');
  }
}

function niceStep(raw: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
