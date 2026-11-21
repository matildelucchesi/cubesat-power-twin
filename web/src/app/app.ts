import { Component, computed, inject, signal } from '@angular/core';
import { DecimalPipe, PercentPipe, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { GroundTrack } from './ground-track';
import { LineChart, Series } from './line-chart';
import { OrbitView } from './orbit-view';
import { Face, SimulationRequest, SimulationResult, SimulationService } from './simulation.service';

@Component({
  selector: 'app-root',
  imports: [FormsModule, DecimalPipe, PercentPipe, DatePipe, LineChart, GroundTrack, OrbitView],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly api = inject(SimulationService);

  readonly faces: Face[] = ['+X', '-X', '+Y', '-Y', '+Z', '-Z'];
  readonly request: SimulationRequest = {
    days: 3,
    step_s: 60,
    attitude: 'tumbling',
    failed_faces: [],
    capacity_wh: 20,
    initial_soc: 0.8,
    tx_duty_cycle: 0.5,
    eclipse_model: 'skyfield',
  };

  readonly result = signal<SimulationResult | null>(null);
  /** Scenario that produced the current result (the form may change after a run). */
  readonly ran = signal<SimulationRequest | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');

  readonly powerSeries = computed<Series[]>(() => {
    const r = this.result();
    return r
      ? [
          { label: 'Solar array', values: r.series.power_w, color: 'var(--accent)' },
          { label: 'Load', values: r.series.load_w, color: 'var(--warn)' },
        ]
      : [];
  });
  readonly socSeries = computed<Series[]>(() => {
    const r = this.result();
    return r ? [{ label: 'State of charge', values: r.series.soc.map((s) => s * 100), color: 'var(--good)' }] : [];
  });

  constructor() {
    this.run();
  }

  toggleFace(face: Face, failed: boolean): void {
    const set = new Set(this.request.failed_faces);
    failed ? set.add(face) : set.delete(face);
    this.request.failed_faces = [...set];
  }

  run(): void {
    this.loading.set(true);
    this.error.set('');
    const request = structuredClone(this.request);
    this.api.simulate(request).subscribe({
      next: (r) => {
        this.result.set(r);
        this.ran.set(request);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e.error?.detail ? JSON.stringify(e.error.detail) : 'Backend not reachable. Is uvicorn running?');
        this.loading.set(false);
      },
    });
  }
}
