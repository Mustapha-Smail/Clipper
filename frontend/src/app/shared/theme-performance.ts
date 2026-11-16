import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ThemeLearning, ThemeVideo } from '../core/models';
import { Toasts } from '../core/toast';

/** How a theme's videos did, ranked by arithmetic shown line by line, and the lessons the AI read in it. */
@Component({
  selector: 'app-theme-performance',
  imports: [RouterLink, AgoPipe],
  template: `
    @if (data(); as d) {
      <div class="tiles stagger">
        <div class="tile"><span class="eyebrow">Published</span><strong class="num">{{ d.videos.length }}</strong>
          <small>{{ d.with_views }} with numbers</small></div>
        <div class="tile"><span class="eyebrow">Views</span><strong class="num">{{ short(d.total_views) }}</strong>
          <small>across every network</small></div>
        <div class="tile"><span class="eyebrow">Median</span><strong class="num">{{ d.median_views === null ? '—' : short(d.median_views) }}</strong>
          <small>views per video</small></div>
        <div class="tile action">
          <button (click)="refresh()" [disabled]="refreshing()">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" /></svg>
            {{ refreshing() ? 'Reading…' : 'Refresh numbers' }}</button>
          <small>{{ sources(d) }}</small>
        </div>
      </div>

      <section class="card lessons">
        <div class="row spread">
          <div>
            <h3>What this theme taught you</h3>
            @if (d.insights; as l) {
              <p class="small muted">From {{ l.based_on }} videos, {{ l.made_at | ago }} ·
                <span class="pill" [class.approved]="l.confidence === 'high'" [class.review]="l.confidence === 'medium'">
                  {{ l.confidence }} confidence</span></p>
            }
          </div>
          <div class="row">
            <button (click)="learn()" [disabled]="!d.can_learn || learning()"
                    [title]="d.can_learn ? 'Ask the AI what the numbers say' : 'Needs 3 published videos with numbers'">
              {{ learning() ? 'Reading the numbers…' : d.insights ? 'Read them again' : 'Draw the lessons' }}</button>
            <button class="primary" (click)="buildOn.emit()" [disabled]="!d.proven.length">Ideas built on what works</button>
          </div>
        </div>
        @if (d.insights; as l) {
          <p class="summary">{{ l.summary }}</p>
          <div class="cols">
            <div><h4 class="good">Works</h4><ul>@for (x of l.works; track x) { <li>{{ x }}</li> }</ul></div>
            <div><h4 class="bad">Avoid</h4><ul>@for (x of l.avoid; track x) { <li>{{ x }}</li> } @empty { <li class="muted">Nothing clear yet</li> }</ul></div>
            <div><h4>Try next</h4><ul>@for (x of l.try_next; track x) { <li>{{ x }}</li> }</ul></div>
          </div>
        } @else if (!d.can_learn) {
          <p class="small muted">Lessons need at least 3 published videos with numbers. Until then, new ideas build on
            what you approved: {{ d.proven.length }} proven {{ d.proven.length === 1 ? 'example' : 'examples' }} so far.</p>
        }
      </section>

      <h2>Videos, best first</h2>
      @if (d.videos.length) {
        <div class="videos stagger">
          @for (v of d.videos; track v.storyboard_id; let i = $index) {
            <article class="card video" [class.open]="openId() === v.storyboard_id">
              <button class="vrow" (click)="toggle(v)" [attr.aria-expanded]="openId() === v.storyboard_id">
                <span class="rank num">{{ i + 1 }}</span>
                <span class="vmain">
                  <strong>{{ v.title }}</strong>
                  <span class="meter" aria-hidden="true"><span [style.width.%]="width(v)"></span></span>
                </span>
                <span class="nums">
                  <strong class="num">{{ v.views === null ? '—' : short(v.views) }}</strong>
                  <small>{{ v.vs_median === null ? 'no numbers' : v.vs_median + '× median' }}</small>
                </span>
              </button>
              @if (openId() === v.storyboard_id) {
                <div class="more">
                  <ul class="lines small">@for (line of v.lines; track line) { <li>{{ line }}</li> }</ul>
                  <div class="nets">
                    @for (n of v.networks; track n.name + (n.link ?? '')) {
                      <span class="net small">
                        @if (n.link) { <a [href]="n.link" target="_blank" rel="noopener">{{ n.name }}</a> } @else { {{ n.name }} }
                        <span class="num">{{ n.views === null ? '—' : short(n.views) }}</span>
                      </span>
                    }
                  </div>
                  <p class="small muted">{{ v.format === 'short' ? 'Short' : 'Long video' }} · {{ v.style }}
                    @if (v.seconds) { · {{ v.seconds }} s } @if (v.published_at) { · out {{ v.published_at | ago }} }
                    · <a [routerLink]="['/storyboards', v.storyboard_id]">open</a></p>
                  @if (v.format === 'long') {
                    <div class="row link">
                      <input #url type="text" [value]="v.published_url ?? ''" placeholder="Its YouTube link, for its numbers"
                             aria-label="YouTube link" />
                      <button class="sm" (click)="link(v, url.value)">Save link</button>
                    </div>
                  }
                </div>
              }
            </article>
          }
        </div>
      } @else {
        <div class="empty">Nothing published yet. Shorts posted through Buffer and long videos marked published show up
          here with their numbers.</div>
      }

      @if (d.proven.length) {
        <details class="fold proven">
          <summary class="muted">What new ideas build on ({{ d.proven.length }})</summary>
          <ol class="small">
            @for (p of d.proven; track p.n) { <li><strong>{{ p.title }}</strong> <span class="muted">— {{ p.evidence }}</span></li> }
          </ol>
        </details>
      }
    } @else {
      <div class="tiles"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>
    }
  `,
  styles: `
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .tile {
      background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
      padding: 14px 16px; display: grid; gap: 2px; align-content: start;
      strong { font-size: 26px; letter-spacing: -0.02em; font-weight: 720; }
      small { color: var(--muted); font-size: 12.5px; }
      &.action { align-content: center; justify-items: stretch; gap: 6px; button { justify-content: center; white-space: normal; } }
      svg { width: 16px; height: 16px; }
    }
    .lessons { display: grid; gap: 12px; }
    .summary { font-size: 15.5px; }
    .cols { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
    h4 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
    h4.good { color: var(--good); }
    h4.bad { color: var(--bad); }
    ul { margin: 0; padding-left: 18px; display: grid; gap: 4px; font-size: 14px; }
    .videos { display: grid; gap: 8px; }
    .video { padding: 0; overflow: hidden; }
    .vrow {
      width: 100%; display: flex; align-items: center; gap: 14px; padding: 12px 16px; border: 0; border-radius: 0;
      background: none; box-shadow: none; text-align: left; white-space: normal;
      &:hover:not(:disabled) { background: var(--sunken); }
      &:active:not(:disabled) { transform: none; }
    }
    .rank { flex: none; width: 26px; color: var(--faint); font-weight: 700; }
    .vmain { flex: 1; min-width: 0; display: grid; gap: 6px;
      strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } }
    .meter { height: 4px; border-radius: 99px; background: var(--sunken); overflow: hidden;
      span { display: block; height: 100%; border-radius: 99px; background: var(--accent);
        transition: width 0.6s var(--ease); } }
    .nums { flex: none; display: grid; justify-items: end; strong { font-size: 16px; } small { color: var(--muted); font-size: 12px; } }
    .more { padding: 0 16px 14px 56px; display: grid; gap: 8px; animation: page-in var(--med) var(--ease); }
    .lines { padding-left: 16px; }
    .nets { display: flex; flex-wrap: wrap; gap: 6px; }
    .net { display: inline-flex; gap: 6px; padding: 3px 10px; border-radius: 99px; background: var(--sunken); }
    .link input { flex: 1 1 260px; }
    .proven { margin-top: 24px; ol { margin: 10px 0 0; padding-left: 20px; display: grid; gap: 4px; } }
    @media (max-width: 860px) {
      .cols { grid-template-columns: 1fr; }
      .more { padding-left: 16px; }
    }
  `,
})
export class ThemePerformance {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly jobs = inject(JobsStore);

  readonly themeId = input.required<number>();
  readonly buildOn = output<void>();

  protected readonly data = signal<ThemeLearning | null>(null);
  protected readonly learning = signal(false);
  protected readonly refreshing = signal(false);
  protected readonly openId = signal<number | null>(null);
  private readonly waiting = signal<number | null>(null);
  private readonly top = computed(() => Math.max(1, ...(this.data()?.videos ?? []).map((v) => v.views ?? 0)));

  constructor() {
    effect(() => this.load(this.themeId()));
    // the statistics job finished: show its numbers
    effect(() => {
      const id = this.waiting();
      const job = id === null ? undefined : this.jobs.jobs().find((j) => j.id === id);
      if (job && !['queued', 'running'].includes(job.state)) {
        this.waiting.set(null);
        this.refreshing.set(false);
        if (job.state === 'failed') this.toasts.error(job.error || 'The numbers could not be read');
        this.load(this.themeId());
      }
    });
  }

  private load(id: number): void {
    this.api.themeLearning(id).subscribe({ next: (d) => this.data.set(d), error: (err) => this.toasts.error(err) });
  }

  protected short(n: number): string {
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
    return String(n);
  }

  protected width(v: ThemeVideo): number {
    return v.views ? Math.max(2, (100 * v.views) / this.top()) : 0;
  }

  protected sources(d: ThemeLearning): string {
    const from = [d.stats_sources.buffer ? 'Buffer' : '', d.stats_sources.youtube ? 'YouTube' : ''].filter(Boolean);
    return from.length ? `From ${from.join(' and ')}, once a day` : 'Connect Buffer or a YouTube key in .env';
  }

  protected toggle(v: ThemeVideo): void {
    this.openId.set(this.openId() === v.storyboard_id ? null : v.storyboard_id);
  }

  protected refresh(): void {
    this.refreshing.set(true);
    this.api.refreshStats().subscribe({
      next: (job) => {
        this.waiting.set(job.id);
        this.jobs.refresh();
        this.toasts.ok('Reading the numbers: this takes a few seconds');
      },
      error: (err) => {
        this.refreshing.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected learn(): void {
    this.learning.set(true);
    this.api.drawLessons(this.themeId()).subscribe({
      next: (d) => {
        this.learning.set(false);
        this.data.set(d);
      },
      error: (err) => {
        this.learning.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected link(v: ThemeVideo, url: string): void {
    this.api.linkPublished(v.storyboard_id, url.trim()).subscribe({
      next: () => {
        this.toasts.ok(url.trim() ? 'Linked: its numbers are read from YouTube' : 'Link removed');
        this.load(this.themeId());
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
