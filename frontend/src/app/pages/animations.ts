import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, UntilPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { StoryboardListItem } from '../core/models';
import { Toasts } from '../core/toast';
import { byDate, byText, rememberedSort, sortBy, SortOption } from '../core/sorting';

type Tab = 'review' | 'approved' | 'rejected' | 'unrendered';

/** Every animated video across scripts, sorted for review: Shorts and long videos alike. */
@Component({
  selector: 'app-animations-page',
  imports: [RouterLink, AgoPipe, UntilPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Animations</h1>
          <p class="muted">Every Short and long video made from your scripts. Watch each one before you post it: the app
            never posts for you.</p>
        </div>
        <div class="head-tools">
        <div class="tabs" role="tablist">
          @for (t of tabs; track t.value) {
            <button role="tab" [class.on]="tab() === t.value" [attr.aria-selected]="tab() === t.value" (click)="tab.set(t.value)">
              {{ t.label }}
              @if (t.value === 'review' && counts()[t.value]) {
                <span class="tab-badge num" [attr.aria-label]="counts()[t.value] + ' to review'">{{ counts()[t.value] }}</span>
              } @else {
                <span class="muted num">{{ counts()[t.value] }}</span>
              }</button>
          }
        </div>
        <select class="sort" [value]="sort()" (change)="sort.set($any($event.target).value)" aria-label="Sort animations">
          @for (o of sorts; track o.value) { <option [value]="o.value" [selected]="o.value === sort()">{{ o.label }}</option> }
        </select>
        </div>
      </div>

      @if (boards(); as all) {
        <div class="grid">
          @for (b of shown(); track b.id) {
            <a class="card item" [routerLink]="['/storyboards', b.id]">
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
              </div>
            </a>
          } @empty {
            <div class="empty">{{ all.length ? 'Nothing in this list.' : 'No animations yet. Open a script, then its Videos tab.' }}</div>
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
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 1fr));
      gap: 12px;
    }
    .item {
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr);
      gap: 12px;
      padding: 12px;
      text-decoration: none;
      align-items: start;
      &:hover { border-color: var(--text); }
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
  `,
})
export class AnimationsPage {
  private readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);

  protected readonly tabs: { value: Tab; label: string }[] = [
    { value: 'review', label: 'To review' }, { value: 'approved', label: 'Approved' },
    { value: 'rejected', label: 'Rejected' }, { value: 'unrendered', label: 'Not rendered' },
  ];
  protected readonly tab = signal<Tab>('review');
  protected readonly boards = signal<StoryboardListItem[] | null>(null);

  private bucket(b: StoryboardListItem): Tab {
    if (!b.has_video) return 'unrendered';
    return b.review === 'pending' ? 'review' : b.review;
  }

  protected readonly sorts: SortOption<StoryboardListItem>[] = [
    { value: 'new', label: 'Newest', compare: (a, b) => byDate(a.created_at, b.created_at) },
    { value: 'old', label: 'Oldest', compare: (a, b) => byDate(a.created_at, b.created_at, false) },
    { value: 'rendered', label: 'Recently rendered', compare: (a, b) => byDate(a.rendered_at, b.rendered_at) },
    { value: 'expiring', label: 'Deleted soonest', compare: (a, b) => byDate(a.expires_at, b.expires_at, false) },
    { value: 'title', label: 'Title, A to Z', compare: (a, b) => byText(a.title, b.title) },
    { value: 'script', label: 'Script, A to Z', compare: (a, b) => byText(a.script_title, b.script_title) || byText(a.title, b.title) },
    { value: 'long', label: 'Longest', compare: (a, b) => b.duration - a.duration },
    { value: 'short', label: 'Shortest', compare: (a, b) => a.duration - b.duration },
    { value: 'format', label: 'Shorts first', compare: (a, b) => byText(b.format, a.format) || byDate(a.created_at, b.created_at) },
  ];
  protected readonly sort = rememberedSort('clipper.animations.sort', this.sorts);
  protected readonly shown = computed(() => sortBy(
    (this.boards() ?? []).filter((b) => this.bucket(b) === this.tab()), this.sorts, this.sort()));
  protected readonly counts = computed(() => {
    const counts: Record<Tab, number> = { review: 0, approved: 0, rejected: 0, unrendered: 0 };
    for (const b of this.boards() ?? []) counts[this.bucket(b)]++;
    return counts;
  });

  constructor() {
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

  protected thumb(b: StoryboardListItem): string {
    return this.api.stillUrl(b.id, Math.min(2, b.duration / 3), b.rendered_at ?? b.created_at, 180);
  }

  protected minutes(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }
}
