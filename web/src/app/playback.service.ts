import { Injectable, NgZone, OnDestroy, inject, signal } from '@angular/core';

/** Playback speeds, as multiples of real time. */
export const RATES = [60, 300, 1000] as const;
export type Rate = (typeof RATES)[number];

/**
 * One clock shared by every animated view (3D view, orbit plane, battery),
 * so they always show the same instant of the replay.
 */
@Injectable({ providedIn: 'root' })
export class Playback implements OnDestroy {
  private readonly zone = inject(NgZone);

  readonly index = signal(0);
  readonly playing = signal(true);
  readonly rate = signal<Rate>(300);

  private length = 1;
  private stepS = 60;
  private acc = 0;
  private last = 0;
  private frame = 0;

  constructor() {
    if (typeof requestAnimationFrame !== 'undefined') {
      this.zone.runOutsideAngular(() => (this.frame = requestAnimationFrame(this.tick)));
    }
  }

  /** Called when a new result arrives: number of samples and their spacing. */
  load(length: number, stepS: number): void {
    this.length = Math.max(1, length);
    this.stepS = Math.max(1, stepS);
    this.acc = 0;
    this.index.set(0);
  }

  seek(i: number): void {
    this.playing.set(false);
    this.index.set(Math.min(Math.max(0, i), this.length - 1));
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.frame);
  }

  private readonly tick = (t: number) => {
    this.frame = requestAnimationFrame(this.tick);
    const dt = this.last ? Math.min(0.25, (t - this.last) / 1000) : 0;
    this.last = t;
    if (!this.playing()) return;
    this.acc += dt * this.rate(); // simulated seconds since the last step
    if (this.acc < this.stepS) return;
    const steps = Math.floor(this.acc / this.stepS);
    this.acc -= steps * this.stepS;
    this.zone.run(() => this.index.set((this.index() + steps) % this.length));
  };
}
