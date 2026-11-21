import { Component, computed, inject, input } from '@angular/core';

import { Playback } from './playback.service';
import { SimulationResult } from './simulation.service';

const R_EARTH_KM = 6378.137;
type V3 = [number, number, number];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const scale = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const unit = (a: V3) => scale(a, 1 / Math.hypot(...a));

/**
 * The orbit seen face-on, to scale, with the Sun to the right.
 *
 * The plane basis is u = Sun direction projected on the orbit plane,
 * v = n x u (n = orbit normal). A point a·u + b·v is in the Earth's
 * cylindrical shadow when a < 0 and a²·sin²β + b² < R², so the shadow
 * drawn in the plane is half an ellipse with semi-axes R/|sin β| and R.
 */
@Component({
  selector: 'app-orbit-plane',
  template: `
    <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" role="img"
         aria-label="Orbit plane drawn to scale, with the Earth's shadow and the Sun to the right">
      <defs><clipPath id="op-clip"><rect [attr.width]="W" [attr.height]="H" /></clipPath></defs>
      <g clip-path="url(#op-clip)">
        <path [attr.d]="g().shadow" class="shadow" />
        <circle [attr.cx]="CX" [attr.cy]="CY" [attr.r]="K" class="night" />
        <path [attr.d]="'M ' + CX + ' ' + (CY - K) + ' A ' + K + ' ' + K + ' 0 0 1 ' + CX + ' ' + (CY + K) + ' Z'" class="day" />
        <polyline [attr.points]="g().orbit" class="orbit" />
        @for (seg of g().eclipses; track $index) {
          <polyline [attr.points]="seg" class="ecl" />
        }
        <line [attr.x1]="W - 14" [attr.x2]="W - 54" [attr.y1]="CY" [attr.y2]="CY" class="sun" />
        <path [attr.d]="'M ' + (W - 10) + ' ' + CY + ' l -10 -5 v 10 z'" class="sun-head" />
        <text [attr.x]="W - 12" [attr.y]="CY - 10" text-anchor="end" class="lbl">Sun</text>
        <text [attr.x]="CX" [attr.y]="CY + 4" text-anchor="middle" class="lbl">Earth</text>
        <text x="8" [attr.y]="CY + g().shadowB + 16" class="lbl">Earth's shadow</text>
        <circle [attr.cx]="g().sat[0]" [attr.cy]="g().sat[1]" r="6" class="sat" />
      </g>
    </svg>
    <p class="meta">β = {{ g().betaDeg.toFixed(1) }}° · altitude {{ g().altKm.toFixed(0) }} km · to scale</p>
  `,
  styles: `
    :host { display: block; min-width: 0; }
    svg { width: 100%; height: auto; display: block; }
    .shadow { fill: var(--shade); }
    .night { fill: var(--earth-night); stroke: var(--muted); stroke-width: 1; }
    .day { fill: var(--earth-day); }
    .orbit { fill: none; stroke: var(--sun); stroke-width: 2; }
    .ecl { fill: none; stroke: var(--text); stroke-width: 3.5; stroke-linecap: round; }
    .sun { stroke: var(--sun); stroke-width: 2; }
    .sun-head { fill: var(--sun); }
    .sat { fill: var(--accent); stroke: var(--panel); stroke-width: 2.5; }
    .lbl { font: 11px var(--f-data); fill: var(--muted); }
    .meta { margin: 6px 0 0; font: 11px var(--f-data); color: var(--muted); text-align: center; }
  `,
})
export class OrbitPlane {
  readonly result = input.required<SimulationResult>();
  private readonly playback = inject(Playback);

  readonly W = 360;
  readonly H = 300;
  readonly CX = 170;
  readonly CY = 150;
  readonly K = 112; // pixels per Earth radius

  /** Plane coordinates of every sample, in Earth radii. Depends on the result only. */
  private readonly plane = computed(() => {
    const o = this.result().orbit3d;
    const p = o.x_km.map((x, i): V3 => [x, o.y_km[i], o.z_km[i]]);
    const s = unit(o.sun_dir as V3);
    const n = unit(cross(p[0], p[1]));
    const sinB = dot(s, n);
    const u = unit(sub(s, scale(n, sinB)));
    const v = cross(n, u);
    const ab = p.map((q) => [dot(q, u) / R_EARTH_KM, dot(q, v) / R_EARTH_KM] as const);
    const altKm = p.reduce((m, q) => m + Math.hypot(...q), 0) / p.length - R_EARTH_KM;
    return { ab, sinB, altKm };
  });

  readonly g = computed(() => {
    const { ab, sinB, altKm } = this.plane();
    const o = this.result().orbit3d;
    const px = (a: number, b: number) => [this.CX + a * this.K, this.CY - b * this.K] as const;
    const pts = ab.map(([a, b]) => px(a, b));
    const str = (q: readonly (readonly [number, number])[]) => q.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

    const eclipses: string[] = [];
    let run: (readonly [number, number])[] = [];
    pts.forEach((q, i) => {
      if (!o.sunlit[i]) run.push(q);
      else if (run.length) { eclipses.push(str(run)); run = []; }
    });
    if (run.length) eclipses.push(str(run));

    const rx = Math.min(1 / Math.max(Math.abs(sinB), 1e-3), 4) * this.K;
    const shadow = `M ${this.CX} ${this.CY - this.K} A ${rx} ${this.K} 0 0 0 ${this.CX} ${this.CY + this.K} Z`;
    const i = Math.min(this.playback.index(), pts.length - 1);
    return {
      orbit: str(pts),
      eclipses,
      shadow,
      shadowB: this.K,
      sat: pts[Math.max(0, i)] ?? [this.CX, this.CY],
      betaDeg: (Math.asin(sinB) * 180) / Math.PI,
      altKm,
    };
  });
}
