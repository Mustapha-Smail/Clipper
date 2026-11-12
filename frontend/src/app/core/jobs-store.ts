import { Injectable, computed, inject, signal } from '@angular/core';
import { Api } from './api';
import { Job, SystemStatus } from './models';

const ACTIVE = new Set(['queued', 'running']);

/**
 * Keeps the job list and system status fresh for the whole app by polling:
 * fast while something is running, slow when idle. Pages read the signals and
 * react to `finished` to reload what a job changed.
 */
@Injectable({ providedIn: 'root' })
export class JobsStore {
  private readonly api = inject(Api);

  readonly jobs = signal<Job[]>([]);
  readonly status = signal<SystemStatus | null>(null);
  readonly offline = signal(false);
  /** Jobs that reached a final state in the latest poll. */
  readonly finished = signal<Job[]>([]);
  /** Increments on every poll, so log views know when to fetch new lines. */
  readonly tick = signal(0);

  readonly active = computed(() => this.jobs().filter((j) => ACTIVE.has(j.state)));

  private timer: ReturnType<typeof setTimeout> | undefined;
  private polls = 0;

  start(): void {
    this.poll();
  }

  /** Poll now, for instance right after queueing a job. */
  refresh(): void {
    clearTimeout(this.timer);
    this.polls = 0;           // read the status again too: settings may have changed
    this.poll();
  }

  isBusy(videoId: string): boolean {
    return this.active().some((j) => j.video_id === videoId);
  }

  private poll(): void {
    this.api.jobs().subscribe({
      next: (jobs) => {
        const before = new Map(this.jobs().map((j) => [j.id, j.state]));
        const done = jobs.filter((j) => !ACTIVE.has(j.state) && ACTIVE.has(before.get(j.id) ?? ''));
        this.jobs.set(jobs);
        this.offline.set(false);
        this.tick.update((n) => n + 1);
        if (done.length) this.finished.set(done);
        this.schedule();
      },
      error: () => {
        this.offline.set(true);
        this.schedule();
      },
    });
    // Status changes slowly: refresh it on every fifth poll.
    if (this.polls++ % 5 === 0) {
      this.api.status().subscribe({ next: (s) => this.status.set(s), error: () => undefined });
    }
  }

  private schedule(): void {
    this.timer = setTimeout(() => this.poll(), this.active().length ? 1200 : 4000);
  }
}
