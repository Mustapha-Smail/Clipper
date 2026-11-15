import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, MegabytesPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ClipListItem, ClipStatus } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { BriefCheck } from '../shared/brief-check';
import { PostScheduler } from '../shared/post-scheduler';

type Filter = 'all' | ClipStatus;

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
        <div class="tabs" role="tablist">
          @for (tab of tabs; track tab.value) {
            <button role="tab" [class.on]="filter() === tab.value" [attr.aria-selected]="filter() === tab.value"
                    (click)="filter.set(tab.value)">
              {{ tab.label }} <span class="muted num">{{ counts()[tab.value] }}</span>
            </button>
          }
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
                  <span class="pill" [class]="clip.status">{{ clip.status }}</span>
                  <span class="small muted">{{ clip.rendered_at | ago }}</span>
                </div>
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
              @if (clip.status === 'approved' && clip.format === 'vertical' && clip.file_exists) {
                <app-post-scheduler class="wide" kind="clip" [refId]="clip.id" />
              }
            </article>
          } @empty {
            <div class="empty wide">
              {{ all.length ? 'No clips with this status.' : 'No clips yet. Add a video to make some.' }}
            </div>
          }
        </div>
      } @else {
        <div class="stack"><div class="skeleton" style="height:200px"></div></div>
      }
    </div>
  `,
  styles: `
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
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  protected readonly tabs: { value: Filter; label: string }[] = [
    { value: 'pending', label: 'To review' },
    { value: 'approved', label: 'Approved' },
    { value: 'rejected', label: 'Rejected' },
    { value: 'all', label: 'All' },
  ];
  protected readonly filter = signal<Filter>('pending');
  protected readonly clips = signal<ClipListItem[] | null>(null);

  protected readonly shown = computed(() => {
    const filter = this.filter();
    return (this.clips() ?? []).filter((c) => filter === 'all' || c.status === filter);
  });

  protected readonly counts = computed(() => {
    const all = this.clips() ?? [];
    const count = (status: ClipStatus) => all.filter((c) => c.status === status).length;
    return { all: all.length, pending: count('pending'), approved: count('approved'), rejected: count('rejected') };
  });

  constructor() {
    this.load();
    effect(() => {
      if (this.store.finished().length) untracked(() => this.load());
    });
  }

  private load(): void {
    this.api.clips().subscribe({ next: (clips) => this.clips.set(clips), error: (err) => this.toasts.error(err) });
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
