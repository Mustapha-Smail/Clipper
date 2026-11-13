import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, LengthPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { Campaign, VideoSummary } from '../core/models';
import { Confirm } from '../core/confirm';
import { Toasts } from '../core/toast';
import { JobCard } from '../shared/job-card';

const RECENT_MS = 15 * 60 * 1000;

@Component({
  selector: 'app-videos-page',
  imports: [FormsModule, RouterLink, JobCard, LengthPipe, AgoPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Make clips from a video</h1>
          <p class="muted">Paste a link or pick a file. Claude chooses the moments; you review every clip.</p>
        </div>
      </div>

      <form class="card" (submit)="$event.preventDefault(); start()">
        <div class="row">
          <div class="grow">
            <input type="text" name="src" [(ngModel)]="src" autocomplete="off"
                   placeholder="Paste a video link, or a file path on the server" aria-label="Video link or file path" />
          </div>
          <button type="button" (click)="picker.click()" [disabled]="uploading() !== null">Upload a file</button>
          <button type="submit" class="primary" [disabled]="sending()">Create clips</button>
        </div>
        <input #picker type="file" accept="video/*,.mkv" hidden (change)="upload(picker)" />
        @if (uploading() !== null) {
          <p class="small muted" style="margin-top:10px">Uploading {{ uploadName() }}: {{ uploading() }}%</p>
        }

        <details class="fold" style="margin-top:14px" [open]="campaignId() !== null">
          <summary class="small muted">Options for this video</summary>
          <div class="stack">
            @if (campaigns().length) {
              <label class="field"><span>Campaign</span>
                <select name="campaign" [ngModel]="campaignId()" (ngModelChange)="pickCampaign($event)">
                  <option [ngValue]="null">None</option>
                  @for (c of campaigns(); track c.id) {
                    <option [ngValue]="c.id">{{ c.title }}</option>
                  }
                </select>
                <small>Its rules become the brief below.</small>
              </label>
            }
            <div class="option-grid">
              <label class="field"><span>Clips</span>
                <input type="number" name="n" min="1" max="20" [(ngModel)]="count" /></label>
              <label class="field"><span>Shortest (s)</span>
                <input type="number" name="min" min="5" max="600" [(ngModel)]="minLen" /></label>
              <label class="field"><span>Longest (s)</span>
                <input type="number" name="max" min="10" max="900" [(ngModel)]="maxLen" /></label>
            </div>
            <label class="field"><span>Campaign brief</span>
              <textarea name="brief" [(ngModel)]="brief"
                        placeholder="Optional. Paste the campaign's rules: required hashtags, banned topics, length limits, tone."></textarea>
              <small>Used to choose the moments and to check each clip before you post it.</small>
            </label>
          </div>
        </details>
      </form>

      @if (shownJobs().length) {
        <div class="stack" style="margin-top:12px">
          @for (job of shownJobs(); track job.id) {
            <app-job-card [job]="job" [showLink]="true" />
          }
        </div>
      }

      <h2>Videos</h2>
      @if (videos(); as list) {
        <div class="stack list">
          @for (video of list; track video.id) {
            <a class="card item" [routerLink]="['/videos', video.id]">
              <div class="grow">
                <div class="title">{{ video.title }}</div>
                <div class="small muted">
                  @if (video.uploader) { {{ video.uploader }} · }{{ video.duration | length }} · added {{ video.created_at | ago }}
                </div>
                @if (video.failed && video.error) {
                  <div class="small error-line">{{ video.error }}</div>
                }
              </div>
              @if (video.busy) {
                <span class="pill running">Working</span>
              } @else if (video.failed) {
                <span class="pill failed" [title]="video.error ?? ''">Failed</span>
                <button class="sm quiet danger" (click)="$event.preventDefault(); $event.stopPropagation(); removeFailed(video)"
                        [attr.aria-label]="'Delete ' + video.title">Delete</button>
              }
              <div class="counts small num">
                <div>{{ video.clip_count }} {{ video.clip_count === 1 ? 'clip' : 'clips' }}</div>
                <div class="muted">
                  @if (video.clip_count) {
                    {{ video.approved_count }} approved
                  } @else {
                    {{ video.moment_count ? 'not rendered' : 'no moments yet' }}
                  }
                </div>
              </div>
            </a>
          } @empty {
            <div class="empty">No videos yet. Paste a link above to make your first clips.</div>
          }
        </div>
      } @else {
        <div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>
      }
    </div>
  `,
  styles: `
    .item {
      display: flex;
      gap: 16px;
      align-items: center;
      padding: 14px 18px;
      text-decoration: none;
      transition: border-color 0.12s;
    }
    .item:hover {
      border-color: var(--text);
    }
    .title {
      font-weight: 650;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .counts {
      text-align: right;
      white-space: nowrap;
    }
    .error-line {
      color: var(--bad);
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    @media (max-width: 600px) {
      .item { flex-wrap: wrap; }
      .item .grow { flex-basis: 100%; }
      .counts { margin-left: auto; }
    }
  `,
})
export class VideosPage {
  private readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  protected readonly videos = signal<VideoSummary[] | null>(null);
  protected readonly src = signal('');
  protected readonly count = signal(5);
  protected readonly minLen = signal(20);
  protected readonly maxLen = signal(60);
  protected readonly brief = signal('');
  protected readonly sending = signal(false);
  protected readonly uploading = signal<number | null>(null);
  protected readonly uploadName = signal('');
  /** Campaigns you have not skipped, to clip a video for. */
  protected readonly campaigns = signal<Campaign[]>([]);
  protected readonly campaignId = signal<number | null>(null);
  private readonly route = inject(ActivatedRoute);

  /** Running and queued jobs, plus ones that ended in the last few minutes. */
  protected readonly shownJobs = computed(() =>
    this.store.jobs().filter((job) =>
      job.state === 'queued' || job.state === 'running' ||
      (job.finished_at !== null && Date.now() - new Date(job.finished_at).getTime() < RECENT_MS &&
        (job.kind === 'process' || job.state === 'failed'))).slice(0, 5));

  constructor() {
    this.load();
    this.api.settings().subscribe((bundle) => {
      this.count.set(bundle.settings.clips_per_video);
      this.minLen.set(bundle.settings.min_len);
      this.maxLen.set(bundle.settings.max_len);
    });
    this.api.campaigns().subscribe((list) => {
      this.campaigns.set(list.filter((c) => c.decision !== 'skipped'));
      const wanted = Number(this.route.snapshot.queryParamMap.get('campaign'));
      if (wanted) this.pickCampaign(wanted);
    });
    // A job creates the video row once the download starts, and changes counts when it ends.
    let known = '';
    effect(() => {
      const signature = this.store.jobs().map((j) => `${j.id}:${j.state}:${j.video_id}`).join();
      if (signature !== known) {
        known = signature;
        untracked(() => this.load());
      }
    });
  }

  /** A project that failed before any clip was made: delete it, its files and its failed jobs. */
  protected async removeFailed(video: VideoSummary): Promise<void> {
    if (!(await this.confirm.ask({
      title: 'Delete this failed video?',
      message: 'Its downloaded file, transcript and failed jobs are deleted. No clip was made from it.',
      confirm: 'Delete', danger: true }))) return;
    const done = {
      next: () => {
        this.toasts.ok('Deleted');
        this.store.refresh();
        this.load();
      },
      error: (err: unknown) => this.toasts.error(err),
    };
    if (video.failed_job_id) this.api.deleteJob(video.failed_job_id).subscribe(done);
    else this.api.deleteVideo(video.id).subscribe(done);
  }

  private load(): void {
    this.api.videos().subscribe({ next: (videos) => this.videos.set(videos), error: () => undefined });
  }

  protected pickCampaign(id: number | null): void {
    const campaign = this.campaigns().find((c) => c.id === id) ?? null;
    this.campaignId.set(campaign?.id ?? null);
    this.brief.set(campaign?.brief ?? '');
  }

  protected start(path?: string): void {
    const src = (path ?? this.src()).trim();
    if (!src) {
      this.toasts.error('Paste a link or a file path first');
      return;
    }
    this.sending.set(true);
    this.api.addVideo({ src, n: this.count(), min_len: this.minLen(), max_len: this.maxLen(), brief: this.brief(),
                        campaign_id: this.campaignId() })
      .subscribe({
        next: () => {
          this.src.set('');
          this.sending.set(false);
          this.toasts.ok('Queued');
          this.store.refresh();
        },
        error: (err) => {
          this.sending.set(false);
          this.toasts.error(err);
        },
      });
  }

  protected upload(picker: HTMLInputElement): void {
    const file = picker.files?.[0];
    if (!file) return;
    this.uploadName.set(file.name);
    this.uploading.set(0);
    this.api.upload(file).subscribe({
      next: (step) => {
        this.uploading.set(Math.round(step.progress * 100));
        if (step.path) this.start(step.path);
      },
      error: (err) => {
        this.uploading.set(null);
        picker.value = '';
        this.toasts.error(err);
      },
      complete: () => {
        this.uploading.set(null);
        picker.value = '';
      },
    });
  }
}
