import { Component, computed, input } from '@angular/core';

/** Replay state shown on the map. */
export interface LiveTrack {
  lat: number;
  lon: number;
  altKm: number;
  /** Recent positions, oldest first. */
  trailLat: number[];
  trailLon: number[];
  /** Sub-solar point [deg]. */
  sunLat: number;
  sunLon: number;
  lit: boolean;
}

const R_EARTH_KM = 6378.137;
const D = Math.PI / 180;
const wrap = (lon: number) => ((((lon + 180) % 360) + 360) % 360) - 180;

/**
 * Ground track on an equirectangular map, with the night side of the Earth,
 * the sub-solar point, and (during the replay) the satellite, its recent
 * trail and the footprint where it is above the horizon.
 */
@Component({
  selector: 'app-ground-track',
  template: `
    <svg viewBox="0 0 720 360" role="img" aria-label="Ground track, day and night, and current satellite position">
      <rect x="0" y="0" width="720" height="360" class="bg" />
      @if (night(); as n) { <polygon [attr.points]="n" class="night" /> }
      @for (lon of lons; track lon) {
        <line [attr.x1]="x(lon)" [attr.x2]="x(lon)" y1="0" y2="360" class="grid" />
        <text [attr.x]="x(lon) + 3" y="354" class="tick">{{ lon }}°</text>
      }
      @for (lat of lats; track lat) {
        <line x1="0" x2="720" [attr.y1]="y(lat)" [attr.y2]="y(lat)" class="grid" [class.eq]="lat === 0" />
        <text x="3" [attr.y]="y(lat) - 3" class="tick">{{ lat }}°</text>
      }
      @for (seg of segments(); track $index) {
        <polyline [attr.points]="seg" class="track" />
      }
      @if (live(); as l) {
        <g [attr.transform]="'translate(' + x(l.sunLon) + ' ' + y(l.sunLat) + ')'" class="sun">
          <circle r="7" />
          @for (a of rays; track a) {
            <line [attr.x1]="10 * cos(a)" [attr.y1]="10 * sin(a)" [attr.x2]="14 * cos(a)" [attr.y2]="14 * sin(a)" />
          }
        </g>
        @for (seg of footprint(); track $index) {
          <polyline [attr.points]="seg" class="foot" />
        }
        @for (seg of trail(); track $index) {
          <polyline [attr.points]="seg" class="trail" />
        }
        <g [attr.transform]="'translate(' + x(l.lon) + ' ' + y(l.lat) + ')'">
          <circle r="11" class="pulse" />
          <circle r="5.5" class="sat" [class.dark]="!l.lit" />
        </g>
      }
    </svg>
    <div class="legend">
      <span><i class="l-track"></i>Next 24 h</span>
      <span><i class="l-trail"></i>Last 30 min</span>
      <span><i class="l-foot"></i>Footprint, satellite above the horizon</span>
      <span><i class="l-night"></i>Night side</span>
      <span><i class="l-sun"></i>Sub-solar point</span>
    </div>
  `,
  styles: `
    svg { width: 100%; height: auto; display: block; border: 1px solid var(--border); }
    .bg { fill: var(--panel-2); }
    .night { fill: var(--text); opacity: 0.12; }
    .grid { stroke: var(--border); stroke-width: 0.6; }
    .grid.eq { stroke: var(--muted); stroke-dasharray: 3 3; }
    .tick { font: 10px var(--f-data); fill: var(--muted); }
    .track { fill: none; stroke: var(--accent); stroke-width: 0.8; opacity: 0.2; }
    .trail { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-linecap: round; }
    .foot { fill: none; stroke: var(--accent); stroke-width: 1.2; stroke-dasharray: 4 3; }
    .sun circle { fill: var(--sun); }
    .sun line { stroke: var(--sun); stroke-width: 1.6; stroke-linecap: round; }
    .sat { fill: var(--accent); stroke: var(--panel); stroke-width: 2; }
    .sat.dark { fill: var(--text); }
    .pulse { fill: var(--accent); opacity: 0; transform-box: fill-box; transform-origin: center; animation: pulse 1.8s ease-out infinite; }
    @keyframes pulse { from { opacity: 0.35; transform: scale(0.5); } to { opacity: 0; transform: scale(2); } }
    @media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 6px; font-size: 11.5px; color: var(--muted); }
    .legend i { display: inline-block; width: 14px; height: 3px; margin-right: 6px; vertical-align: middle; }
    .l-track { background: var(--accent); opacity: 0.3; }
    .l-trail { background: var(--accent); height: 4px !important; }
    .l-foot { border-top: 1.5px dashed var(--accent); height: 0 !important; }
    .l-night { background: var(--text); opacity: 0.15; height: 10px !important; }
    .l-sun { background: var(--sun); width: 9px !important; height: 9px !important; border-radius: 50%; }
  `,
})
export class GroundTrack {
  readonly lat = input.required<number[]>();
  readonly lon = input.required<number[]>();
  readonly live = input<LiveTrack | null>(null);

  readonly lons = [-180, -120, -60, 0, 60, 120];
  readonly lats = [60, 30, 0, -30, -60];
  readonly rays = [0, 45, 90, 135, 180, 225, 270, 315].map((a) => a * D);
  readonly cos = Math.cos;
  readonly sin = Math.sin;

  /** Polylines of the track, split where it wraps from +180 to -180 longitude. */
  readonly segments = computed(() => this.split(this.lat(), this.lon()));
  readonly trail = computed(() => {
    const l = this.live();
    return l ? this.split(l.trailLat, l.trailLon) : [];
  });

  /** Night side: the terminator is where the Sun is on the horizon, tan(lat) = -cos(lon - lon_s) / tan(dec). */
  readonly night = computed(() => {
    const l = this.live();
    if (!l) return null;
    const dec = Math.abs(l.sunLat) < 0.1 ? 0.1 * Math.sign(l.sunLat || 1) : l.sunLat;
    const pts: string[] = [];
    for (let lon = -180; lon <= 180; lon += 3) {
      const lat = Math.atan(-Math.cos((lon - l.sunLon) * D) / Math.tan(dec * D)) / D;
      pts.push(`${this.x(lon).toFixed(1)},${this.y(lat).toFixed(1)}`);
    }
    const pole = dec > 0 ? -90 : 90; // night covers the pole away from the Sun
    pts.push(`${this.x(180)},${this.y(pole)}`, `${this.x(-180)},${this.y(pole)}`);
    return pts.join(' ');
  });

  /** Small circle of angular radius acos(R / (R + h)) around the sub-satellite point. */
  readonly footprint = computed(() => {
    const l = this.live();
    if (!l) return [];
    const lam = Math.acos(R_EARTH_KM / (R_EARTH_KM + l.altKm));
    const p0 = l.lat * D;
    const lat: number[] = [];
    const lon: number[] = [];
    for (let az = 0; az <= 360; az += 6) {
      const a = az * D;
      const p = Math.asin(Math.sin(p0) * Math.cos(lam) + Math.cos(p0) * Math.sin(lam) * Math.cos(a));
      const dl = Math.atan2(Math.sin(a) * Math.sin(lam) * Math.cos(p0), Math.cos(lam) - Math.sin(p0) * Math.sin(p));
      lat.push(p / D);
      lon.push(wrap(l.lon + dl / D));
    }
    return this.split(lat, lon);
  });

  x(lon: number): number {
    return ((wrap(lon) + 180) / 360) * 720;
  }
  y(lat: number): number {
    return ((90 - lat) / 180) * 360;
  }

  private split(lat: number[], lon: number[]): string[] {
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
  }
}
