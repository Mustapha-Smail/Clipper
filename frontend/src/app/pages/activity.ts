import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { ClockPipe, KIND_AREAS, KIND_NAMES, elapsed } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ActivityEvent, Job } from '../core/models';
import { JobCard } from '../shared/job-card';

type View = 'jobs' | 'log';
type StateFilter = 'all' | 'succeeded' | 'failed' | 'cancelled';
type Level = 'all' | 'warning' | 'error';
const PAGE = 40;
const DAY = 86_400_000;

/**
 * What the system is doing now and what it did. Running and queued jobs get a full card; finished
 * ones are one line each, grouped by day, with their log one click away. The combined log is its own tab.
 */
@Component({
  selector: 'app-activity-page',
  imports: [FormsModule, RouterLink, JobCard, ClockPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <span class="eyebrow">Project</span>
          <h1>Activity</h1>
          <p class="muted">Jobs run one at a time, in the order they were queued.</p>
        </div>
        @if (store.status(); as st) {
          <span class="worker" [class.off]="!st.worker_online">
            <span class="dot" [class.on]="st.worker_online" [class.off]="!st.worker_online"></span>
            {{ st.worker_online ? 'Worker online' : 'Worker offline' }}</span>
        }
      </div>

      <div class="tiles stagger">
        <div class="tile" [class.live]="running().length"><span class="eyebrow">Running</span>
          <strong class="num">{{ running().length }}</strong><small>{{ running()[0]?.label ?? 'Nothing right now' }}</small></div>
        <div class="tile"><span class="eyebrow">Queued</span><strong class="num">{{ queued().length }}</strong>
          <small>{{ queued().length ? 'Waiting their turn' : 'Queue empty' }}</small></div>
        <div class="tile"><span class="eyebrow">Done today</span><strong class="num">{{ today().done }}</strong>
          <small>in the last 24 hours</small></div>
        <button class="tile as-button" [class.bad]="today().failed" (click)="showFailed()">
          <span class="eyebrow">Failed today</span><strong class="num">{{ today().failed }}</strong>
          <small>{{ today().failed ? 'See what went wrong' : 'Nothing failed' }}</small></button>
      </div>

      <div class="tabs view-tabs" role="tablist">
        <button role="tab" [class.on]="view() === 'jobs'" [attr.aria-selected]="view() === 'jobs'" (click)="setView('jobs')">Jobs</button>
        <button role="tab" [class.on]="view() === 'log'" [attr.aria-selected]="view() === 'log'" (click)="setView('log')">Log</button>
      </div>

      @if (view() === 'jobs') {
        @if (store.active().length) {
          <section>
            <h2>Now</h2>
            <div class="stack">
              @for (job of store.active(); track job.id) { <app-job-card [job]="job" [showLink]="true" /> }
            </div>
          </section>
        }

        <section>
          <div class="toolbar">
            <input class="find" type="search" placeholder="Find a job" [ngModel]="query()" (ngModelChange)="query.set($event); limit.set(page)"
                   aria-label="Find a job" />
            <div class="tabs" role="radiogroup" aria-label="Result">
              @for (s of states; track s.value) {
                <button role="radio" [class.on]="state() === s.value" [attr.aria-checked]="state() === s.value"
                        (click)="state.set(s.value); limit.set(page)">{{ s.label }} <span class="muted num">{{ stateCount(s.value) }}</span></button>
              }
            </div>
            <select class="area" [ngModel]="area()" (ngModelChange)="area.set($event); limit.set(page)" aria-label="Kind of job">
              <option value="">Every kind</option>
              @for (a of areas(); track a) { <option [value]="a">{{ a }}</option> }
            </select>
          </div>

          @for (day of days(); track day.label) {
            <h2 class="day">{{ day.label }} <span class="muted num">{{ day.jobs.length }}</span></h2>
            <ul class="jobs">
              @for (job of day.jobs; track job.id) {
                <li [class.failed]="job.state === 'failed'" [class.open]="open() === job.id">
                  <span class="icon" [class]="'icon ' + job.state" aria-hidden="true">
                    {{ job.state === 'succeeded' ? '✓' : job.state === 'failed' ? '!' : '–' }}</span>
                  <span class="what">
                    <span class="label">{{ job.label }}</span>
                    <span class="small muted">{{ kind(job) }} · {{ took(job) }} · {{ (job.finished_at ?? job.created_at) | clock }}</span>
                    @if (job.state === 'failed' && job.error) {
                      <span class="error small">{{ firstLine(job.error) }}</span>
                    }
                  </span>
                  <span class="actions">
                    @if (link(job); as l) { <a class="btn sm quiet" [routerLink]="l">Open</a> }
                    <button class="sm quiet" (click)="toggle(job.id)" [attr.aria-expanded]="open() === job.id">
                      {{ open() === job.id ? 'Hide' : 'Details' }}</button>
                  </span>
                  @if (open() === job.id) {
                    <div class="inline-card"><app-job-card [job]="job" [showLink]="false" /></div>
                  }
                </li>
              }
            </ul>
          } @empty {
            <div class="empty">{{ store.jobs().length ? 'No job matches.' : 'Nothing has run yet.' }}</div>
          }
          @if (moreCount() > 0) {
            <button class="sm quiet more" (click)="limit.set(limit() + page)">Show {{ moreCount() > page ? page : moreCount() }} more</button>
          }
        </section>
      }

      @if (view() === 'log') {
        <section class="combined">
          <div class="toolbar">
            <input class="find" type="search" placeholder="Find in the log" [ngModel]="logQuery()" (ngModelChange)="logQuery.set($event)"
                   aria-label="Find in the log" />
            <div class="tabs" role="radiogroup" aria-label="Level">
              @for (tab of levels; track tab.value) {
                <button role="radio" [class.on]="level() === tab.value" (click)="setLevel(tab.value)">{{ tab.label }}</button>
              }
            </div>
          </div>
          <div class="log full" role="log">
            @for (event of shownEvents(); track event.id) {
              <div class="line" [class]="event.level">
                <span class="time">{{ event.ts | clock }}</span>
                <span>
                  <span class="tag">{{ kindName(event.job_kind) }} #{{ event.job_id }}@if (event.stage) { · {{ event.stage }} }</span>
                  {{ event.message }}
                </span>
              </div>
            } @empty {
              <div class="muted">No log lines{{ level() === 'all' && !logQuery() ? ' yet' : ' match' }}.</div>
            }
          </div>
          <p class="small muted">The last 200 lines, newest first. The server's own log has the same lines
            (<span class="mono">docker compose logs -f worker</span>).</p>
        </section>
      }
    </div>
  `,
  styles: `
    section { margin-bottom: 28px; }
    h2 { margin: 0 0 12px; }
    .worker {
      display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 99px; font-weight: 600;
      font-size: 13px; background: var(--good-soft); color: var(--good);
      &.off { background: var(--bad-soft); color: var(--bad); }
    }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .tile {
      background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
      padding: 14px 16px; display: grid; gap: 2px; align-content: start; text-align: left; min-width: 0;
      strong { font-size: 26px; font-weight: 720; letter-spacing: -0.02em; }
      small { color: var(--muted); font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      &.live strong { color: var(--warn); }
      &.bad strong { color: var(--bad); }
      &.as-button { font: inherit; color: inherit; cursor: pointer; white-space: normal;
        &:hover { border-color: var(--line-strong); background: var(--surface); } }
    }
    .view-tabs { margin-bottom: 20px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px;
      .find { flex: 1 1 200px; width: auto; } .area { width: auto; } }
    .day { margin: 20px 0 8px; }
    .jobs {
      list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: var(--radius);
      background: var(--surface); overflow: hidden; box-shadow: var(--shadow-sm);
      li { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 10px 14px; border-top: 1px solid var(--line);
        transition: background var(--fast);
        &:first-child { border-top: 0; }
        &:hover, &.open { background: color-mix(in srgb, var(--sunken) 55%, transparent); } }
    }
    .icon {
      width: 24px; height: 24px; flex: none; display: grid; place-items: center; border-radius: 50%;
      font-size: 12px; font-weight: 800; background: var(--sunken); color: var(--muted);
      &.succeeded { background: var(--good-soft); color: var(--good); }
      &.failed { background: var(--bad-soft); color: var(--bad); }
    }
    .what { flex: 1; min-width: 0; display: grid; gap: 2px;
      .label { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } }
    .actions { display: flex; gap: 4px; }
    .error { color: var(--bad); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .inline-card { flex-basis: 100%; padding: 4px 0 6px; }
    .more { margin-top: 8px; }
    .combined .log.full { max-height: 640px; }
    .tag { color: var(--muted); margin-right: 6px; }
    @media (max-width: 600px) {
      .actions { width: 100%; justify-content: flex-end; }
    }
  `,
})
export class ActivityPage {
  private readonly api = inject(Api);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly store = inject(JobsStore);
  protected readonly page = PAGE;

  protected readonly view = signal<View>(this.route.snapshot.queryParamMap.get('view') === 'log' ? 'log' : 'jobs');
  protected readonly state = signal<StateFilter>('all');
  protected readonly area = signal('');
  protected readonly query = signal('');
  protected readonly limit = signal(PAGE);
  protected readonly open = signal<number | null>(null);

  protected readonly states: { value: StateFilter; label: string }[] = [
    { value: 'all', label: 'All' }, { value: 'succeeded', label: 'Done' },
    { value: 'failed', label: 'Failed' }, { value: 'cancelled', label: 'Cancelled' },
  ];

  protected readonly running = computed(() => this.store.jobs().filter((j) => j.state === 'running'));
  protected readonly queued = computed(() => this.store.jobs().filter((j) => j.state === 'queued'));
  protected readonly finished = computed(() =>
    this.store.jobs().filter((j) => j.state !== 'queued' && j.state !== 'running'));
  protected readonly today = computed(() => {
    const since = Date.now() - DAY;
    const recent = this.finished().filter((j) => Date.parse(j.finished_at ?? j.created_at) >= since);
    return { done: recent.filter((j) => j.state === 'succeeded').length, failed: recent.filter((j) => j.state === 'failed').length };
  });
  protected readonly areas = computed(() =>
    [...new Set(this.finished().map((j) => KIND_AREAS[j.kind] ?? 'Other'))].sort());

  private readonly matching = computed(() => {
    const words = this.query().toLowerCase().split(/\s+/).filter(Boolean);
    return this.finished().filter((j) =>
      (!this.area() || (KIND_AREAS[j.kind] ?? 'Other') === this.area())
      && words.every((w) => `${j.label} ${this.kind(j)} ${j.error ?? ''} #${j.id}`.toLowerCase().includes(w)));
  });
  private readonly filtered = computed(() =>
    this.state() === 'all' ? this.matching() : this.matching().filter((j) => j.state === this.state()));
  protected readonly moreCount = computed(() => this.filtered().length - this.limit());

  /** Finished jobs grouped by the day they ended: Today, Yesterday, then dates. */
  protected readonly days = computed(() => {
    const groups: { label: string; jobs: Job[] }[] = [];
    const startOfToday = new Date().setHours(0, 0, 0, 0);
    for (const job of this.filtered().slice(0, this.limit())) {
      const at = Date.parse(job.finished_at ?? job.created_at);
      const label = at >= startOfToday ? 'Today' : at >= startOfToday - DAY ? 'Yesterday'
        : new Date(at).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
      const last = groups[groups.length - 1];
      if (last?.label === label) last.jobs.push(job);
      else groups.push({ label, jobs: [job] });
    }
    return groups;
  });

  protected stateCount(state: StateFilter): number {
    return state === 'all' ? this.matching().length : this.matching().filter((j) => j.state === state).length;
  }

  // --- the combined log ------------------------------------------------------------------------

  protected readonly levels: { value: Level; label: string }[] = [
    { value: 'all', label: 'Everything' },
    { value: 'warning', label: 'Warnings and errors' },
    { value: 'error', label: 'Errors' },
  ];
  protected readonly level = signal<Level>('all');
  protected readonly logQuery = signal('');
  protected readonly events = signal<ActivityEvent[]>([]);
  protected readonly shownEvents = computed(() => {
    const words = this.logQuery().toLowerCase().split(/\s+/).filter(Boolean);
    return this.events().filter((e) => words.every((w) =>
      `${e.message} ${this.kindName(e.job_kind)} #${e.job_id} ${e.stage ?? ''}`.toLowerCase().includes(w)));
  });

  constructor() {
    // The log loads only when its tab is open, then follows running jobs.
    effect(() => {
      this.store.tick();
      this.store.finished();
      if (this.view() !== 'log') return;
      const busy = untracked(() => this.store.active().length > 0);
      if (busy || untracked(() => this.events().length === 0)) untracked(() => this.load());
    });
  }

  protected setView(view: View): void {
    this.view.set(view);
    this.router.navigate([], { queryParams: { view: view === 'jobs' ? null : view }, replaceUrl: true });
  }

  protected showFailed(): void {
    this.setView('jobs');
    this.state.set('failed');
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
