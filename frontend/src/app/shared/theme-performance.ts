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
                  @if (v.hold_score !== null || v.wpm) {
                    <p class="small muted">
                      @if (v.hold_score !== null) { Holds {{ v.hold_score }}/100 · first twist {{ v.first_twist === null ? 'none' : v.first_twist + ' s' }}
                        · longest gap {{ v.longest_gap === null ? '–' : v.longest_gap + ' s' }} · opens {{ v.opening }} }
                      @if (v.hold_score !== null && v.wpm) { · }
                      @if (v.wpm) { {{ v.wpm }} words a minute }
                    </p>
                  }
                  @if (v.story_template || v.hook_shape || v.starter !== null || v.riser !== null || v.first_visual_event !== null) {
                    <p class="small muted">Rendered as {{ v.look }}
                      @if (v.story_template) { · {{ v.story_template }} template }
                      @if (v.hook_shape) { · hook {{ v.hook_shape }} }
                      @if (v.starter !== null) { · starter {{ v.starter ? 'on' : 'off' }} }
                      @if (v.riser !== null) { · riser {{ v.riser ? 'on' : 'off' }} }
                      @if (v.first_visual_event !== null) { · first visual {{ v.first_visual_event.toFixed(1) }} s }
                    </p>
                  }
                  @if (v.retention; as r) {
                    <div class="watch">
                      <div class="figs">
                        <div><strong class="num">{{ pct(v.avg_viewed) }}</strong><small>average viewed</small></div>
                        <div [title]="r.why['kept_at_5s'] ?? ''"><strong class="num">{{ pct(v.kept_at_5s) }}</strong>
                          <small>still watching at 5 seconds</small></div>
                        <div [title]="r.why['kept_at_twist'] ?? ''"><strong class="num">{{ pct(v.kept_at_twist) }}</strong>
                          <small>still watching at the first twist</small></div>
                        <div [title]="r.why['kept_at_payoff'] ?? ''"><strong class="num">{{ pct(v.kept_at_payoff) }}</strong>
                          <small>still watching at the payoff</small></div>
                      </div>
                      @if (r.curve.length > 1) {
                        <svg class="curve" viewBox="0 0 300 100" preserveAspectRatio="none" role="img"
                             [attr.aria-label]="'Retention curve: ' + pct(v.avg_viewed) + ' viewed on average'">
                          <path class="area" [attr.d]="area(r.curve)" />
                          <path class="line" [attr.d]="line(r.curve)" />
                          @if (r.twist_at !== null) { <line class="mark" [attr.x1]="r.twist_at * 300" [attr.x2]="r.twist_at * 300" y1="0" y2="100" /> }
                          @if (r.payoff_at !== null) { <line class="mark payoff" [attr.x1]="r.payoff_at * 300" [attr.x2]="r.payoff_at * 300" y1="0" y2="100" /> }
                        </svg>
                        <p class="axis small muted"><span>0 s</span>
                          @if (r.twist_at !== null || r.payoff_at !== null) {
                            <span class="keys">@if (r.twist_at !== null) { <i class="k"></i> first twist } @if (r.payoff_at !== null) { <i class="k payoff"></i> payoff }</span>
                          }
                          <span>{{ r.duration_s === null ? '' : round(r.duration_s) + ' s' }}</span></p>
                      }
                      <ul class="lines small">@for (line of r.lines; track line) { <li>{{ line }}</li> }</ul>
                      @for (w of whys(r.why); track w) { <p class="small muted why">{{ w }}</p> }
                      <p class="small muted">YouTube Analytics, organic views · read {{ r.fetched_at | ago }}</p>
                    </div>
                  } @else if (d.stats_sources.analytics && hasYoutube(v)) {
                    <p class="small muted">No YouTube Analytics for it yet: read with the next refresh.</p>
                  }
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
        @if (d.unmatched.length) {
          <p class="small muted unmatched">Not matched to YouTube Analytics:
            @for (u of d.unmatched; track u.storyboard_id + u.why; let last = $last) { "{{ u.title }}" ({{ u.why }}){{ last ? '.' : '; ' }} }
          </p>
        }
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
    .watch { display: grid; gap: 8px; padding: 12px; border-radius: 12px; background: var(--sunken); }
    .figs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;
      div { display: grid; gap: 1px; min-width: 0; }
      strong { font-size: 20px; font-weight: 720; letter-spacing: -0.02em; }
      small { color: var(--muted); font-size: 12px; } }
    .curve { width: 100%; height: 96px; display: block; overflow: visible;
      .area { fill: color-mix(in srgb, var(--text) 8%, transparent); }
      .line { fill: none; stroke: var(--text); stroke-width: 1.8; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
      .mark { stroke: var(--warn); stroke-width: 1.5; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }
      .mark.payoff { stroke: var(--good); } }
    .axis { display: flex; justify-content: space-between; gap: 8px; margin: -4px 0 0; }
    .keys { display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
    .k { display: inline-block; width: 12px; border-top: 2px dashed var(--warn); &.payoff { border-color: var(--good); } }
    .why { margin: 0; }
    .unmatched { margin-top: 10px; }
    .proven { margin-top: 24px; ol { margin: 10px 0 0; padding-left: 20px; display: grid; gap: 4px; } }
    @media (max-width: 860px) {
      .cols { grid-template-columns: 1fr; }
      .more { padding-left: 16px; }
    }
    @media (max-width: 480px) { .figs strong { font-size: 17px; } }
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
    const from = [d.stats_sources.buffer ? 'Buffer' : '', d.stats_sources.youtube ? 'YouTube' : '',
      d.stats_sources.analytics ? 'YouTube Analytics' : ''].filter(Boolean);
    if (!from.length) return 'Connect Buffer or a YouTube key in .env';
    const list = from.length > 1 ? `${from.slice(0, -1).join(', ')} and ${from[from.length - 1]}` : from[0];
    return `From ${list}, once a day`;
  }

  protected pct(n: number | null): string {
    return n === null ? '—' : `${Math.round(n)}%`;
  }

  protected round(n: number): number {
    return Math.round(n);
  }

  protected hasYoutube(v: ThemeVideo): boolean {
    return !!v.published_url || v.networks.some((n) => n.service === 'youtube');
  }

  /** Why a figure is blank, once per reason. */
  protected whys(why: Record<string, string>): string[] {
    const names: Record<string, string> = {
      kept_at_5s: 'Five-second retention', kept_at_twist: 'First twist', kept_at_payoff: 'Payoff'
    };
    const seen = new Map<string, string[]>();
    for (const [k, reason] of Object.entries(why)) seen.set(reason, [...(seen.get(reason) ?? []), names[k] ?? k]);
    return [...seen].map(([reason, keys]) => `${keys.join(' and ')} not measured: ${reason}.`);
  }

  private top_(curve: [number, number][]): number {
    return Math.max(1, ...curve.map((p) => p[1]));
  }

  protected line(curve: [number, number][]): string {
    const top = this.top_(curve);
    return curve.map(([x, y], i) => `${i ? 'L' : 'M'}${(x * 300).toFixed(1)},${(100 - (100 * y) / top).toFixed(1)}`).join(' ');
  }

  protected area(curve: [number, number][]): string {
    const first = curve[0][0] * 300, last = curve[curve.length - 1][0] * 300;
    return `${this.line(curve)} L${last.toFixed(1)},100 L${first.toFixed(1)},100 Z`;
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
