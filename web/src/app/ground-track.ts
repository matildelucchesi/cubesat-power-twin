import { Component, computed, input } from '@angular/core';

/** Ground track on an equirectangular grid (longitude x latitude). */
@Component({
  selector: 'app-ground-track',
  template: `
    <svg viewBox="0 0 720 360" role="img" aria-label="Ground track over 24 hours">
      <rect x="0" y="0" width="720" height="360" class="bg" />
      @for (lon of lons; track lon) {
        <line [attr.x1]="x(lon)" [attr.x2]="x(lon)" y1="0" y2="360" class="grid" />
        <text [attr.x]="x(lon) + 3" y="354" class="tick">{{ lon }}°</text>
      }
      @for (lat of lats; track lat) {
        <line x1="0" x2="720" [attr.y1]="y(lat)" [attr.y2]="y(lat)" class="grid" />
        <text x="3" [attr.y]="y(lat) - 3" class="tick">{{ lat }}°</text>
      }
      @for (seg of segments(); track $index) {
        <polyline [attr.points]="seg" class="track" />
      }
      @if (lat().length) {
        <circle [attr.cx]="x(lon()[0])" [attr.cy]="y(lat()[0])" r="5" class="start" />
      }
    </svg>
  `,
  styles: `
    svg { width: 100%; height: auto; display: block; }
    .bg { fill: var(--panel-2); }
    .grid { stroke: var(--grid); }
    .tick { font: 10px var(--f-data); fill: var(--muted); }
    .track { fill: none; stroke: var(--accent); stroke-width: 1.5; }
    .start { fill: var(--sun); }
  `,
})
export class GroundTrack {
  readonly lat = input.required<number[]>();
  readonly lon = input.required<number[]>();
  readonly lons = [-180, -120, -60, 0, 60, 120];
  readonly lats = [60, 30, 0, -30, -60];

  /** Polylines of the track, split where it wraps from +180 to -180 longitude. */
  readonly segments = computed(() => {
    const lat = this.lat();
    const lon = this.lon();
    const segs: string[] = [];
    let current: string[] = [];
    lon.forEach((l, i) => {
      if (i > 0 && Math.abs(l - lon[i - 1]) > 180) {
        segs.push(current.join(' '));
        current = [];
      }
      current.push(`${this.x(l).toFixed(1)},${this.y(lat[i]).toFixed(1)}`);
    });
    if (current.length) segs.push(current.join(' '));
    return segs;
  });

  x(lon: number): number {
    return ((lon + 180) / 360) * 720;
  }
  y(lat: number): number {
    return ((90 - lat) / 180) * 360;
  }
}
