import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { Api } from '../core/api';
import { JobsStore } from '../core/jobs-store';
import { CostLine, CostOf, CostReport } from '../core/models';

interface Step {
  name: string;
  total: number;
  lines: CostLine[];
}

/**
 * What AI cost so far for a video, step by step, with the total: a small line that
 * opens into the breakdown. Reloads when a job finishes, since a job may have spent.
 */
@Component({
  selector: 'app-cost-report',
  template: `
    @if (report(); as r) {
      <details class="cost">
        <summary>
          <span class="muted">{{ label() }}</span>
          <strong class="num">{{ money(r.total) }}</strong>
          <span class="more muted small">{{ r.lines.length ? 'step by step' : '' }}</span>
        </summary>
        @if (r.lines.length) {
          <table>
            <tbody>
              @for (step of steps(r); track step.name) {
                <tr class="step"><th colspan="2">{{ step.name }}</th><td class="num">{{ money(step.total) }}</td></tr>
                @for (l of step.lines; track l.task) {
                  <tr>
                    <td>{{ l.label }}</td>
                    <td class="muted small">{{ l.calls }} {{ l.calls === 1 ? 'call' : 'calls' }}</td>
                    <td class="num">{{ money(l.cost) }}</td>
                  </tr>
                }
              }
            </tbody>
            <tfoot><tr><th colspan="2">Total</th><td class="num">{{ money(r.total) }}</td></tr></tfoot>
          </table>
        } @else {
          <p class="small muted">No AI calls recorded for this yet.</p>
        }
        <p class="small muted note">{{ r.note }}</p>
      </details>
    }
  `,
  styles: `
    .cost {
      font-size: 13.5px;
    }
    summary {
      display: inline-flex;
      gap: 8px;
      align-items: baseline;
      cursor: pointer;
      list-style: none;
      &::-webkit-details-marker { display: none; }
    }
    .more { text-decoration: underline dotted; }
    table {
      width: 100%;
      max-width: 520px;
      margin-top: 8px;
      border-collapse: collapse;
    }
    td, th {
      padding: 3px 6px;
      text-align: left;
      font-weight: 400;
    }
    td.num { text-align: right; white-space: nowrap; }
    tr.step th, tr.step td { font-weight: 650; padding-top: 8px; }
    tfoot th, tfoot td { border-top: 1px solid var(--line); font-weight: 700; }
    .note { margin: 6px 0 0; max-width: 520px; }
  `,
})
export class CostReportView {
  private readonly api = inject(Api);
  private readonly store = inject(JobsStore);

  readonly of = input.required<CostOf>();
  readonly label = input('AI cost so far');
  protected readonly report = signal<CostReport | null>(null);

  constructor() {
    effect(() => {
      const of = this.of();
      this.store.finished();      // reload after any job: it may have spent
      untracked(() => this.api.costs(of).subscribe({ next: (r) => this.report.set(r), error: () => undefined }));
    });
  }

  protected steps(r: CostReport): Step[] {
    const out: Step[] = [];
    for (const line of r.lines) {
      let step = out.find((s) => s.name === line.step);
      if (!step) {
        step = { name: line.step, total: 0, lines: [] };
        out.push(step);
      }
      step.lines.push(line);
      step.total += line.cost;
    }
    return out;
  }

  protected money(value: number): string {
    return value > 0 && value < 0.01 ? '$' + value.toFixed(4) : '$' + value.toFixed(2);
  }
}
