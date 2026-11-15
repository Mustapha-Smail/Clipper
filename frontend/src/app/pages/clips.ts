import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, MegabytesPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ClipListItem, ClipStatus } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { PostsStore, handedOver, lastSent, nextDue, postLine } from '../core/posts-store';
import { SortOption, byDate, byText, rememberedSort, sortBy } from '../core/sorting';
import { BriefCheck } from '../shared/brief-check';
import { PostScheduler } from '../shared/post-scheduler';

/** Approved clips handed to Buffer leave Approved for Scheduled. */
type Filter = 'all' | 'scheduled' | ClipStatus;

const VERDICT_ORDER: Record<string, number> = { fail: 0, review: 1, pass: 2 };

/** Every rendered clip in one place: the queue to work through before posting. */
@Component({
  selector: 'app-clips-page',
  imports: [RouterLink, BriefCheck, PostScheduler, MegabytesPipe, AgoPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Review</h1>
          <p class="muted">Every clip is checked by you before it is posted. Approve the ones worth posting.</p>
        </div>
        <div class="head-tools">
          <div class="tabs" role="tablist">
            @for (tab of tabs; track tab.value) {
              <button role="tab" [class.on]="filter() === tab.value" [attr.aria-selected]="filter() === tab.value"
                      (click)="filter.set(tab.value)">
                {{ tab.label }} <span class="muted num">{{ counts()[tab.value] }}</span>
              </button>
            }
          </div>
          <select class="sort" [value]="sort()" (change)="setSort($any($event.target).value)" aria-label="Sort clips">
            @for (o of sorts[filter()]; track o.value) { <option [value]="o.value" [selected]="o.value === sort()">{{ o.label }}</option> }
          </select>
        </div>
      </div>

      @if (clips(); as all) {
        <div class="grid">
          @for (clip of shown(); track clip.id) {
            <article class="card clip" [class.landscape]="clip.format === 'horizontal'">
              @if (clip.file_exists) {
                <div class="player" [class.wide]="clip.format === 'horizontal'"><video controls preload="metadata" playsinline [src]="api.clipUrl(clip)"></video></div>
              } @else {
                <div class="player none" [class.wide]="clip.format === 'horizontal'">The clip file is missing from disk.</div>
              }
              <div class="body">
                <div class="row spread">
                  <span class="row tight">
                    <span class="pill" [class]="clip.status">{{ clip.status }}</span>
                    @if (clip.score) { <span class="small muted num">score {{ clip.score }}/10</span> }
                  </span>
                  <span class="small muted">{{ clip.rendered_at | ago }}</span>
                </div>
                @if (line(clip); as text) { <span class="small posted">{{ text }}</span> }
                <div class="title">{{ clip.title }}</div>
                <a class="small muted" [routerLink]="['/videos', clip.video_id]">{{ clip.video_title }}</a>
                <p class="small caption">{{ clip.caption }}</p>
                <div class="row">
                  <button class="sm" [class.on-good]="clip.status === 'approved'" (click)="mark(clip, 'approved')">
                    {{ clip.status === 'approved' ? 'Approved' : 'Approve' }}
                  </button>
                  <button class="sm" [class.on-bad]="clip.status === 'rejected'" (click)="mark(clip, 'rejected')">
                    {{ clip.status === 'rejected' ? 'Rejected' : 'Reject' }}
                  </button>
                  <button class="sm quiet" (click)="copy(clip)">Copy caption</button>
                  @if (clip.file_exists) {
                    <a class="btn sm quiet" [href]="api.clipUrl(clip, true)">Download ({{ clip.size_bytes | megabytes }})</a>
                  }
                  <button class="sm quiet danger" (click)="remove(clip)">Delete</button>
                </div>
              </div>
              @if (clip.compliance) {
                <app-brief-check class="wide" [result]="clip.compliance" />
              }
              @if (clip.status === 'approved' && clip.file_exists && clip.format === 'vertical') {
                <app-post-scheduler class="wide" kind="clip" [refId]="clip.id" />
              } @else if (clip.status === 'approved' && clip.file_exists) {
                <p class="small muted wide">Buffer accepts only vertical Shorts. Publish this horizontal clip manually.</p>
              }
            </article>
          } @empty {
            <div class="empty wide">
              {{ all.length ? (filter() === 'scheduled' ? 'Nothing scheduled yet. Approve a clip, then press Schedule on it.' : 'No clips with this status.') : 'No clips yet. Add a video to make some.' }}
            </div>
          }
        </div>
      } @else {
        <div class="stack"><div class="skeleton" style="height:200px"></div></div>
      }
    </div>
  `,
  styles: `
    .head-tools { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .sort { width: auto; }
    .tight { gap: 8px; }
    .posted { color: var(--good); font-weight: 600; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(420px, 100%), 1fr));
      gap: 12px;
    }
    .wide {
      grid-column: 1 / -1;
    }
    .clip {
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 18px;
      padding: 16px;
    }
    .clip {
      align-items: start;
    }
    /* A horizontal clip sits above its text: beside it, it would be too small to judge. */
    .clip.landscape {
      grid-template-columns: minmax(0, 1fr);
    }
    .body {
      display: grid;
      gap: 8px;
      align-content: start;
    }
    .title {
      font-weight: 650;
      line-height: 1.3;
    }
    .caption {
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    @media (max-width: 520px) {
      .grid {
        grid-template-columns: 1fr;
      }
      .clip:not(.landscape) {
        grid-template-columns: 110px minmax(0, 1fr);
      }
    }
  `,
})
export class ClipsPage {
  protected readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly posts = inject(PostsStore);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  protected readonly tabs: { value: Filter; label: string }[] = [
    { value: 'pending', label: 'To review' },
    { value: 'approved', label: 'Approved' },
    { value: 'scheduled', label: 'Scheduled' },
    { value: 'rejected', label: 'Rejected' },
    { value: 'all', label: 'All' },
  ];
  protected readonly filter = signal<Filter>('pending');
  protected readonly clips = signal<ClipListItem[] | null>(null);

  // Orders that make sense for each tab; the first is the default.
  private readonly newest: SortOption<ClipListItem> =
    { value: 'new', label: 'Newest render', compare: (a, b) => byDate(a.rendered_at, b.rendered_at) };
  private readonly oldest: SortOption<ClipListItem> =
    { value: 'old', label: 'Oldest render', compare: (a, b) => byDate(a.rendered_at, b.rendered_at, false) };
  private readonly best: SortOption<ClipListItem> =
    { value: 'score', label: 'Best score', compare: (a, b) => (b.score ?? 0) - (a.score ?? 0) };
  private readonly brief: SortOption<ClipListItem> =
    { value: 'brief', label: 'Brief problems first', compare: (a, b) => this.verdict(a) - this.verdict(b) };
  private readonly shortest: SortOption<ClipListItem> =
    { value: 'short', label: 'Shortest', compare: (a, b) => a.seconds - b.seconds };
  private readonly longest: SortOption<ClipListItem> =
    { value: 'long', label: 'Longest', compare: (a, b) => b.seconds - a.seconds };
  private readonly source: SortOption<ClipListItem> =
    { value: 'video', label: 'Source video, A to Z', compare: (a, b) => byText(a.video_title, b.video_title) };
  private readonly title: SortOption<ClipListItem> =
    { value: 'title', label: 'Title, A to Z', compare: (a, b) => byText(a.title, b.title) };

  protected readonly sorts: Record<Filter, SortOption<ClipListItem>[]> = {
    pending: [this.best, this.newest, this.oldest, this.brief, this.shortest, this.longest, this.source],
    approved: [this.best, this.newest, this.oldest, this.brief, this.shortest, this.longest, this.source],
    scheduled: [
      { value: 'soon', label: 'Posting soonest', compare: (a, b) => this.waitingFirst(a, b) || byDate(nextDue(this.postsOf(a)), nextDue(this.postsOf(b)), false) },
      { value: 'late', label: 'Posting latest', compare: (a, b) => this.waitingFirst(a, b) || byDate(nextDue(this.postsOf(a)), nextDue(this.postsOf(b))) },
      { value: 'posted', label: 'Recently posted', compare: (a, b) => byDate(lastSent(this.postsOf(a)), lastSent(this.postsOf(b))) },
      { value: 'asked', label: 'Recently scheduled', compare: (a, b) => byDate(this.postsOf(a)[0]?.created_at, this.postsOf(b)[0]?.created_at) },
      this.title,
    ],
    rejected: [this.newest, this.oldest, this.source, this.title],
    all: [this.newest, this.oldest, this.best, this.source, this.title],
  };
  /** Each tab remembers its own order. */
  private readonly sortChoice = Object.fromEntries(this.tabs.map((t) =>
    [t.value, rememberedSort(`clipper.review.sort.${t.value}`, this.sorts[t.value])])) as Record<Filter, ReturnType<typeof rememberedSort>>;
  protected readonly sort = computed(() => this.sortChoice[this.filter()]());

  protected readonly shown = computed(() => {
    const filter = this.filter();
    const list = (this.clips() ?? []).filter((c) => filter === 'all' || this.tabOf(c) === filter);
    return sortBy(list, this.sorts[filter], this.sort());
  });

  protected readonly counts = computed(() => {
    const counts: Record<Filter, number> = { pending: 0, approved: 0, scheduled: 0, rejected: 0, all: 0 };
    for (const clip of this.clips() ?? []) {
      counts[this.tabOf(clip)]++;
      counts.all++;
    }
    return counts;
  });

  constructor() {
    this.load();
    this.posts.load(5_000);
    effect(() => {
      if (this.store.finished().length) untracked(() => this.load());
    });
  }

  private load(): void {
    this.api.clips().subscribe({ next: (clips) => this.clips.set(clips), error: (err) => this.toasts.error(err) });
  }

  protected setSort(value: string): void {
    this.sortChoice[this.filter()].set(value);
  }

  protected postsOf(clip: ClipListItem) {
    return this.posts.byClip().get(clip.id) ?? [];
  }

  protected line(clip: ClipListItem): string {
    return postLine(this.postsOf(clip));
  }

  private tabOf(clip: ClipListItem): Exclude<Filter, 'all'> {
    if (clip.status === 'approved' && handedOver(this.postsOf(clip)).length) return 'scheduled';
    return clip.status;
  }

  /** Clips still waiting to go out come before the ones already posted. */
  private waitingFirst(a: ClipListItem, b: ClipListItem): number {
    return Number(!!lastSent(this.postsOf(a)) && !nextDue(this.postsOf(a)))
      - Number(!!lastSent(this.postsOf(b)) && !nextDue(this.postsOf(b)));
  }

  private verdict(clip: ClipListItem): number {
    return clip.compliance ? VERDICT_ORDER[clip.compliance.verdict] : 3;
  }

  protected mark(clip: ClipListItem, status: ClipStatus): void {
    const next = clip.status === status ? 'pending' : status;
    this.api.reviewClip(clip.id, next).subscribe({
      next: () => this.clips.update((clips) => (clips ?? []).map((c) => (c.id === clip.id ? { ...c, status: next } : c))),
      error: (err) => this.toasts.error(err),
    });
  }

  protected copy(clip: ClipListItem): void {
    navigator.clipboard.writeText(clip.caption).then(
      () => this.toasts.ok('Caption copied'),
      () => this.toasts.error('The browser blocked copying. Open the video to copy the caption by hand.'));
  }

  protected async remove(clip: ClipListItem): Promise<void> {
    if (!(await this.confirm.ask({ title: `Delete the clip "${clip.title}"?`, message: 'The moment stays and can be rendered again.',
      confirm: 'Delete', danger: true }))) return;
    this.api.deleteClip(clip.id).subscribe({
      next: () => {
        this.clips.update((clips) => (clips ?? []).filter((c) => c.id !== clip.id));
        this.toasts.ok('Clip deleted');
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
