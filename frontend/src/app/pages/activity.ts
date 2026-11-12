import { Component, effect, inject, signal, untracked } from '@angular/core';
import { Api } from '../core/api';
import { ClockPipe, KIND_NAMES } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ActivityEvent } from '../core/models';
import { JobCard } from '../shared/job-card';

type Level = 'all' | 'warning' | 'error';

/** What the system is doing and has done: jobs with their logs, and one combined log. */
@Component({
  selector: 'app-activity-page',
  imports: [JobCard, ClockPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Activity</h1>
          <p class="muted">Jobs run one at a time. Open a job's log to see each step it took.</p>
        </div>
      </div>

      <h2 style="margin-top:0">Jobs</h2>
      <div class="stack">
        @for (job of store.jobs(); track job.id) {
          <app-job-card [job]="job" [showLink]="true" />
        } @empty {
          <div class="empty">Nothing has run yet.</div>
        }
      </div>

      <div class="row spread" style="margin-top:36px">
        <h2 style="margin:0">Log</h2>
        <div class="tabs" role="tablist">
          @for (tab of levels; track tab.value) {
            <button role="tab" [class.on]="level() === tab.value" (click)="setLevel(tab.value)">{{ tab.label }}</button>
          }
        </div>
      </div>
      <div class="log full" role="log" style="margin-top:12px">
        @for (event of events(); track event.id) {
          <div class="line" [class]="event.level">
            <span class="time">{{ event.ts | clock }}</span>
            <span>
              <span class="tag">{{ kind(event) }} #{{ event.job_id }}@if (event.stage) { · {{ event.stage }} }</span>
              {{ event.message }}
            </span>
          </div>
        } @empty {
          <div class="muted">No log lines{{ level() === 'all' ? ' yet' : ' at this level' }}.</div>
        }
      </div>
      <p class="small muted" style="margin-top:8px">
        Newest first, last 200 lines. The same lines are written to the server's own log
        (<span class="mono">docker compose logs -f worker</span>).
      </p>
    </div>
  `,
  styles: `
    .log.full {
      max-height: 520px;
    }
    .tag {
      color: var(--muted);
      margin-right: 6px;
    }
  `,
})
export class ActivityPage {
  private readonly api = inject(Api);
  protected readonly store = inject(JobsStore);

  protected readonly levels: { value: Level; label: string }[] = [
    { value: 'all', label: 'Everything' },
    { value: 'warning', label: 'Warnings and errors' },
    { value: 'error', label: 'Errors' },
  ];
  protected readonly level = signal<Level>('all');
  protected readonly events = signal<ActivityEvent[]>([]);

  constructor() {
    // Refresh with every poll while something is running, and once when a job ends.
    effect(() => {
      this.store.tick();
      const busy = untracked(() => this.store.active().length > 0);
      if (busy || untracked(() => this.events().length === 0)) untracked(() => this.load());
    });
    effect(() => {
      if (this.store.finished().length) untracked(() => this.load());
    });
  }

  protected setLevel(level: Level): void {
    this.level.set(level);
    this.load();
  }

  protected kind(event: ActivityEvent): string {
    return KIND_NAMES[event.job_kind] ?? event.job_kind;
  }

  private load(): void {
    const level = this.level();
    this.api.activity(level === 'all' ? undefined : level).subscribe({
      next: (events) => this.events.set(events),
      error: () => undefined,
    });
  }
}
