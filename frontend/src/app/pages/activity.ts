import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, ClockPipe, KIND_NAMES, elapsed } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ActivityEvent, Job } from '../core/models';
import { JobCard } from '../shared/job-card';

type Show = 'all' | 'failed';
type Level = 'all' | 'warning' | 'error';
const PAGE = 15;

/**
 * What the system is doing now, what it did, and the combined log.
 * Only running and queued jobs get a full card; finished ones are one line each,
 * and the log stays folded until you need it.
 */
@Component({
  selector: 'app-activity-page',
  imports: [RouterLink, JobCard, ClockPipe, AgoPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Activity</h1>
          <p class="muted">Jobs run one at a time, in the order they were queued.</p>
        </div>
      </div>

      <section>
        <h2>Now</h2>
        @if (store.active().length) {
          <div class="stack">
            @for (job of store.active(); track job.id) { <app-job-card [job]="job" [showLink]="true" /> }
          </div>
        } @else {
          <p class="idle muted">Nothing is running.</p>
        }
      </section>

      <section>
        <div class="row spread">
          <h2>Recent</h2>
          <div class="tabs" role="tablist">
            <button role="tab" [class.on]="show() === 'all'" (click)="setShow('all')">All
              <span class="muted num">{{ finished().length }}</span></button>
            <button role="tab" [class.on]="show() === 'failed'" (click)="setShow('failed')">Failed
              <span class="muted num">{{ failedCount() }}</span></button>
          </div>
        </div>
        <ul class="jobs">
          @for (job of shownJobs(); track job.id) {
            <li [class.failed]="job.state === 'failed'">
              <span class="dot" [class]="job.state" [attr.aria-label]="job.state"></span>
              <span class="what">
                <span class="label">{{ job.label }}</span>
                <span class="small muted">{{ kind(job) }} · #{{ job.id }} · {{ took(job) }} · {{ job.finished_at | ago }}</span>
                @if (job.state === 'failed' && job.error) {
                  <span class="error small">{{ firstLine(job.error) }}</span>
                }
              </span>
              @if (link(job); as l) { <a class="btn sm quiet" [routerLink]="l">Open</a> }
              <button class="sm quiet" (click)="toggle(job.id)">{{ open() === job.id ? 'Hide log' : 'Log' }}</button>
              @if (open() === job.id) {
                <div class="inline-card"><app-job-card [job]="job" [showLink]="false" /></div>
              }
            </li>
          } @empty {
            <li class="muted empty-row">{{ show() === 'failed' ? 'No failed jobs.' : 'Nothing has run yet.' }}</li>
          }
        </ul>
        @if (moreCount() > 0) {
          <button class="sm quiet more" (click)="limit.set(limit() + 30)">Show {{ moreCount() > 30 ? 30 : moreCount() }} more</button>
        }
      </section>

      <details class="fold combined" (toggle)="logOpen.set($any($event.target).open)">
        <summary><h2>Combined log</h2><span class="small muted">every job's lines, newest first</span></summary>
        <div class="tabs log-tabs" role="tablist">
          @for (tab of levels; track tab.value) {
            <button role="tab" [class.on]="level() === tab.value" (click)="setLevel(tab.value)">{{ tab.label }}</button>
          }
        </div>
        <div class="log full" role="log">
          @for (event of events(); track event.id) {
            <div class="line" [class]="event.level">
              <span class="time">{{ event.ts | clock }}</span>
              <span>
                <span class="tag">{{ kindName(event.job_kind) }} #{{ event.job_id }}@if (event.stage) { · {{ event.stage }} }</span>
                {{ event.message }}
              </span>
            </div>
          } @empty {
            <div class="muted">No log lines{{ level() === 'all' ? ' yet' : ' at this level' }}.</div>
          }
        </div>
        <p class="small muted">The last 200 lines. The server's own log has the same lines
          (<span class="mono">docker compose logs -f worker</span>).</p>
      </details>
    </div>
  `,
  styles: `
    section { margin-bottom: 28px; }
    h2 { margin: 0 0 12px; }
    .idle {
      margin: 0;
      padding: 14px 16px;
      border: 1px dashed var(--line);
      border-radius: var(--radius-sm);
    }
    .jobs {
      list-style: none;
      margin: 0;
      padding: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      overflow: hidden;
      li {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 10px;
        padding: 10px 14px;
        border-top: 1px solid var(--line);
        &:first-child { border-top: 0; }
      }
      .empty-row { padding: 16px; }
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      flex: none;
      background: var(--line-strong);
      &.succeeded { background: var(--good); }
      &.failed { background: var(--bad); }
      &.cancelled { background: var(--muted); }
    }
    .what {
      flex: 1;
      min-width: 0;
      display: grid;
      gap: 2px;
      .label {
        font-weight: 600;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }
    .error {
      color: var(--bad);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .inline-card { flex-basis: 100%; }
    .more { margin-top: 8px; }
    .combined {
      summary {
        display: flex;
        gap: 10px;
        align-items: baseline;
        cursor: pointer;
        h2 { display: inline; margin: 0; }
      }
      .log-tabs { margin: 12px 0; }
      .log.full { max-height: 520px; }
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

  protected readonly show = signal<Show>('all');
  protected readonly limit = signal(PAGE);
  protected readonly open = signal<number | null>(null);
  protected readonly logOpen = signal(false);

  protected readonly finished = computed(() =>
    this.store.jobs().filter((j) => j.state !== 'queued' && j.state !== 'running'));
  protected readonly failedCount = computed(() => this.finished().filter((j) => j.state === 'failed').length);
  private readonly filtered = computed(() =>
    this.show() === 'failed' ? this.finished().filter((j) => j.state === 'failed') : this.finished());
  protected readonly shownJobs = computed(() => this.filtered().slice(0, this.limit()));
  protected readonly moreCount = computed(() => this.filtered().length - this.limit());

  protected readonly levels: { value: Level; label: string }[] = [
    { value: 'all', label: 'Everything' },
    { value: 'warning', label: 'Warnings and errors' },
    { value: 'error', label: 'Errors' },
  ];
  protected readonly level = signal<Level>('all');
  protected readonly events = signal<ActivityEvent[]>([]);

  constructor() {
    // The combined log loads only when it is open, then follows running jobs.
    effect(() => {
      this.store.tick();
      this.store.finished();
      if (!this.logOpen()) return;
      const busy = untracked(() => this.store.active().length > 0);
      if (busy || untracked(() => this.events().length === 0)) untracked(() => this.load());
    });
  }

  protected setShow(show: Show): void {
    this.show.set(show);
    this.limit.set(PAGE);
  }

  protected toggle(id: number): void {
    this.open.set(this.open() === id ? null : id);
  }

  protected setLevel(level: Level): void {
    this.level.set(level);
    this.load();
  }

  protected kind(job: Job): string {
    return KIND_NAMES[job.kind] ?? job.kind;
  }

  protected kindName(kind: string): string {
    return KIND_NAMES[kind] ?? kind;
  }

  protected took(job: Job): string {
    return elapsed(job.started_at, job.finished_at) || job.state;
  }

  protected firstLine(text: string): string {
    return text.split('\n').find((l) => l.trim()) ?? text;
  }

  /** Where the job's result lives. */
  protected link(job: Job): (string | number)[] | null {
    if (job.storyboard_id) return ['/storyboards', job.storyboard_id];
    if (job.script_id) return ['/scripts', job.script_id];
    if (job.video_id) return ['/videos', job.video_id];
    return null;
  }

  private load(): void {
    const level = this.level();
    this.api.activity(level === 'all' ? undefined : level).subscribe({
      next: (events) => this.events.set(events),
      error: () => undefined,
    });
  }
}
