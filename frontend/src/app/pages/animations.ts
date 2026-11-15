import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, UntilPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { StoryboardListItem } from '../core/models';
import { Toasts } from '../core/toast';
import { byDate, byText, rememberedSort, sortBy, SortOption } from '../core/sorting';
import { PostsStore, handedOver, lastSent, nextDue, postLine } from '../core/posts-store';
import { PostScheduler } from '../shared/post-scheduler';

/** Ready Shorts handed to Buffer and manually published long videos leave Approved for Published. */
type Tab = 'review' | 'approved' | 'published' | 'rejected' | 'unrendered';
const TABS: Tab[] = ['review', 'approved', 'published', 'rejected', 'unrendered'];

/** Every animated video across scripts, sorted for review: Shorts and long videos alike. */
@Component({
  selector: 'app-animations-page',
  imports: [RouterLink, AgoPipe, UntilPipe, PostScheduler],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Animations</h1>
          <p class="muted">Review every Studio video here. Ready Shorts are scheduled from Approved; long videos are
            published manually from their Publish tab.</p>
        </div>
        <div class="head-tools">
        <div class="tabs" role="tablist">
          @for (t of tabs; track t.value) {
            <button role="tab" [class.on]="tab() === t.value" [attr.aria-selected]="tab() === t.value" (click)="setTab(t.value)">
              {{ t.label }}
              @if (t.value === 'review' && counts()[t.value]) {
                <span class="tab-badge num" [attr.aria-label]="counts()[t.value] + ' to review'">{{ counts()[t.value] }}</span>
              } @else {
                <span class="muted num">{{ counts()[t.value] }}</span>
              }</button>
          }
        </div>
        <select class="sort" [value]="sort()" (change)="setSort($any($event.target).value)" aria-label="Sort animations">
          @for (o of sorts[tab()]; track o.value) { <option [value]="o.value" [selected]="o.value === sort()">{{ o.label }}</option> }
        </select>
        </div>
      </div>

      @if (boards(); as all) {
        <div class="grid">
          @for (b of shown(); track b.id) {
            <article class="card item">
              <a class="item-link" [routerLink]="['/storyboards', b.id]">
                <img [class.wide]="b.format === 'long'" [src]="thumb(b)" alt="" loading="lazy" />
                <div class="body">
                  <strong>{{ b.title }}</strong>
                  <span class="small muted">{{ b.format === 'short' ? 'Short · 9:16' : 'Long video · 16:9' }} ·
                    {{ minutes(b.duration) }} · {{ b.scene_count }} scenes</span>
                  <span class="small muted">From “{{ b.script_title }}” · {{ (b.rendered_at ?? b.created_at) | ago }}</span>
                  @if (b.job) {
                    <span class="pill running">{{ b.job.message }}</span>
                  } @else if (b.has_video) {
                    <span class="pill" [class.approved]="b.review === 'approved'" [class.failed]="b.review === 'rejected'">
                      {{ b.review === 'pending' ? 'to review' : b.review }}</span>
                  } @else {
                    <span class="pill">{{ b.expired_at ? 'render deleted' : 'not rendered' }}</span>
                  }
                  @if (b.expires_at && !b.job) {
                    <span class="small muted">Deleted {{ b.expires_at | until }} unless approved</span>
                  }
                  @if (line(b); as text) { <span class="small posted">{{ text }}</span> }
                </div>
              </a>
              @if (tab() === 'approved' && b.format === 'short') {
                <div class="handoff">
                  @if (b.publish_ready) {
                    <app-post-scheduler kind="storyboard" [refId]="b.id" />
                  } @else {
                    <div class="notice small"><span>Finish the YouTube title and description before scheduling this Short.</span></div>
                    <a class="btn sm" [routerLink]="['/storyboards', b.id]" [queryParams]="{ tab: 'publish' }">
                      Complete Publish details</a>
                  }
                </div>
              }
            </article>
          } @empty {
            <div class="empty">{{ all.length ? (tab() === 'published' ? 'Nothing published yet. Schedule a ready Short here, or publish a long video manually from its Publish tab.' : 'Nothing in this list.') : 'No animations yet. Open a script, then its Videos tab.' }}</div>
          }
        </div>
      } @else {
        <div class="skeleton" style="height:200px"></div>
      }
    </div>
  `,
  styles: `
    .head-tools { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .sort { width: auto; }
    .posted { color: var(--good); font-weight: 600; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 1fr));
      gap: 12px;
    }
    .item {
      padding: 0;
      overflow: hidden;
      &:hover { border-color: var(--text); }
    }
    .item-link {
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr);
      gap: 12px;
      padding: 12px;
      color: inherit;
      text-decoration: none;
      align-items: start;
      img {
        width: 72px;
        aspect-ratio: 9 / 16;
        object-fit: cover;
        border-radius: 6px;
        background: var(--sunken);
        &.wide { aspect-ratio: 16 / 9; align-self: center; }
      }
    }
    .body {
      display: grid;
      gap: 4px;
      justify-items: start;
    }
    .handoff {
      display: grid;
      gap: 8px;
      padding: 12px;
      border-top: 1px solid var(--line);
    }
    app-post-scheduler { display: block; }
  `,
})
export class AnimationsPage {
  private readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);
  private readonly posts = inject(PostsStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly tabs: { value: Tab; label: string }[] = [
    { value: 'review', label: 'To review' }, { value: 'approved', label: 'Approved' },
    { value: 'published', label: 'Published' }, { value: 'rejected', label: 'Rejected' },
    { value: 'unrendered', label: 'Not rendered' },
  ];
  protected readonly tab = signal<Tab>(TABS.includes(this.route.snapshot.queryParamMap.get('tab') as Tab)
    ? this.route.snapshot.queryParamMap.get('tab') as Tab : 'review');
  protected readonly boards = signal<StoryboardListItem[] | null>(null);

  private bucket(b: StoryboardListItem): Tab {
    if (!b.has_video) return 'unrendered';
    if (b.review === 'approved' && (b.published_at || handedOver(this.postsOf(b)).length)) return 'published';
    return b.review === 'pending' ? 'review' : b.review;
  }

  // Orders that make sense for each tab; the first is the default.
  private readonly o = {
    newest: { value: 'new', label: 'Newest', compare: (a, b) => byDate(a.created_at, b.created_at) },
    oldest: { value: 'old', label: 'Oldest', compare: (a, b) => byDate(a.created_at, b.created_at, false) },
    rendered: { value: 'rendered', label: 'Recently rendered', compare: (a, b) => byDate(a.rendered_at, b.rendered_at) },
    expiring: { value: 'expiring', label: 'Deleted soonest', compare: (a, b) => byDate(a.expires_at, b.expires_at, false) },
    reviewed: { value: 'reviewed', label: 'Recently reviewed', compare: (a, b) => byDate(a.review_at, b.review_at) },
    title: { value: 'title', label: 'Title, A to Z', compare: (a, b) => byText(a.title, b.title) },
    script: { value: 'script', label: 'Script, A to Z', compare: (a, b) => byText(a.script_title, b.script_title) || byText(a.title, b.title) },
    longest: { value: 'long', label: 'Longest', compare: (a, b) => b.duration - a.duration },
    shortest: { value: 'short', label: 'Shortest', compare: (a, b) => a.duration - b.duration },
    shorts: { value: 'format', label: 'Shorts first', compare: (a, b) => byText(b.format, a.format) || byDate(a.created_at, b.created_at) },
  } satisfies Record<string, SortOption<StoryboardListItem>>;

  protected readonly sorts: Record<Tab, SortOption<StoryboardListItem>[]> = {
    review: [this.o.expiring, this.o.rendered, this.o.shorts, this.o.script, this.o.title, this.o.shortest, this.o.longest],
    approved: [this.o.reviewed, this.o.rendered, this.o.shorts, this.o.script, this.o.title, this.o.shortest, this.o.longest],
    published: [
      { value: 'soon', label: 'Posting soonest', compare: (a, b) => this.waitingFirst(a, b) || byDate(nextDue(this.postsOf(a)), nextDue(this.postsOf(b)), false) },
      { value: 'late', label: 'Posting latest', compare: (a, b) => this.waitingFirst(a, b) || byDate(nextDue(this.postsOf(a)), nextDue(this.postsOf(b))) },
      { value: 'posted', label: 'Recently posted', compare: (a, b) => byDate(lastSent(this.postsOf(a)), lastSent(this.postsOf(b))) },
      { value: 'asked', label: 'Recently scheduled', compare: (a, b) => byDate(this.postsOf(a)[0]?.created_at, this.postsOf(b)[0]?.created_at) },
      this.o.script, this.o.title,
    ],
    rejected: [this.o.expiring, this.o.reviewed, this.o.script, this.o.title],
    unrendered: [this.o.newest, this.o.oldest, this.o.script, this.o.title, this.o.shorts],
  };
  /** Each tab remembers its own order. */
  private readonly sortChoice = Object.fromEntries(this.tabs.map((t) =>
    [t.value, rememberedSort(`clipper.animations.sort.${t.value}`, this.sorts[t.value])])) as Record<Tab, ReturnType<typeof rememberedSort>>;
  protected readonly sort = computed(() => this.sortChoice[this.tab()]());

  protected readonly shown = computed(() => sortBy(
    (this.boards() ?? []).filter((b) => this.bucket(b) === this.tab()), this.sorts[this.tab()], this.sort()));
  protected readonly counts = computed(() => {
    const counts: Record<Tab, number> = { review: 0, approved: 0, published: 0, rejected: 0, unrendered: 0 };
    for (const b of this.boards() ?? []) counts[this.bucket(b)]++;
    return counts;
  });

  constructor() {
    this.posts.load(5_000);
    let known: string | null = null;      // null, so the list loads even when no job has ever run
    effect(() => {
      const signature = this.store.jobs().filter((j) => j.storyboard_id).map((j) => `${j.id}:${j.state}`).join();
      if (signature !== known) {
        known = signature;
        untracked(() => this.api.allStoryboards().subscribe({
          next: (list) => this.boards.set(list), error: (err) => this.toasts.error(err) }));
      }
    });
  }

  protected setSort(value: string): void {
    this.sortChoice[this.tab()].set(value);
  }

  protected setTab(tab: Tab): void {
    this.tab.set(tab);
    this.router.navigate([], { queryParams: { tab: tab === 'review' ? null : tab }, replaceUrl: true });
  }

  protected postsOf(b: StoryboardListItem) {
    return this.posts.byBoard().get(b.id) ?? [];
  }

  protected line(b: StoryboardListItem): string {
    if (b.published_at) return `Published manually ${this.relative(b.published_at)}`;
    return postLine(this.postsOf(b));
  }

  private relative(iso: string): string {
    const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`;
    return `${Math.floor(seconds / 86_400)} d ago`;
  }

  /** Videos still waiting to go out come before the ones already posted. */
  private waitingFirst(a: StoryboardListItem, b: StoryboardListItem): number {
    const done = (x: StoryboardListItem) => Number(!!lastSent(this.postsOf(x)) && !nextDue(this.postsOf(x)));
    return done(a) - done(b);
  }

  protected thumb(b: StoryboardListItem): string {
    return this.api.stillUrl(b.id, Math.min(2, b.duration / 3), b.rendered_at ?? b.created_at, 180);
  }

  protected minutes(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }
}
