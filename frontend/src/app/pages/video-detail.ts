import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Observable, switchMap, tap } from 'rxjs';
import { Api, errorMessage } from '../core/api';
import { LengthPipe, MegabytesPipe, formatTime, parseTime } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { AppSettings, Campaign, ClipFormat, ClipStatus, Moment, MomentInput, SubtitleMode, VideoDetail } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { BriefCheck } from '../shared/brief-check';
import { CostReportView } from '../shared/cost-report';
import { JobCard } from '../shared/job-card';
import { PostScheduler } from '../shared/post-scheduler';

/** The editable copy of a moment shown in a card. `moment` is the saved version. */
interface Draft {
  key: number;
  id: number | null;
  title: string;
  hook: string;
  start: string;
  end: string;
  caption: string;
  moment: Moment | null;
}

const MODE_LABELS: Record<SubtitleMode, string> = {
  cover: 'Blur them',
  crop: 'Crop the frame above them',
  keep: 'Keep them and add no captions',
  ignore: 'Leave them as they are',
};

const FORMAT_LABELS: Record<ClipFormat, string> = {
  vertical: 'Vertical 9:16',
  horizontal: 'Horizontal 16:9',
};

@Component({
  selector: 'app-video-detail-page',
  imports: [FormsModule, RouterLink, JobCard, BriefCheck, PostScheduler, LengthPipe, MegabytesPipe, CostReportView],
  templateUrl: './video-detail.html',
  styleUrl: './video-detail.scss',
})
export class VideoDetailPage {
  protected readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly router = inject(Router);

  /** Route parameter. */
  readonly id = input.required<string>();

  protected readonly video = signal<VideoDetail | null>(null);
  protected readonly loadError = signal<string | null>(null);
  protected readonly drafts = signal<Draft[]>([]);
  protected readonly settings = signal<AppSettings | null>(null);
  protected readonly briefText = signal('');
  protected readonly campaigns = signal<Campaign[]>([]);
  protected readonly count = signal(5);
  protected readonly minLen = signal(20);
  protected readonly maxLen = signal(60);
  protected readonly modes = Object.entries(MODE_LABELS) as [SubtitleMode, string][];
  protected readonly formatLabels = FORMAT_LABELS;
  protected readonly formats = Object.entries(FORMAT_LABELS) as [ClipFormat, string][];
  private nextKey = 1;

  protected readonly busy = computed(() => this.store.isBusy(this.id()));
  protected readonly briefChanged = computed(() => this.briefText().trim() !== (this.video()?.brief ?? ''));
  protected readonly hasClips = computed(() => this.drafts().some((d) => d.moment?.clip));

  /** Active jobs for this video, or its latest job if that one failed. */
  protected readonly jobs = computed(() => {
    const mine = this.store.jobs().filter((j) => j.video_id === this.id());
    const active = mine.filter((j) => j.state === 'queued' || j.state === 'running');
    if (active.length) return active;
    return mine.length && mine[0].state === 'failed' ? [mine[0]] : [];
  });

  protected readonly defaultModeLabel = computed(() => {
    const mode = this.settings()?.subtitle_mode;
    return mode ? MODE_LABELS[mode].toLowerCase() : '';
  });

  protected readonly defaultFormatLabel = computed(() => {
    const format = this.settings()?.clip_format;
    return format ? FORMAT_LABELS[format].toLowerCase() : '';
  });

  /** A card is laid out for the clip it holds, or for the shape the next render will have. */
  protected wide(draft: Draft): boolean {
    return (draft.moment?.clip?.format ?? this.video()?.effective_clip_format) === 'horizontal';
  }

  constructor() {
    this.api.settings().subscribe((bundle) => {
      this.settings.set(bundle.settings);
      this.count.set(bundle.settings.clips_per_video);
      this.minLen.set(bundle.settings.min_len);
      this.maxLen.set(bundle.settings.max_len);
    });
    this.api.campaigns().subscribe((list) => this.campaigns.set(list.filter((c) => c.decision !== 'skipped')));
    effect(() => {
      this.id();
      untracked(() => this.load(true));
    });
    // When a job on this video ends, pick up what it changed. A new selection
    // replaces the cards; a render or check only refreshes the clip on each
    // card, so text being typed elsewhere is left alone.
    effect(() => {
      const mine = this.store.finished().filter((j) => j.video_id === this.id());
      if (mine.length) untracked(() => this.load(mine.some((j) => j.kind === 'process' || j.kind === 'select')));
    });
  }

  private load(rebuild: boolean): void {
    this.api.video(this.id()).subscribe({
      next: (video) => this.apply(video, rebuild),
      error: (err) => this.loadError.set(errorMessage(err)),
    });
  }

  private apply(video: VideoDetail, rebuild: boolean): void {
    const first = this.video() === null;
    this.video.set(video);
    this.loadError.set(null);
    if (first || rebuild) {
      this.briefText.set(video.brief);
      this.drafts.set(video.moments.map((m) => this.toDraft(m)));
    } else {
      const saved = new Map(video.moments.map((m) => [m.id, m]));
      this.drafts.update((drafts) => drafts.map((d) => (d.id !== null && saved.has(d.id) ? { ...d, moment: saved.get(d.id)! } : d)));
    }
  }

  private toDraft(moment: Moment): Draft {
    return {
      key: this.nextKey++, id: moment.id, title: moment.title, hook: moment.hook, caption: moment.caption,
      start: formatTime(moment.start), end: formatTime(moment.end), moment,
    };
  }

  protected span(draft: Draft): string {
    const length = parseTime(draft.end) - parseTime(draft.start);
    return Number.isNaN(length) ? '' : `${length.toFixed(1)} s long`;
  }

  protected edited(draft: Draft): boolean {
    const m = draft.moment;
    return !m || m.title !== draft.title || m.hook !== draft.hook || m.caption !== draft.caption ||
      formatTime(m.start) !== draft.start || formatTime(m.end) !== draft.end;
  }

  // --- saving and rendering -----------------------------------------------------

  private collect(): MomentInput[] {
    return this.drafts().map((draft, i) => {
      const start = parseTime(draft.start);
      const end = parseTime(draft.end);
      if (Number.isNaN(start) || Number.isNaN(end)) {
        throw new Error(`Clip ${i + 1}: write times as m:ss, for example 4:36.5`);
      }
      return { id: draft.id, start, end, title: draft.title, hook: draft.hook, caption: draft.caption };
    });
  }

  /** Save every card; the answer comes back in the same order, so new cards get their ids. */
  private saveAll(): Observable<VideoDetail> {
    return this.api.saveMoments(this.id(), this.collect()).pipe(
      tap((video) => {
        this.video.set(video);
        this.drafts.update((drafts) => drafts.map((d, i) => ({ ...this.toDraft(video.moments[i]), key: d.key })));
      }),
    );
  }

  private run(action: () => Observable<unknown>, done?: string): void {
    try {
      action().subscribe({
        next: () => {
          if (done) this.toasts.ok(done);
          this.store.refresh();
        },
        error: (err) => this.toasts.error(err),
      });
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected save(): void {
    this.run(() => this.saveAll(), 'Saved');
  }

  protected renderAll(): void {
    this.run(() => this.saveAll().pipe(switchMap(() => this.api.render(this.id(), null))));
  }

  protected renderOne(draft: Draft): void {
    const index = this.drafts().indexOf(draft);
    this.run(() => this.saveAll().pipe(
      switchMap((video) => this.api.render(this.id(), [video.moments[index].id]))));
  }

  protected addMoment(): void {
    this.drafts.update((drafts) => [...drafts, {
      key: this.nextKey++, id: null, title: '', hook: '', start: '', end: '', caption: '', moment: null }]);
  }

  protected async removeMoment(draft: Draft): Promise<void> {
    const warning = draft.moment?.clip ? 'Remove this moment and delete its rendered clip?' : 'Remove this moment?';
    if (!(await this.confirm.ask({ title: warning, confirm: 'Remove', danger: true }))) return;
    this.drafts.update((drafts) => drafts.filter((d) => d !== draft));
    if (draft.id !== null) this.save();
  }

  // --- review ---------------------------------------------------------------------

  protected mark(draft: Draft, status: ClipStatus): void {
    const clip = draft.moment?.clip;
    if (!clip) return;
    const next = clip.status === status ? 'pending' : status;
    this.api.reviewClip(clip.id, next).subscribe({
      next: (updated) => this.drafts.update((drafts) => drafts.map((d) =>
        d === draft && d.moment ? { ...d, moment: { ...d.moment, clip: updated } } : d)),
      error: (err) => this.toasts.error(err),
    });
  }

  protected copyCaption(draft: Draft): void {
    navigator.clipboard.writeText(draft.caption).then(
      () => this.toasts.ok('Caption copied'),
      () => this.toasts.error('The browser blocked copying. Select the text and copy it by hand.'));
  }

  protected checkAll(): void {
    this.run(() => this.api.check(this.id(), null));
  }

  protected checkOne(draft: Draft): void {
    const clip = draft.moment?.clip;
    if (clip) this.run(() => this.api.check(this.id(), [clip.id]));
  }

  // --- video-level settings ---------------------------------------------------------

  protected saveBrief(): void {
    this.api.updateVideo(this.id(), { brief: this.briefText() }).subscribe({
      next: (video) => {
        this.video.set(video);
        this.briefText.set(video.brief);
        this.toasts.ok('Brief saved');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected async setCampaign(id: number | null): Promise<void> {
    const replace = id !== null && (!this.video()?.brief ||
      await this.confirm.ask({ title: "Replace this video's brief?", message: "The campaign's rules replace the brief you have now.",
        confirm: 'Replace', cancel: 'Keep my brief' }));
    this.api.updateVideo(this.id(), { campaign_id: id, use_campaign_brief: replace }).subscribe({
      next: (video) => {
        this.video.set(video);
        this.briefText.set(video.brief);
        this.toasts.ok(id === null ? 'Campaign unlinked' : 'Campaign linked');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected setSubtitleMode(value: string): void {
    this.api.updateVideo(this.id(), { subtitle_mode: value as SubtitleMode | 'default' }).subscribe({
      next: (video) => {
        this.video.set(video);
        this.toasts.ok('Saved. It applies the next time you render.');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected setClipFormat(value: string): void {
    this.api.updateVideo(this.id(), { clip_format: value as ClipFormat | 'default' }).subscribe({
      next: (video) => {
        this.video.set(video);
        this.toasts.ok('Saved. It applies the next time you render.');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected detectSubtitles(): void {
    this.run(() => this.api.detectSubtitles(this.id()));
  }

  protected reselect(fold: HTMLDetailsElement): void {
    this.run(() => this.api.select(this.id(), { n: this.count(), min_len: this.minLen(), max_len: this.maxLen() })
      .pipe(tap(() => (fold.open = false))));
  }

  protected async deleteSourceFile(): Promise<void> {
    const video = this.video();
    if (!video || !(await this.confirm.ask({ title: `Delete the source video file (${video.file_mb} MB)?`,
      message: 'Clips, transcript and moments are kept, but you cannot render new clips until you add the video again.',
      confirm: 'Delete the file', danger: true }))) return;
    this.api.deleteSourceFile(this.id()).subscribe({
      next: () => {
        this.toasts.ok('Source file deleted');
        this.load(false);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected async deleteVideo(): Promise<void> {
    const video = this.video();
    if (!video) return;
    const clips = video.clip_count ? ` and its ${video.clip_count} rendered ${video.clip_count === 1 ? 'clip' : 'clips'}` : '';
    if (!(await this.confirm.ask({ title: `Delete "${video.title}"${clips}?`, message: 'This cannot be undone.',
      confirm: 'Delete', danger: true }))) return;
    this.api.deleteVideo(this.id()).subscribe({
      next: () => {
        this.toasts.ok('Video deleted');
        this.router.navigateByUrl('/');
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
