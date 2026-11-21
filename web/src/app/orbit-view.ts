import {
  AfterViewInit, Component, ElementRef, OnDestroy, computed, effect, input, signal, viewChild,
} from '@angular/core';
import { DecimalPipe, PercentPipe } from '@angular/common';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { Face, SimulationResult } from './simulation.service';

const EARTH_RADIUS_KM = 6378.137;
const FACES: Face[] = ['+X', '-X', '+Y', '-Y', '+Z', '-Z'];
// Body-frame normals in the same order as THREE.BoxGeometry material groups.
const NORMALS = [
  new THREE.Vector3(1, 0, 0), new THREE.Vector3(-1, 0, 0),
  new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, -1, 0),
  new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 0, -1),
];

/** GCRS (z = north) to three.js world (y = up), in Earth radii. */
function toWorld(x: number, y: number, z: number): THREE.Vector3 {
  return new THREE.Vector3(x, z, -y).divideScalar(EARTH_RADIUS_KM);
}

function cssColor(name: string, fallback: string): THREE.Color {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return new THREE.Color(v || fallback);
}

/**
 * 3D view of the first two orbits: Earth lit by the Sun, the Earth's shadow
 * cylinder, the orbit coloured by sunlight, and the CubeSat (drawn much larger
 * than scale) with each face shaded by how directly it faces the Sun.
 */
@Component({
  selector: 'app-orbit-view',
  imports: [DecimalPipe, PercentPipe],
  template: `
    <div class="layout">
      <div class="stage">
        <canvas #canvas aria-label="3D view of the orbit and the CubeSat"></canvas>
        <div class="legend">
          <span><i class="sun"></i>Orbit in sunlight</span>
          <span><i class="shade"></i>Orbit in eclipse</span>
          <span><i class="cyl"></i>Earth's shadow</span>
          <span><i class="sun"></i>→ Sun direction</span>
        </div>
      </div>

      <aside>
        <div class="row">
          <button type="button" class="ghost" (click)="playing.set(!playing())">{{ playing() ? 'Pause' : 'Play' }}</button>
          <input type="range" min="0" [max]="last()" step="1" [value]="index()"
                 (input)="scrub($any($event.target).valueAsNumber)" aria-label="Time" />
        </div>
        <p class="time">T + {{ hours() | number: '1.2-2' }} h</p>

        <span class="badge" [class.dark]="!lit()">{{ lit() ? 'In sunlight' : 'In eclipse' }}</span>
        <dl>
          <dt>Array power</dt><dd>{{ power() | number: '1.2-2' }} W</dd>
          <dt>Battery</dt><dd>{{ soc() | percent: '1.0-1' }}</dd>
        </dl>

        <h3>Sunlight on each face</h3>
        <p class="hint">cos of the angle between the face and the Sun. Only faces with cells and a positive value produce power.</p>
        <ul class="faces">
          @for (f of faceRows(); track f.face) {
            <li [class.failed]="f.failed">
              <span class="name">{{ f.face }}</span>
              <span class="bar"><b [style.width.%]="f.factor * 100"></b></span>
              <span class="val">{{ f.failed ? 'failed' : (f.factor | number: '1.2-2') }}</span>
            </li>
          }
        </ul>
        <p class="hint">
          {{ attitude() === 'sun_pointing'
            ? 'Sun pointing: +X is kept towards the Sun.'
            : 'Tumbling: the attitude changes over time, so the power model uses the average (total cell area / 4).' }}
        </p>
      </aside>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 16px; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
    .stage { position: relative; }
    canvas { width: 100%; aspect-ratio: 4 / 3; display: block; border-radius: 8px; background: var(--panel-2); }
    .legend { position: absolute; left: 12px; bottom: 10px; display: flex; gap: 14px; font-size: 12px; color: var(--muted); }
    .legend i { display: inline-block; width: 14px; height: 3px; margin-right: 6px; vertical-align: middle; }
    .legend .sun { background: var(--warn); }
    .legend .shade { background: #5a6a94; }
    .legend .cyl { background: #3a4670; height: 8px; }
    aside { display: flex; flex-direction: column; gap: 10px; font-size: 13px; }
    .row { display: flex; gap: 8px; align-items: center; }
    .row input { flex: 1; }
    .ghost { background: transparent; border: 1px solid var(--border); color: var(--text); padding: 6px 10px; border-radius: 6px; cursor: pointer; }
    .time { margin: 0; font-variant-numeric: tabular-nums; color: var(--muted); }
    .badge { align-self: start; padding: 3px 10px; border-radius: 999px; background: color-mix(in srgb, var(--warn) 22%, transparent); color: var(--warn); font-weight: 600; }
    .badge.dark { background: color-mix(in srgb, #8a9bc8 18%, transparent); color: #a9b6d8; }
    dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
    dt { color: var(--muted); }
    dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
    h3 { margin: 8px 0 0; font-size: 13px; }
    .hint { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.4; }
    .faces { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .faces li { display: grid; grid-template-columns: 28px 1fr 44px; gap: 8px; align-items: center; }
    .bar { height: 6px; background: var(--panel-2); border-radius: 3px; overflow: hidden; }
    .bar b { display: block; height: 100%; background: var(--warn); }
    .val { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
    .failed .name, .failed .val { color: var(--danger); }
    .failed .bar b { background: transparent; }
  `,
})
export class OrbitView implements AfterViewInit, OnDestroy {
  readonly result = input.required<SimulationResult>();
  readonly failedFaces = input<Face[]>([]);
  readonly attitude = input<'tumbling' | 'sun_pointing'>('tumbling');

  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');

  readonly index = signal(0);
  readonly playing = signal(true);

  private readonly o = computed(() => this.result().orbit3d);
  readonly last = computed(() => this.o().hours.length - 1);
  readonly hours = computed(() => this.o().hours[this.index()] ?? 0);
  readonly lit = computed(() => this.o().sunlit[this.index()] ?? false);
  readonly power = computed(() => this.o().power_w[this.index()] ?? 0);
  readonly soc = computed(() => this.o().soc[this.index()] ?? 0);

  private readonly sunDir = computed(() => {
    const [x, y, z] = this.o().sun_dir;
    return toWorld(x, y, z).normalize();
  });

  /** CubeSat attitude at the current step. */
  private readonly attitudeQuat = computed(() => {
    const q = new THREE.Quaternion();
    if (this.attitude() === 'sun_pointing') {
      q.setFromUnitVectors(new THREE.Vector3(1, 0, 0), this.sunDir());
    } else {
      // Illustrative slow tumble about a fixed tilted axis.
      const axis = new THREE.Vector3(0.4, 1, 0.25).normalize();
      q.setFromAxisAngle(axis, this.index() * 0.12);
    }
    return q;
  });

  readonly faceRows = computed(() => {
    const q = this.attitudeQuat();
    const sun = this.sunDir();
    const lit = this.lit();
    const failed = new Set(this.failedFaces());
    return FACES.map((face, i) => ({
      face,
      failed: failed.has(face),
      factor: lit ? Math.max(0, NORMALS[i].clone().applyQuaternion(q).dot(sun)) : 0,
    }));
  });

  private renderer?: THREE.WebGLRenderer;
  private controls?: OrbitControls;
  private frame = 0;
  private lastStep = 0;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(40, 4 / 3, 0.01, 100);
  private readonly sat = new THREE.Group();
  private readonly cubeMaterials = FACES.map(() => new THREE.MeshStandardMaterial({ roughness: 0.6 }));
  private readonly dynamic = new THREE.Group(); // rebuilt when the result changes

  constructor() {
    effect(() => {
      this.result();
      this.index.set(0);
      this.buildDynamic();
    });
    effect(() => this.updateSatellite());
  }

  ngAfterViewInit(): void {
    const canvas = this.canvas().nativeElement;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.minDistance = 1.6;
    this.controls.maxDistance = 8;

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.12));

    const earth = new THREE.Mesh(
      new THREE.SphereGeometry(1, 64, 48),
      new THREE.MeshStandardMaterial({ color: 0x2b5c8a, roughness: 0.9 }),
    );
    const axis = new THREE.Mesh(
      new THREE.CylinderGeometry(0.006, 0.006, 2.5),
      new THREE.MeshBasicMaterial({ color: 0x8a9bc8 }),
    );
    this.scene.add(earth, axis);

    const cube = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.2, 0.2), this.cubeMaterials);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(cube.geometry),
      new THREE.LineBasicMaterial({ color: 0xc9d3ea }),
    );
    this.sat.add(cube, edges);
    this.scene.add(this.sat, this.dynamic);

    this.buildDynamic();
    this.updateSatellite();
    this.resize();
    window.addEventListener('resize', this.resize);
    this.loop(0);
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.frame);
    window.removeEventListener('resize', this.resize);
    this.controls?.dispose();
    this.renderer?.dispose();
  }

  scrub(i: number): void {
    this.playing.set(false);
    this.index.set(i);
  }

  private readonly resize = () => {
    if (!this.renderer) return;
    const c = this.canvas().nativeElement;
    this.renderer.setSize(c.clientWidth, c.clientHeight, false);
    this.camera.aspect = c.clientWidth / c.clientHeight;
    this.camera.updateProjectionMatrix();
  };

  private loop = (t: number) => {
    this.frame = requestAnimationFrame(this.loop);
    if (this.playing() && t - this.lastStep > 60) {
      this.lastStep = t;
      this.index.set(this.index() >= this.last() ? 0 : this.index() + 1);
    }
    this.controls?.update();
    this.renderer?.render(this.scene, this.camera);
  };

  /** Sun light, shadow cylinder and orbit line: they depend on the result only. */
  private buildDynamic(): void {
    this.dynamic.clear();
    const o = this.o();
    const sun = this.sunDir();

    const light = new THREE.DirectionalLight(0xffffff, 2.4);
    light.position.copy(sun.clone().multiplyScalar(10));
    this.dynamic.add(light);

    // Earth's shadow: a cylinder of one Earth radius pointing away from the Sun.
    const length = 3;
    const shadow = new THREE.Mesh(
      new THREE.CylinderGeometry(1, 1, length, 48, 1, true),
      new THREE.MeshBasicMaterial({ color: 0x3a4670, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false }),
    );
    shadow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), sun.clone().negate());
    shadow.position.copy(sun.clone().multiplyScalar(-length / 2));
    this.dynamic.add(shadow);

    // Arrow towards the Sun.
    this.dynamic.add(new THREE.ArrowHelper(sun, sun.clone().multiplyScalar(1.15), 0.9, cssColor('--warn', '#f2a33a').getHex(), 0.14, 0.08));

    // Orbit, one colour per sunlit/eclipse step.
    const positions: number[] = [];
    const colors: number[] = [];
    const sunC = cssColor('--warn', '#f2a33a');
    const shadeC = new THREE.Color(0x5a6a94);
    o.hours.forEach((_, i) => {
      const p = toWorld(o.x_km[i], o.y_km[i], o.z_km[i]);
      positions.push(p.x, p.y, p.z);
      const c = o.sunlit[i] ? sunC : shadeC;
      colors.push(c.r, c.g, c.b);
    });
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geom.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    this.dynamic.add(new THREE.Line(geom, new THREE.LineBasicMaterial({ vertexColors: true })));

    // Camera: look at the orbit almost face-on, from the side of the orbit
    // normal that faces the Sun, so both the lit arc and the eclipse are visible.
    if (o.hours.length > 2) {
      const r0 = toWorld(o.x_km[0], o.y_km[0], o.z_km[0]);
      const r1 = toWorld(o.x_km[1], o.y_km[1], o.z_km[1]);
      const normal = r0.clone().cross(r1).normalize();
      if (normal.dot(sun) < 0) normal.negate();
      const side = sun.clone().cross(normal).normalize();
      this.camera.position.copy(
        normal.multiplyScalar(5.0).add(side.multiplyScalar(2.2)).add(new THREE.Vector3(0, 1.2, 0)),
      );
      this.camera.lookAt(0, 0, 0);
      this.controls?.target.set(0, 0, 0);
    }
  }

  /** Move and orient the CubeSat, and shade its faces. */
  private updateSatellite(): void {
    const o = this.o();
    const i = this.index();
    if (!o.hours.length) return;
    this.sat.position.copy(toWorld(o.x_km[i], o.y_km[i], o.z_km[i]));
    this.sat.quaternion.copy(this.attitudeQuat());

    const panel = new THREE.Color(0x34508f);
    const lit = cssColor('--warn', '#f2a33a');
    const failed = cssColor('--danger', '#ff5c6c');
    this.faceRows().forEach((row, k) => {
      const m = this.cubeMaterials[k];
      m.color.copy(row.failed ? failed.clone().multiplyScalar(0.55) : panel);
      m.emissive.copy(row.failed ? new THREE.Color(0) : lit).multiplyScalar(row.failed ? 0 : row.factor * 0.8);
    });
  }
}
