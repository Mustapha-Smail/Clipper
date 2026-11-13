import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { ClockPipe, KIND_NAMES, STAGE_NAMES, elapsed } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { Job, JobEvent } from '../core/models';
import { Confirm } from '../core/confirm';
import { Toasts } from '../core/toast';

/** One job: what it is, how far along, its live log, and cancel or retry. */
@Component({
  selector: 'app-job-card',
  imports: [RouterLink, ClockPipe],
  template: `
    <div class="card job" [class.failed]="job().state === 'failed'">
      <div class="head">
        <div class="what">
          <span class="name">{{ job().label }}</span>
          <span class="small muted">{{ kind() }} · #{{ job().id }}</span>
        </div>
        <div class="row">
          <span class="small muted num">{{ time() }}</span>
          <span class="pill" [class]="job().state">{{ stateLabel() }}</span>
        </div>
      </div>

      <div class="steps" [attr.aria-label]="'Progress: ' + job().message">
        @for (step of steps(); track step.name) {
          <div class="step" [class.done]="step.done" [class.now]="step.now" [class.unknown]="step.unknown">
            <div class="bar"><i [style.width.%]="step.fill"></i></div>
            {{ step.label }}
          </div>
        }
      </div>

      @if (job().state === 'running' || job().state === 'queued') {
        <p class="small muted">{{ job().state === 'queued' ? 'Waiting for the current job to finish' : job().message }}</p>
      }
      @if (job().error) {
        <pre class="error">{{ job().error }}</pre>
      }

      <div class="row actions">
        <button class="sm quiet" (click)="toggleLog()" [attr.aria-expanded]="open()">
          {{ open() ? 'Hide log' : 'Show log' }}
        </button>
        @if (job().state === 'running' || job().state === 'queued') {
          <button class="sm quiet danger" (click)="cancel()" [disabled]="job().cancel_requested">
            {{ job().cancel_requested ? 'Stopping…' : 'Cancel' }}
          </button>
        }
        @if (job().state === 'failed' || job().state === 'cancelled') {
          <button class="sm" (click)="retry()">Retry</button>
          <button class="sm quiet danger" (click)="remove()" [disabled]="removing()">Delete</button>
        }
        @if (showLink() && job().video_id && job().state === 'succeeded') {
          <a class="btn sm" [routerLink]="['/videos', job().video_id]">Open the video</a>
        }
        @if (showLink() && job().storyboard_id && job().state === 'succeeded') {
          <a class="btn sm" [routerLink]="['/storyboards', job().storyboard_id]">Open the storyboard</a>
        } @else if (showLink() && job().script_id && job().state === 'succeeded') {
          <a class="btn sm" [routerLink]="['/scripts', job().script_id]">Open the script</a>
        }
      </div>

      @if (open()) {
        <div class="log" role="log">
          @for (event of events(); track event.id) {
            <div class="line" [class]="event.level">
              <span class="time">{{ event.ts | clock }}</span><span>{{ event.message }}</span>
            </div>
          } @empty {
            <div class="muted">No log lines yet.</div>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .job.failed {
      border-color: var(--bad);
    }
    .head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .what {
      display: grid;
      min-width: 0;
    }
    .name {
      font-weight: 650;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .steps {
      display: flex;
      gap: 6px;
      margin: 14px 0 8px;
    }
    .step {
      flex: 1;
      min-width: 0;
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .bar {
      height: 5px;
      border-radius: 3px;
      background: var(--sunken);
      overflow: hidden;
      margin-bottom: 5px;
    }
    .bar i {
      display: block;
      height: 100%;
      width: 0;
      background: var(--text);
      transition: width 0.4s;
    }
    .step.done .bar i {
      width: 100% !important;
    }
    .step.now {
      color: var(--text);
      font-weight: 650;
    }
    .step.unknown .bar i {
      width: 35% !important;
      animation: slide 1.3s infinite ease-in-out;
    }
    @keyframes slide {
      from {
        margin-left: -35%;
      }
      to {
        margin-left: 100%;
      }
    }
    pre.error {
      white-space: pre-wrap;
      word-break: break-word;
      font: 12.5px/1.45 var(--mono);
      margin: 8px 0 0;
      color: var(--bad);
      max-height: 180px;
      overflow: auto;
    }
    .actions {
      margin-top: 8px;
      margin-left: -10px;
    }
    .log {
      margin-top: 10px;
    }
  `,
})
export class JobCard {
  private readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  protected readonly removing = signal(false);

  readonly job = input.required<Job>();
  /** Offer a link to the video once the job has succeeded. */
  readonly showLink = input(false);
  /** Start with the log visible. */
  readonly expanded = input(false);

  protected readonly open = signal(false);
  protected readonly events = signal<JobEvent[]>([]);
  private loadedFor = 0;

  protected readonly kind = computed(() => KIND_NAMES[this.job().kind] ?? this.job().kind);

  protected readonly stateLabel = computed(() => {
    const labels: Record<string, string> = {
      queued: 'Queued', running: 'Running', succeeded: 'Done', failed: 'Failed', cancelled: 'Cancelled',
    };
    return labels[this.job().state];
  });

  protected readonly steps = computed(() => {
    const job = this.job();
    const current = job.stage ? job.stages.indexOf(job.stage) : -1;
    return job.stages.map((name, i) => {
      const now = job.state === 'running' && i === current;
      return {
        name,
        label: STAGE_NAMES[name] ?? name,
        done: job.state === 'succeeded' || i < current,
        now,
        unknown: now && job.progress == null,
        fill: now && job.progress != null ? Math.round(job.progress * 100) : 0,
      };
    });
  });

  protected readonly time = computed(() => {
    this.store.tick(); // re-evaluate while the job runs
    const job = this.job();
    return job.state === 'queued' ? '' : elapsed(job.started_at, job.finished_at);
  });

  constructor() {
    effect(() => {
      if (this.expanded()) untracked(() => this.toggleLog(true));
    });
    // While the log is open, fetch new lines on every poll until the job ends.
    effect(() => {
      this.store.tick();
      const job = this.job();
      if (!this.open()) return;
      const active = job.state === 'running' || job.state === 'queued';
      untracked(() => {
        if (active || this.loadedFor !== job.id || this.justFinished(job)) this.loadEvents(job.id);
      });
    });
  }

  private lastState = '';
  private justFinished(job: Job): boolean {
    const changed = this.lastState !== job.state;
    this.lastState = job.state;
    return changed;
  }

  protected toggleLog(force?: boolean): void {
    this.open.set(force ?? !this.open());
  }

  private loadEvents(jobId: number): void {
    const known = this.loadedFor === jobId ? this.events() : [];
    const after = known.length ? known[known.length - 1].id : 0;
    this.loadedFor = jobId;
    this.api.jobEvents(jobId, after).subscribe({
      next: (fresh) => {
        if (fresh.length || !known.length) this.events.set([...known, ...fresh]);
      },
      error: () => undefined,
    });
  }

  protected cancel(): void {
    this.api.cancelJob(this.job().id).subscribe({
      next: () => this.store.refresh(),
      error: (err) => this.toasts.error(err),
    });
  }

  /** Forget the job. A clipping video that never got a clip goes with it, files included. */
  protected async remove(): Promise<void> {
    const job = this.job();
    const clipping = !!job.video_id;
    if (!(await this.confirm.ask({
      title: 'Delete this job?',
      message: clipping ? 'If this video has no clip yet, it is deleted too, with its downloaded file and transcript.'
        : 'Its log is deleted. What it already made is kept.',
      confirm: 'Delete', danger: true }))) return;
    this.removing.set(true);
    this.api.deleteJob(job.id).subscribe({
      next: () => {
        this.removing.set(false);
        this.toasts.ok('Deleted');
        this.store.refresh();
      },
      error: (err) => {
        this.removing.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected retry(): void {
    this.api.retryJob(this.job().id).subscribe({
      next: () => {
        this.toasts.ok('Queued again');
        this.store.refresh();
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
