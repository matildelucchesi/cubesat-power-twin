import { Component, computed, input } from '@angular/core';

/**
 * Battery drawn as a cell that fills with the state of charge (linear, 0–100 %).
 * The fill colour moves from red (empty) through amber to green (full).
 */
@Component({
  selector: 'app-battery-gauge',
  template: `
    <svg viewBox="0 0 64 128" role="img" [attr.aria-label]="'Battery at ' + pct() + ' percent'">
      <rect x="22" y="2" width="20" height="9" rx="2" class="cap" />
      <rect x="4" y="11" width="56" height="113" rx="7" class="case" />
      <rect x="9" [attr.y]="16 + 103 - level()" width="46" [attr.height]="level()" rx="3" [style.fill]="color()" />
      @for (t of [0.25, 0.5, 0.75]; track t) {
        <line x1="9" x2="55" [attr.y1]="16 + 103 * (1 - t)" [attr.y2]="16 + 103 * (1 - t)" class="tick" />
      }
      @if (state() !== 'full') {
        <path d="M32 54 l11 15 h-6.5 v13 h-9 v-13 h-6.5 z" class="arrow"
              [attr.transform]="state() === 'charging' ? null : 'rotate(180 32 68)'" />
      }
    </svg>
    <strong class="pct">{{ pct() }} %</strong>
    <span class="state" [class]="state()">
      {{ state() === 'full' ? 'Full' : state() === 'charging' ? 'Charging' : 'Discharging' }}
    </span>
    <span class="net">{{ state() === 'full' ? 'surplus not stored' : (net() > 0 ? '+' : '') + net().toFixed(2) + ' W' }}</span>
  `,
  styles: `
    :host { display: flex; flex-direction: column; align-items: center; gap: 4px; font-family: var(--f-data); font-variant-numeric: tabular-nums; }
    svg { width: 64px; height: auto; }
    .cap { fill: var(--text); }
    .case { fill: var(--panel); stroke: var(--text); stroke-width: 2.5; }
    .tick { stroke: var(--panel); stroke-width: 2; }
    .arrow { fill: var(--panel); opacity: 0.9; }
    .pct { font-size: 20px; font-weight: 600; color: var(--text); margin-top: 4px; }
    .state { font-size: 11px; font-weight: 600; }
    .state.charging, .state.full { color: var(--good); }
    .state.discharging { color: var(--sun); }
    .net { font-size: 11px; color: var(--muted); }
  `,
})
export class BatteryGauge {
  readonly soc = input.required<number>();
  /** Net power into the battery bus [W]: generated minus load. */
  readonly net = input.required<number>();

  readonly pct = computed(() => (this.soc() * 100).toFixed(1));
  readonly level = computed(() => 103 * Math.min(1, Math.max(0, this.soc())));
  readonly state = computed(() => (this.soc() >= 0.999 ? 'full' : this.net() > 0 ? 'charging' : 'discharging'));
  readonly color = computed(() => {
    const s = this.soc();
    if (s < 0.3) return 'var(--danger)';
    if (s < 0.6) return `color-mix(in oklab, var(--mid) ${((s - 0.3) / 0.3) * 100}%, var(--danger))`;
    return `color-mix(in oklab, var(--good) ${((s - 0.6) / 0.4) * 100}%, var(--mid))`;
  });
}
