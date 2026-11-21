import {
  AfterViewInit, Component, ElementRef, OnDestroy, computed, effect, inject, input, viewChild,
} from '@angular/core';
import { DecimalPipe } from '@angular/common';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { Playback } from './playback.service';
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
  imports: [DecimalPipe],
  template: `
    <div class="stage">
      <canvas #canvas aria-label="3D view of the orbit and the CubeSat"></canvas>
      <div class="legend">
        <span><i class="sun"></i>Orbit in sunlight</span>
        <span><i class="shade"></i>Orbit in eclipse</span>
        <span><i class="cyl"></i>Earth's shadow</span>
      </div>
      <span class="hint-3d">Drag to rotate · scroll to zoom</span>
    </div>
    <div class="faces-head">
      <h3>Sunlight on each face</h3>
      <span>cos θ between face normal and Sun · {{ attitude() === 'sun_pointing' ? '+X kept on the Sun' : 'tumbling: this rotation is illustrative, the power model uses the average (A/4)' }}</span>
    </div>
    <ul class="faces">
      @for (f of faceRows(); track f.face) {
        <li [class.failed]="f.failed">
          <span class="name">{{ f.face }}</span>
          <span class="bar"><b [style.width.%]="f.factor * 100"></b></span>
          <span class="val">{{ f.failed ? 'failed' : (f.factor | number: '1.2-2') }}</span>
        </li>
      }
      @if (hasWings()) {
        <li>
          <span class="name">Wings</span>
          <span class="bar"><b [style.width.%]="wingFactor() * 100"></b></span>
          <span class="val">{{ wingFactor() | number: '1.2-2' }}</span>
        </li>
      }
    </ul>
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
    .stage { position: relative; }
    canvas { width: 100%; aspect-ratio: 4 / 3; display: block; background: var(--canvas); border: 1px solid var(--border); }
    .legend { position: absolute; left: 10px; bottom: 8px; display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 11px; color: var(--muted); }
    .legend i { display: inline-block; width: 14px; height: 3px; margin-right: 6px; vertical-align: middle; }
    .legend .sun { background: var(--sun); }
    .legend .shade { background: var(--text); }
    .legend .cyl { background: var(--shade-3d); height: 8px; }
    .hint-3d { position: absolute; right: 10px; top: 8px; font: 11px var(--f-data); color: var(--muted); }
    .faces-head { display: flex; flex-wrap: wrap; gap: 2px 12px; align-items: baseline; }
    h3 { margin: 0; font-size: 12px; font-weight: 600; }
    .faces-head span { font-size: 11px; color: var(--muted); }
    .faces { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px 16px; }
    .faces li { display: grid; grid-template-columns: 40px 1fr 44px; gap: 8px; align-items: center; font: 12px var(--f-data); }
    .bar { height: 6px; background: var(--grid); overflow: hidden; }
    .bar b { display: block; height: 100%; background: var(--sun); }
    .val { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
    .failed .name, .failed .val { color: var(--danger); }
    .failed .bar b { background: transparent; }
  `,
})
export class OrbitView implements AfterViewInit, OnDestroy {
  readonly result = input.required<SimulationResult>();
  readonly failedFaces = input<Face[]>([]);
  readonly attitude = input<'tumbling' | 'sun_pointing'>('tumbling');
  /** Canvas elsewhere on the page where the close-up of the CubeSat is drawn. */
  readonly closeupCanvas = input<HTMLCanvasElement | null>(null);

  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');

  private readonly playback = inject(Playback);
  readonly index = computed(() => Math.min(this.playback.index(), this.last()));

  private readonly o = computed(() => this.result().orbit3d);
  readonly last = computed(() => this.o().hours.length - 1);
  readonly lit = computed(() => this.o().sunlit[this.index()] ?? false);

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

  /** Deployable wings face the same way as +X (towards the Sun when sun pointing). */
  readonly hasWings = computed(() => this.result().spacecraft?.deployables ?? false);
  readonly wingFactor = computed(() =>
    this.lit() ? Math.max(0, NORMALS[0].clone().applyQuaternion(this.attitudeQuat()).dot(this.sunDir())) : 0,
  );

  private renderer?: THREE.WebGLRenderer;
  private controls?: OrbitControls;
  private frame = 0;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(40, 4 / 3, 0.01, 100);
  private readonly sat = new THREE.Group();
  private readonly cubeMaterials = FACES.map(() => new THREE.MeshStandardMaterial({ roughness: 0.6 }));
  private readonly wingMaterial = new THREE.MeshStandardMaterial({ roughness: 0.5 });

  // Close-up: the same spacecraft in its own scene and renderer, camera fixed near the Sun line.
  private closeRenderer?: THREE.WebGLRenderer;
  private readonly insetScene = new THREE.Scene();
  private readonly insetCamera = new THREE.PerspectiveCamera(32, 1, 0.01, 20);
  private readonly insetSat = new THREE.Group();
  private readonly insetLight = new THREE.DirectionalLight(0xffffff, 2.2);
  private readonly insetSunArrow = new THREE.Group();
  private readonly insetMaterials = FACES.map(() => new THREE.MeshStandardMaterial({ roughness: 0.6 }));
  private readonly insetWingMaterial = new THREE.MeshStandardMaterial({ roughness: 0.5 });
  /** One label sprite per body face in the close-up, in FACES order. */
  private readonly labelMaterials = FACES.map((f) => new THREE.SpriteMaterial({ map: labelTexture(f), depthTest: true, transparent: true }));
  private readonly dynamic = new THREE.Group(); // rebuilt when the result changes

  constructor() {
    effect(() => {
      const sc = this.result().spacecraft;
      const size = sc?.size_u ?? [1, 1, 1];
      this.buildBody(this.sat, this.cubeMaterials, this.wingMaterial, size, sc?.deployables ?? false, 0.12);
      this.buildBody(this.insetSat, this.insetMaterials, this.insetWingMaterial, size, sc?.deployables ?? false, 1);
      this.addFaceLabels(this.insetSat, size, 1);
      this.buildDynamic();
    });
    effect(() => this.updateSatellite());
  }

  ngAfterViewInit(): void {
    const canvas = this.canvas().nativeElement;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.autoClear = true;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.minDistance = 1.6;
    this.controls.maxDistance = 8;

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.9));

    const earth = new THREE.Mesh(
      new THREE.SphereGeometry(1, 64, 48),
      new THREE.MeshStandardMaterial({ color: 0x9fbcd2, roughness: 0.95 }),
    );
    const axis = new THREE.Mesh(
      new THREE.CylinderGeometry(0.006, 0.006, 2.5),
      new THREE.MeshBasicMaterial({ color: 0x56637a }),
    );
    this.scene.add(earth, axis);

    this.scene.add(this.sat, this.dynamic);
    this.insetScene.add(new THREE.AmbientLight(0xffffff, 0.55), this.insetLight, this.insetSat, this.insetSunArrow);

    this.buildDynamic();
    this.updateSatellite();
    this.resize();
    window.addEventListener('resize', this.resize);
    this.loop();
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.frame);
    window.removeEventListener('resize', this.resize);
    this.controls?.dispose();
    this.renderer?.dispose();
    this.closeRenderer?.dispose();
  }

  private readonly resize = () => {
    if (!this.renderer) return;
    const c = this.canvas().nativeElement;
    this.renderer.setSize(c.clientWidth, c.clientHeight, false);
    this.camera.aspect = c.clientWidth / c.clientHeight;
    this.camera.updateProjectionMatrix();
  };

  private loop = () => {
    this.frame = requestAnimationFrame(this.loop);
    this.controls?.update();
    const r = this.renderer;
    if (!r) return;
    r.render(this.scene, this.camera);

    const cc = this.closeupCanvas();
    if (cc && !this.closeRenderer) {
      this.closeRenderer = new THREE.WebGLRenderer({ canvas: cc, antialias: true, alpha: true });
      this.closeRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    }
    if (cc && this.closeRenderer) {
      if (cc.width !== Math.round(cc.clientWidth * this.closeRenderer.getPixelRatio())) {
        this.closeRenderer.setSize(cc.clientWidth, cc.clientHeight, false);
        this.insetCamera.aspect = cc.clientWidth / cc.clientHeight;
        this.insetCamera.updateProjectionMatrix();
      }
      this.closeRenderer.render(this.insetScene, this.insetCamera);
    }
  };

  /** Sun light, shadow cylinder and orbit line: they depend on the result only. */
  private buildDynamic(): void {
    this.dynamic.clear();
    const o = this.o();
    const sun = this.sunDir();

    // Inset: light from the Sun; camera 55° away from the Sun line so lit and dark faces both show.
    this.insetLight.position.copy(sun.clone().multiplyScalar(5));
    const perp = new THREE.Vector3(0, 1, 0).cross(sun);
    if (perp.lengthSq() < 1e-6) perp.set(1, 0, 0);
    perp.normalize();
    const camDir = sun.clone().multiplyScalar(Math.cos(55 * Math.PI / 180))
      .add(perp.multiplyScalar(Math.sin(55 * Math.PI / 180)))
      .add(new THREE.Vector3(0, 0.35, 0)).normalize();
    this.insetCamera.position.copy(camDir.multiplyScalar(4.2));
    this.insetCamera.lookAt(0, 0, 0);
    this.insetSunArrow.clear();
    this.insetSunArrow.add(new THREE.ArrowHelper(sun, sun.clone().multiplyScalar(0.85), 0.55, cssColor('--sun', '#d08a12').getHex(), 0.18, 0.1));

    const light = new THREE.DirectionalLight(0xffffff, 1.6);
    light.position.copy(sun.clone().multiplyScalar(10));
    this.dynamic.add(light);

    // Earth's shadow: a cylinder of one Earth radius pointing away from the Sun.
    const length = 3;
    const shadow = new THREE.Mesh(
      new THREE.CylinderGeometry(1, 1, length, 48, 1, true),
      new THREE.MeshBasicMaterial({ color: 0x56637a, transparent: true, opacity: 0.1, side: THREE.DoubleSide, depthWrite: false }),
    );
    shadow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), sun.clone().negate());
    shadow.position.copy(sun.clone().multiplyScalar(-length / 2));
    this.dynamic.add(shadow);

    // Arrow towards the Sun.
    this.dynamic.add(new THREE.ArrowHelper(sun, sun.clone().multiplyScalar(1.15), 0.9, cssColor('--sun', '#d08a12').getHex(), 0.14, 0.08));

    // Orbit, one colour per sunlit/eclipse step.
    const positions: number[] = [];
    const colors: number[] = [];
    const sunC = cssColor('--sun', '#d08a12');
    const shadeC = cssColor('--text', '#0f1b2d');
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
        side.multiplyScalar(4.0).add(normal.multiplyScalar(2.8)).add(sun.clone().multiplyScalar(1.2)),
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
    this.insetSat.quaternion.copy(this.attitudeQuat());

    const panel = new THREE.Color(0x2c3e5c);
    const lit = cssColor('--sun', '#d08a12');
    const failed = cssColor('--danger', '#c0392b');
    this.faceRows().forEach((row, k) => {
      for (const m of [this.cubeMaterials[k], this.insetMaterials[k]]) {
        m.color.copy(row.failed ? failed.clone().multiplyScalar(0.55) : panel);
        m.emissive.copy(row.failed ? new THREE.Color(0) : lit).multiplyScalar(row.failed ? 0 : row.factor * 0.8);
      }
    });
    const failedSet = new Set(this.failedFaces());
    FACES.forEach((f, k) => this.labelMaterials[k].color.set(failedSet.has(f) ? 0xff6b5e : 0xffffff));
    for (const m of [this.wingMaterial, this.insetWingMaterial]) {
      m.color.copy(panel);
      m.emissive.copy(lit).multiplyScalar(this.wingFactor() * 0.8);
    }
  }

  /**
   * Body box sized by the form factor (in 10 cm units, normalised so the
   * longest side is `longest`), with two wings along ±Y when deployables are on.
   */
  private buildBody(group: THREE.Group, mats: THREE.Material[], wingMat: THREE.Material,
                    sizeU: number[], wings: boolean, longest: number): void {
    group.clear();
    const k = longest / Math.max(...sizeU);
    const [sx, sy, sz] = sizeU.map((u) => u * k);
    // three.js world axes: body X -> x, body Y -> y, body Z -> z.
    const box = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mats);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry), new THREE.LineBasicMaterial({ color: 0x0f1b2d }));
    group.add(box, edges);
    if (!wings) return;
    const back = new THREE.MeshStandardMaterial({ color: 0xc9d1dc, roughness: 0.8 });
    const w = sy * 1.6;
    for (const side of [1, -1]) {
      // Cells on the +X side of each wing (material group 0), bare back elsewhere.
      const wing = new THREE.Mesh(new THREE.BoxGeometry(sx * 0.04, w, sz * 0.95), [wingMat, back, back, back, back, back]);
      wing.position.set(sx / 2, side * (sy / 2 + w / 2 + sy * 0.05), 0);
      const outline = new THREE.LineSegments(new THREE.EdgesGeometry(wing.geometry), new THREE.LineBasicMaterial({ color: 0x0f1b2d }));
      outline.position.copy(wing.position);
      group.add(wing, outline);
    }
  }

  /** Face names (+X ... -Z) floating just outside each face; they turn with the body. */
  private addFaceLabels(group: THREE.Group, sizeU: number[], longest: number): void {
    const k = longest / Math.max(...sizeU);
    const half = sizeU.map((u) => (u * k) / 2);
    NORMALS.forEach((n, i) => {
      const sprite = new THREE.Sprite(this.labelMaterials[i]);
      const axis = i >> 1; // 0: X, 1: Y, 2: Z
      sprite.position.copy(n).multiplyScalar(half[axis] + 0.2);
      sprite.scale.set(0.44, 0.22, 1);
      sprite.renderOrder = 2;
      group.add(sprite);
    });
  }
}

/** Small dark tag with the face name, drawn on a canvas. */
function labelTexture(text: string): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 128;
  c.height = 64;
  const g = c.getContext('2d')!;
  g.fillStyle = 'rgba(15, 27, 45, 0.85)';
  g.beginPath();
  g.roundRect(4, 8, 120, 48, 10);
  g.fill();
  g.fillStyle = '#ffffff';
  g.font = '600 30px "JetBrains Mono", monospace';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(text.replace('-', '\u2212'), 64, 33);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
