import { Component, ElementRef, effect, inject, input, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Explanation, HealthRequest, HealthService } from './health.service';

interface ChatMessage {
  role: 'user' | 'assistant' | 'error';
  text: string;
  source?: Explanation['source'];
  whatIfs?: number[];
}

const SUGGESTIONS = [
  'What is happening to the power system and what should I do?',
  'How sure is the twin about this diagnosis?',
  'How much time do we have before safe mode?',
  'What if we stop transmitting?',
];

/**
 * The operator assistant as a floating chat: a button fixed at the bottom right
 * opens a panel with the conversation about the current health report.
 * Each question is answered from the report and the twin's what-if tool.
 */
@Component({
  selector: 'app-assistant-chat',
  imports: [FormsModule],
  styleUrls: ['./assistant-chat.css'],
  host: { '(document:keydown.escape)': 'open.set(false)' },
  template: `
@if (open()) {
  <section class="panel" role="dialog" aria-label="Operator assistant">
    <header>
      <div>
        <h2>Operator assistant</h2>
        <p>Explains the current health report. Numbers come from the twin.</p>
      </div>
      <button type="button" class="close" (click)="open.set(false)" aria-label="Close the assistant">×</button>
    </header>

    <div class="log" #log>
      @if (!messages().length) {
        <p class="intro">{{ request() ? 'Ask about the alarm, the forecast or a corrective action.' : 'Run a health check first, then ask about it.' }}</p>
        @if (request()) {
          <div class="suggest">
            @for (s of suggestions; track s) { <button type="button" (click)="send(s)">{{ s }}</button> }
          </div>
        }
      }
      @for (m of messages(); track $index) {
        <div class="msg" [class]="m.role">
          <p>{{ m.text }}</p>
          @if (m.role === 'assistant') {
            <span class="meta">
              {{ m.source === 'model' ? 'Written by the language model from the twin report' : 'Template answer (no LLM configured on the backend)' }}
              @for (d of m.whatIfs; track $index) { · what-if at {{ (d * 100).toFixed(0) }} % duty cycle }
            </span>
          }
        </div>
      }
      @if (asking()) { <div class="msg assistant typing"><p>Thinking…</p></div> }
    </div>

    <form (ngSubmit)="send(draft)">
      <input name="q" [(ngModel)]="draft" [disabled]="!request()" placeholder="Ask the assistant…" aria-label="Question for the assistant" #box />
      <button type="submit" [disabled]="asking() || !draft.trim() || !request()">Send</button>
    </form>
  </section>
}

<button type="button" class="fab" [class.on]="open()" (click)="toggle()"
        [attr.aria-expanded]="open()" [attr.aria-label]="open() ? 'Close the assistant' : 'Open the operator assistant'">
  @if (open()) { <span aria-hidden="true">×</span> } @else {
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z" /></svg>
    <span class="label">Ask the assistant</span>
  }
</button>
  `,
})
export class AssistantChat {
  private readonly api = inject(HealthService);
  /** The request of the report on screen; null before the first health check. */
  readonly request = input<HealthRequest | null>(null);

  readonly suggestions = SUGGESTIONS;
  readonly open = signal(false);
  readonly messages = signal<ChatMessage[]>([]);
  readonly asking = signal(false);
  draft = '';

  private readonly log = viewChild<ElementRef<HTMLElement>>('log');
  private readonly box = viewChild<ElementRef<HTMLInputElement>>('box');

  constructor() {
    // A new health check starts a new conversation.
    effect(() => {
      this.request();
      this.messages.set([]);
    });
    // Keep the latest message in view.
    effect(() => {
      this.messages();
      this.asking();
      const el = this.log()?.nativeElement;
      if (el) queueMicrotask(() => (el.scrollTop = el.scrollHeight));
    });
  }

  toggle() {
    this.open.update((v) => !v);
    if (this.open()) queueMicrotask(() => this.box()?.nativeElement.focus());
  }

  send(text: string) {
    const r = this.request();
    const q = text.trim();
    if (!r || !q || this.asking()) return;
    this.draft = '';
    this.messages.update((m) => [...m, { role: 'user', text: q }]);
    this.asking.set(true);
    this.api.explain(r, q).subscribe({
      next: (a) => {
        this.messages.update((m) => [...m, {
          role: 'assistant', text: a.text, source: a.source, whatIfs: a.tool_calls.map((t) => t.input.tx_duty),
        }]);
        this.asking.set(false);
      },
      error: (e) => {
        const text = e?.status === 0 ? 'Backend not reachable.' : `The backend returned an error (${e?.status}). See the uvicorn terminal.`;
        this.messages.update((m) => [...m, { role: 'error', text }]);
        this.asking.set(false);
      },
    });
  }
}
