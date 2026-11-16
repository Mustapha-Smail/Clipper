import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { byDate, byText, rememberedSort, sortBy, SortOption } from '../core/sorting';
import { THEME_STAGES, Theme, ThemeStage } from '../core/models';
import { ThemesStore } from '../core/themes-store';
import { Toasts } from '../core/toast';

type StatusFilter = 'active' | 'paused' | 'archived' | 'all';

/** Home of the Studio: every channel theme, how far its work is, and the one next step. */
@Component({
  selector: 'app-themes-page',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <span class="eyebrow">Studio</span>
          <h1>Your themes</h1>
          <p class="muted">One theme per channel. Each one keeps its ideas, scripts, videos and what they taught you.</p>
        </div>
        <button class="primary" (click)="openNew()">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>New theme</button>
      </div>

      @if (creating()) {
        <section class="card new" aria-label="New theme">
          <div class="new-grid">
            <label class="field"><span>Name</span>
              <input type="text" maxlength="300" [(ngModel)]="name" placeholder="For example: Odd rules of history"
                     (keydown.enter)="create()" /></label>
            <label class="field"><span>What it is about <em class="muted">(optional)</em></span>
              <input type="text" maxlength="2000" [(ngModel)]="about"
                     placeholder="Who it is for, the tone, what to avoid" /></label>
          </div>
          <div class="row">
            <button class="primary" (click)="create()" [disabled]="saving() || name().trim().length < 2">
              {{ saving() ? 'Creating…' : 'Create theme' }}</button>
            <button class="quiet" (click)="suggest()" [disabled]="suggesting()">
              {{ suggesting() ? 'Thinking…' : 'Suggest themes for me' }}</button>
            <button class="quiet" (click)="creating.set(false)">Cancel</button>
          </div>
          @if (suggestions(); as list) {
            <div class="suggestions stagger">
              @for (t of list; track t.theme) {
                <button type="button" class="suggestion" (click)="name.set(t.theme)">
                  <strong>{{ t.theme }}</strong>
                  <span class="small muted">{{ t.why }}</span>
                  <span class="small"><em>e.g. “{{ t.example }}”</em></span>
                </button>
              }
            </div>
          }
        </section>
      }

      @if (store.all(); as all) {
        @if (all.length) {
          <div class="toolbar">
            <label class="search">
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></svg>
              <input type="search" placeholder="Find a theme" [ngModel]="query()" (ngModelChange)="query.set($event)"
                     aria-label="Find a theme" />
            </label>
            <div class="tabs" role="radiogroup" aria-label="Status">
              @for (st of statuses; track st.value) {
                <button role="radio" [class.on]="status() === st.value" [attr.aria-checked]="status() === st.value"
                        (click)="status.set(st.value)">{{ st.label }} <span class="muted num">{{ statusCount(st.value) }}</span></button>
              }
            </div>
            <button class="chip-toggle" [class.on]="waitingOnly()" [attr.aria-pressed]="waitingOnly()"
                    (click)="waitingOnly.set(!waitingOnly())">Needs you</button>
            <select class="sort" [ngModel]="sort()" (ngModelChange)="sort.set($event)" aria-label="Sort themes">
              @for (o of sorts; track o.value) { <option [value]="o.value">{{ o.label }}</option> }
            </select>
          </div>
        }
        @if (shown().length) {
          <div class="grid stagger">
            @for (t of shown(); track t.id) {
              <a [class]="'card link theme hue-' + t.color" [class.dim]="t.status !== 'active'" [routerLink]="['/themes', t.id]">
                @if (api.bannerUrl(t); as src) {
                  <div class="banner"><img [src]="src" alt="" loading="lazy" /></div>
                } @else if (t.banner_job) {
                  <div class="banner drawing"><span class="small">Drawing the banner…</span></div>
                } @else {
                  <div class="glow" aria-hidden="true"></div>
                }
                <div class="top">
                  <span class="swatch"></span>
                  @if (t.status !== 'active') { <span class="pill">{{ t.status }}</span> }
                  @if (store.attention(t); as n) {
                    <span class="waiting small">{{ n }} waiting for you</span>
                  }
                </div>
                <h3>{{ t.name }}</h3>
                @if (t.description) { <p class="about small muted">{{ t.description }}</p> }

                <ol class="pipe" [attr.aria-label]="'Work by stage'">
                  @for (st of stages; track st.value) {
                    <li [class.has]="count(t, st.value)" [class.next]="t.next.stage === st.value"
                        [title]="st.label + ': ' + count(t, st.value)">
                      <span class="bar"></span>
                      <span class="lbl">{{ st.label }}</span>
                      <span class="n num">{{ count(t, st.value) }}</span>
                    </li>
                  }
                </ol>

                <div class="foot">
                  <span class="next">
                    <span class="arrow" aria-hidden="true">→</span> {{ t.next.text }}
                  </span>
                  @if (t.performance.published) {
                    <span class="perf small muted num">
                      {{ t.performance.published }} out ·
                      {{ t.performance.with_views ? views(t.performance.total_views) + ' views' : 'no numbers yet' }}</span>
                  }
                </div>
              </a>
            }
          </div>
        } @else if (all.length) {
          <div class="empty">No theme matches. <button class="sm quiet" (click)="clearFilters()">Show them all</button></div>
        } @else if (!creating()) {
          <div class="empty hero">
            <h3>Start with a theme</h3>
            <p class="muted">A theme is what every video of a channel is about. Create one, then generate ideas for it.</p>
            <button class="primary" (click)="openNew()">New theme</button>
          </div>
        }

      } @else {
        <div class="grid"><div class="skeleton tall"></div><div class="skeleton tall"></div><div class="skeleton tall"></div></div>
      }
    </div>
  `,
  styles: `
    .ico { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; }
    .new { margin-bottom: 24px; display: grid; gap: 14px; animation: page-in var(--med) var(--ease); }
    .new-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 12px; }
    .suggestions { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
    .suggestion {
      display: grid; gap: 4px; text-align: left; white-space: normal; align-content: start;
      padding: 12px 14px; height: 100%;
    }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 16px; }
    .skeleton.tall { height: 230px; }
    .theme {
      position: relative;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 18px 18px 16px;
      h3 { font-size: 17px; line-height: 1.3; letter-spacing: -0.015em; }
    }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }
    .search {
      flex: 1 1 220px; position: relative; display: block;
      svg { position: absolute; left: 11px; top: 50%; width: 16px; height: 16px; transform: translateY(-50%);
        fill: none; stroke: var(--muted); stroke-width: 2; stroke-linecap: round; pointer-events: none; }
      input { padding-left: 34px; }
    }
    .sort { width: auto; }
    .chip-toggle {
      border-radius: 99px; box-shadow: none; font-weight: 550; color: var(--muted);
      &.on { background: var(--accent-soft); color: var(--text); border-color: color-mix(in srgb, var(--accent) 60%, transparent); }
    }
    .theme.dim { opacity: 0.78; }
    .banner {
      margin: -18px -18px 4px;
      aspect-ratio: 10 / 3;
      background: var(--hue-soft);
      overflow: hidden;
      position: relative;
      img { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform 0.6s var(--ease); }
      &::after { content: ''; position: absolute; inset: auto 0 0 0; height: 40%;
        background: linear-gradient(transparent, color-mix(in srgb, var(--surface) 70%, transparent)); }
      &.drawing { display: grid; place-items: center; color: var(--muted);
        background: linear-gradient(90deg, var(--sunken), var(--hue-soft), var(--sunken)); background-size: 200% 100%;
        animation: shimmer 1.6s infinite linear; }
    }
    .theme:hover .banner img { transform: scale(1.03); }
    .glow {
      position: absolute;
      inset: -40% -20% auto auto;
      width: 220px;
      height: 220px;
      background: radial-gradient(closest-side, var(--hue-soft), transparent);
      pointer-events: none;
    }
    .top { display: flex; justify-content: space-between; align-items: center; position: relative; min-height: 22px; }
    .waiting { padding: 1px 9px; border-radius: 99px; background: var(--accent-soft); font-weight: 600; }
    .about { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .pipe {
      list-style: none;
      margin: 4px 0 0;
      padding: 0;
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 4px;
      li { display: grid; gap: 4px; min-width: 0; }
      .bar { height: 5px; border-radius: 99px; background: var(--sunken); transition: background var(--med); }
      .has .bar { background: color-mix(in srgb, var(--hue) 55%, var(--sunken)); }
      .next .bar { background: var(--hue); }
      .lbl { font-size: 10.5px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .n { font-size: 13px; font-weight: 650; color: var(--faint); }
      .has .n, .next .n { color: var(--text); }
    }
    .foot {
      margin-top: auto;
      padding-top: 10px;
      border-top: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .next { font-weight: 600; font-size: 14px; }
    .arrow { color: var(--hue); display: inline-block; transition: transform var(--med) var(--ease); }
    .theme:hover .arrow { transform: translateX(3px); }
    .hero { display: grid; gap: 10px; justify-items: center; padding: 64px 24px; }
    @media (max-width: 720px) {
      .new-grid { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
    }
  `,
})
export class ThemesPage {
  protected readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly router = inject(Router);
  protected readonly store = inject(ThemesStore);
  protected readonly stages = THEME_STAGES;

  protected readonly creating = signal(inject(ActivatedRoute).snapshot.queryParamMap.has('new'));
  protected readonly name = signal('');
  protected readonly about = signal('');
  protected readonly saving = signal(false);
  protected readonly suggesting = signal(false);
  protected readonly suggestions = signal<{ theme: string; why: string; example: string }[] | null>(null);

  protected readonly statuses: { value: StatusFilter; label: string }[] = [
    { value: 'active', label: 'Active' }, { value: 'paused', label: 'Paused' },
    { value: 'archived', label: 'Archived' }, { value: 'all', label: 'All' },
  ];
  protected readonly status = signal<StatusFilter>('active');
  protected readonly query = signal('');
  protected readonly waitingOnly = signal(false);
  protected readonly sorts: SortOption<Theme>[] = [
    { value: 'needs', label: 'Needs you most', compare: (a, b) => this.store.attention(b) - this.store.attention(a)
      || byDate(a.updated_at, b.updated_at) },
    { value: 'recent', label: 'Recently active', compare: (a, b) => byDate(a.updated_at, b.updated_at) },
    { value: 'views', label: 'Most views', compare: (a, b) => b.performance.total_views - a.performance.total_views },
    { value: 'published', label: 'Most published', compare: (a, b) => b.performance.published - a.performance.published },
    { value: 'name', label: 'Name, A to Z', compare: (a, b) => byText(a.name, b.name) },
    { value: 'newest', label: 'Newest', compare: (a, b) => byDate(a.created_at, b.created_at) },
  ];
  protected readonly sort = rememberedSort('clipper.themes.sort', this.sorts);

  protected readonly shown = computed(() => {
    const words = this.query().toLowerCase().split(/\s+/).filter(Boolean);
    const list = (this.store.all() ?? []).filter((t) =>
      (this.status() === 'all' || t.status === this.status())
      && (!this.waitingOnly() || this.store.attention(t) > 0)
      && words.every((w) => `${t.name} ${t.description}`.toLowerCase().includes(w)));
    return sortBy(list, this.sorts, this.sort());
  });

  protected statusCount(status: StatusFilter): number {
    const all = this.store.all() ?? [];
    return status === 'all' ? all.length : all.filter((t) => t.status === status).length;
  }

  protected clearFilters(): void {
    this.query.set('');
    this.status.set('all');
    this.waitingOnly.set(false);
  }

  constructor() {
    this.store.load();
  }

  protected count(theme: Theme, stage: ThemeStage): number {
    return stage === 'ideas' ? theme.counts.ideas + theme.inbox : theme.counts[stage];
  }

  protected views(n: number): string {
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
    return String(n);
  }

  protected openNew(): void {
    this.creating.set(true);
    this.suggestions.set(null);
  }

  protected create(): void {
    const name = this.name().trim();
    if (name.length < 2) return;
    this.saving.set(true);
    this.api.createTheme(name, this.about().trim()).subscribe({
      next: (theme) => {
        this.saving.set(false);
        this.store.put(theme);
        this.toasts.ok(`“${theme.name}” is ready. Generate its first ideas.`);
        this.router.navigate(['/themes', theme.id], { queryParams: { stage: 'ideas' } });
      },
      error: (err) => {
        this.saving.set(false);
        this.toasts.error(err);
      },
    });
  }

  /** Five themes from the AI, around what is typed if anything. */
  protected suggest(): void {
    this.suggesting.set(true);
    this.api.suggestThemes(this.name().trim()).subscribe({
      next: (list) => {
        this.suggesting.set(false);
        this.suggestions.set(list);
      },
      error: (err) => {
        this.suggesting.set(false);
        this.toasts.error(err);
      },
    });
  }
}
