import { Component, computed, input } from '@angular/core';
import { Compliance } from '../core/models';

/** Result of checking one clip against the campaign brief. */
@Component({
  selector: 'app-brief-check',
  template: `
    @if (result(); as r) {
      <div class="box">
        <div><span class="pill" [class]="r.verdict">{{ verdict() }}</span></div>
        <p class="small">{{ r.summary }}</p>
        @if (open().length) {
          <ul class="checks">
            @for (check of open(); track check.requirement) {
              <li>
                <span class="pill" [class]="check.status">{{ check.status === 'fail' ? 'Broken' : 'Check' }}</span>
                <span><strong>{{ check.requirement }}.</strong> {{ check.note }}</span>
              </li>
            }
          </ul>
        }
        @if (passed().length) {
          <details class="fold small">
            <summary class="muted">{{ passed().length }} {{ passed().length === 1 ? 'requirement' : 'requirements' }} met</summary>
            <ul class="checks">
              @for (check of passed(); track check.requirement) {
                <li>
                  <span class="pill pass">Met</span>
                  <span><strong>{{ check.requirement }}.</strong> {{ check.note }}</span>
                </li>
              }
            </ul>
          </details>
        }
      </div>
    }
  `,
  styles: `
    .box {
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: 12px 14px;
      display: grid;
      gap: 6px;
    }
    details {
      margin-top: 4px;
    }
  `,
})
export class BriefCheck {
  readonly result = input<Compliance | null>(null);

  protected readonly verdict = computed(() => {
    const labels = { pass: 'Passes the brief', review: 'Needs your check', fail: 'Breaks the brief' };
    return labels[this.result()?.verdict ?? 'review'];
  });
  protected readonly open = computed(() => (this.result()?.checks ?? []).filter((c) => c.status !== 'pass'));
  protected readonly passed = computed(() => (this.result()?.checks ?? []).filter((c) => c.status === 'pass'));
}
