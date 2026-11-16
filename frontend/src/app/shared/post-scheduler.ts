import { Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, errorMessage } from '../core/api';
import { Confirm } from '../core/confirm';
import { JobsStore } from '../core/jobs-store';
import { BufferChannel, Post, PostMode, PostingStatus, PostStatus } from '../core/models';
import { PostsStore, SERVICE_NAMES, WAITING, dayKeyOf } from '../core/posts-store';
import { WhenPicker, nextSlot } from './when-picker';
import { Toasts } from '../core/toast';

const SERVICE = SERVICE_NAMES;
const STATUS: Record<PostStatus, [string, string]> = {
  queued: ['Sending to Buffer', 'running'],
  draft: ['Draft in Buffer', ''],
  scheduled: ['Scheduled', 'pass'],
  sending: ['Posting now', 'running'],
  sent: ['Posted', 'approved'],
  failed: ['Failed', 'failed'],
  cancelled: ['Cancelled', ''],
  removed: ['Deleted in Buffer', ''],
};
const LIMITS: Record<string, number> = { instagram: 2196, tiktok: 2200, youtube: 5000 };

/**
 * Schedule an approved Short through the feature's Buffer account, and show where
 * its posts stand. Long and horizontal videos are published manually.
 */
@Component({
  selector: 'app-post-scheduler',
  imports: [FormsModule, WhenPicker],
  template: `
    <div class="box">
      <div class="row spread">
        <strong class="small">Post it</strong>
        <span class="row tight">
          @if (undoable().length > 1) {
            <button class="sm quiet danger" (click)="undoAll()" [disabled]="busy()"
                    title="Delete every waiting post of this video from Buffer">Undo all</button>
          }
          @if (!open()) {
            <button class="sm" (click)="start()">{{ mine().length ? 'Schedule again' : 'Schedule' }}</button>
          }
        </span>
      </div>

      @for (post of mine(); track post.id) {
        <div class="post small">
          <span class="where">{{ service(post.service) }} <span class="muted">{{ post.channel_name }}</span></span>
          <span class="pill" [class]="label(post)[1]">{{ label(post)[0] }}</span>
          <span class="muted grow">
            @if (post.status === 'sent') {
              {{ time(post.sent_at) }}
              @if (post.link) { · <a [href]="post.link" target="_blank" rel="noopener">Open the post</a> }
            } @else if (post.status === 'failed') {
              {{ post.error }}
            } @else if (post.status === 'draft') {
              Schedule it in Buffer when you are ready
            } @else if (post.due_at) {
              {{ time(post.due_at) }}
            } @else if (post.status === 'queued') {
              Uploading the video, then asking Buffer
            }
          </span>
          @if (cancellable(post)) {
            <button class="sm quiet danger" (click)="cancel(post)" [disabled]="busy()"
                    title="Delete it from Buffer before it goes out">Undo</button>
          } @else if (post.status === 'sent') {
            <span class="small muted" title="Buffer cannot take back a post that is out">Delete it on {{ service(post.service) }} to undo</span>
          }
        </div>
      }

      @if (open()) {
        @if (ready(); as r) {
          @if (!r.ready) {
            <div class="notice small"><span>Scheduling is not set up yet. Add to the <code>.env</code> file, then restart the app:
              <code>{{ r.missing.join(', ') }}</code>. The README explains where each one comes from.</span></div>
            <div><button class="sm quiet" (click)="open.set(false)">Close</button></div>
          } @else if (refused()) {
            <div class="notice small"><span>{{ refused() }}</span></div>
            <div><button class="sm quiet" (click)="open.set(false)">Close</button></div>
          } @else if (channels(); as list) {
            @if (!list.length) {
              <div class="notice small"><span>No Instagram, TikTok or YouTube channel is connected in this Buffer account. Connect one in
                Buffer, then reload.</span></div>
            } @else {
              <div class="channels">
                @for (c of list; track c.id) {
                  <label class="check small">
                    <input type="checkbox" [checked]="chosen().has(c.id)" (change)="toggle(c.id)"
                      [disabled]="c.disconnected || c.locked || !channelAllowed(c)" />
                    <span>{{ service(c.service) }} <span class="muted">{{ c.name }}</span>
                      @if (c.disconnected || c.locked) { <small>Disconnected or locked in Buffer</small> }
                      @else if (!channelAllowed(c)) { <small>Horizontal videos can only go to YouTube</small> }
                      @else if (c.queue_paused) { <small>Its queue is paused in Buffer: nothing goes out until you resume it</small> }
                      @else if (mode() === 'queue' && !c.weekly_slots) { <small>No posting times are set in Buffer: choose a date and time instead</small> }
                      @else if (mode() === 'queue' && nextTime(c); as next) { <small>Next free time in your Buffer schedule: {{ next }}, or later if it is taken</small> }
                    </span>
                  </label>
                }
              </div>
            }
            @if (missingServices().length) {
              <p class="small muted">Not connected in Buffer: {{ missingServices().join(' and ') }}.
                <button class="link" (click)="loadChannels(true)">Reload channels</button></p>
            }
            <label class="field"><span>When</span>
              <select [ngModel]="mode()" (ngModelChange)="mode.set($event)">
                <option value="queue">Next free slot in the Buffer queue</option>
                <option value="at">At a date and time</option>
                <option value="draft">Save as a draft in Buffer (nothing is posted)</option>
              </select>
            </label>
            @if (mode() === 'at') {
              <app-when-picker [(value)]="when" [channels]="chosenChannels()" [marks]="marks()" />
            }
            @if (hasSocial()) {
              <label class="field"><span>Instagram / TikTok caption</span>
                <textarea [ngModel]="text()" (ngModelChange)="text.set($event)" rows="4"></textarea>
                <small [class.over]="text().length > socialLimit()">{{ text().length }} / {{ socialLimit() }}</small>
              </label>
            }
            @if (hasYoutube()) {
              <label class="field"><span>YouTube title</span>
                <input type="text" [ngModel]="title()" (ngModelChange)="title.set($event)" maxlength="100" />
                <small [class.over]="youtubeTitleInvalid()">{{ title().length }} / 100 ·
                  {{ kind() === 'storyboard' ? 'Education' : 'People & Blogs' }}</small>
              </label>
              <label class="field"><span>YouTube description</span>
                <textarea [ngModel]="youtubeText()" (ngModelChange)="youtubeText.set($event)" rows="6"></textarea>
                <small [class.over]="youtubeTextInvalid()">{{ youtubeText().length }} / 5000</small>
              </label>
              @if (youtubeTitleInvalid() || youtubeTextInvalid()) {
                <div class="notice bad small"><span>A YouTube title is required, and neither its title nor description may contain
                  <code>&lt;</code> or <code>&gt;</code>.</span></div>
              }
            }
            <label class="check small"><input type="checkbox" [checked]="ai()" (change)="ai.set(!ai())" />
              <span>Label it as AI-generated<small>The selected networks receive their AI disclosure field.</small></span>
            </label>
            <p class="small muted">A copy of the video goes online under an address nobody can guess, so Buffer can fetch
              it. It is removed once the post is out or cancelled.</p>
            <div class="row">
              <button class="primary sm" (click)="submit()" [disabled]="busy() || invalid()">
                {{ busy() ? 'Sending…' : mode() === 'draft' ? 'Save the draft' : 'Schedule' }}</button>
              <button class="sm quiet" (click)="open.set(false)">Close</button>
            </div>
          } @else if (problem()) {
            <div class="notice bad small"><span>{{ problem() }}</span></div>
            <div class="row"><button class="sm" (click)="loadChannels(true)">Try again</button>
              <button class="sm quiet" (click)="open.set(false)">Close</button></div>
          } @else {
            <p class="small muted">Asking Buffer for your channels…</p>
          }
        } @else {
          <p class="small muted">Checking the setup…</p>
        }
      }
    </div>
  `,
  styles: `
    .box {
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: 12px 14px;
      display: grid;
      gap: 10px;
    }
    .post {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 10px;
      align-items: center;
    }
    .tight { gap: 6px; }
    .where { font-weight: 650; }
    .where .muted { font-weight: 400; }
    .grow { flex: 1; min-width: 120px; overflow-wrap: anywhere; }
    .channels { display: grid; gap: 6px; }
    .over { color: var(--bad); }
    .link {
      border: 0;
      background: none;
      box-shadow: none;
      padding: 0;
      color: inherit;
      text-decoration: underline;
      cursor: pointer;
      font: inherit;
    }
    code { overflow-wrap: anywhere; }
  `,
})
export class PostScheduler {
  private readonly api = inject(Api);
  private readonly store = inject(PostsStore);
  private readonly jobs = inject(JobsStore);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  readonly kind = input.required<'clip' | 'storyboard'>();
  readonly refId = input.required<number>();

  protected readonly open = signal(false);
  protected readonly busy = signal(false);
  protected readonly channels = signal<BufferChannel[] | null>(null);
  protected readonly chosen = signal<Set<string>>(new Set());
  protected readonly mode = signal<PostMode>('queue');
  protected readonly when = signal('');
  protected readonly text = signal('');
  protected readonly youtubeText = signal('');
  protected readonly title = signal('');
  protected readonly ai = signal(false);
  protected readonly short = signal(true);
  protected readonly problem = signal('');
  protected readonly refused = signal('');

  protected readonly ready = signal<PostingStatus | null>(null);

  /** The latest post per channel of this video, without the ones taken back. */
  protected readonly mine = computed(() => {
    const seen = new Set<string>();
    return this.store.posts()
      .filter((p) => (this.kind() === 'clip' ? p.clip_id : p.storyboard_id) === this.refId())
      .filter((p) => p.status !== 'cancelled' && p.status !== 'removed')
      .filter((p) => !seen.has(p.channel_id) && !!seen.add(p.channel_id));
  });

  protected readonly chosenChannels = computed(() => (this.channels() ?? []).filter((c) => this.chosen().has(c.id)));
  protected readonly hasYoutube = computed(() => this.chosenChannels().some((c) => c.service === 'youtube'));
  protected readonly hasSocial = computed(() => this.chosenChannels().some((c) => c.service !== 'youtube'));
  protected readonly socialLimit = computed(() => {
    const limits = this.chosenChannels().filter((c) => c.service !== 'youtube').map((c) => LIMITS[c.service] ?? 2196);
    return limits.length ? Math.min(...limits) : 2196;
  });
  protected readonly youtubeTitleInvalid = computed(() => this.hasYoutube()
    && (!this.title().trim() || this.title().length > 100 || /[<>]/.test(this.title())));
  protected readonly youtubeTextInvalid = computed(() => this.hasYoutube()
    && (this.youtubeText().length > LIMITS['youtube'] || /[<>]/.test(this.youtubeText())));
  protected readonly invalid = computed(() => !this.chosen().size
    || this.hasSocial() && this.text().length > this.socialLimit()
    || this.youtubeTitleInvalid() || this.youtubeTextInvalid());

  /** Posts already waiting on each day, to see the calendar fill up. */
  protected readonly marks = computed(() => {
    const counts: Record<string, number> = {};
    for (const post of this.store.posts()) {
      if (WAITING.has(post.status) && post.due_at) counts[dayKeyOf(post.due_at)] = (counts[dayKeyOf(post.due_at)] ?? 0) + 1;
    }
    return counts;
  });

  protected readonly missingServices = computed(() => {
    const have = new Set((this.channels() ?? []).map((c) => c.service));
    return Object.keys(SERVICE).filter((s) => !have.has(s)).map((s) => SERVICE[s]);
  });

  constructor() {
    this.store.load(10_000);
  }

  protected service(name: string): string {
    return SERVICE[name] ?? name;
  }

  protected label(post: Post): [string, string] {
    return STATUS[post.status] ?? [post.status, ''];
  }

  /** Posts that can still be taken back before they go out. */
  protected readonly undoable = computed(() => this.mine().filter((p) => this.cancellable(p)));

  protected cancellable(post: Post): boolean {
    return WAITING.has(post.status) || post.status === 'failed' && post.hosted;
  }

  protected time(iso: string | null): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  protected nextTime(channel: BufferChannel): string {
    const at = nextSlot(channel);
    return at ? this.time(at.toISOString()) : '';
  }

  private local(date: Date): string {
    const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
    return shifted.toISOString().slice(0, 16);
  }

  protected start(): void {
    this.open.set(true);
    this.refused.set('');
    if (!this.when()) {
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(18, 0, 0, 0);
      this.when.set(this.local(tomorrow));
    }
    this.jobs.refresh();          // the setup may have changed since the page loaded
    this.ready.set(null);
    this.api.postingStatus(this.kind()).subscribe({
      next: (state) => {
        this.ready.set(state);
        if (state.ready && this.channels() === null) this.loadChannels(false);
      },
      error: (err) => this.problem.set(errorMessage(err)),
    });
    this.api.postDraft(this.kind(), this.refId()).subscribe({
      next: (draft) => {
        if (!this.text()) this.text.set(draft.text);
        if (!this.youtubeText()) this.youtubeText.set(draft.youtube_text);
        if (!this.title()) this.title.set(draft.title);
        this.ai.set(draft.ai_label);
        this.short.set(draft.short);
        this.preferred = draft.channels ?? [];
        this.chooseAvailable();
        this.refused.set(draft.allowed ? '' : draft.reason);
      },
      error: (err) => this.refused.set(errorMessage(err)),
    });
  }

  protected loadChannels(fresh: boolean): void {
    this.problem.set('');
    this.channels.set(null);
    this.api.postChannels(this.kind(), fresh).subscribe({
      next: (found) => {
        this.channels.set(found.channels);
        this.chooseAvailable();
      },
      error: (err) => this.problem.set(errorMessage(err)),
    });
  }

  protected toggle(id: string): void {
    const channel = this.channels()?.find((c) => c.id === id);
    if (!channel || !this.channelAllowed(channel)) return;
    this.chosen.update((set) => {
      const next = new Set(set);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  protected channelAllowed(_channel: BufferChannel): boolean {
    return this.short();
  }

  /** The theme's usual channels; when it has some, only those start ticked. */
  private preferred: string[] = [];

  private chooseAvailable(): void {
    if (!this.channels()) return;
    const waiting = new Set(this.mine().filter((p) => WAITING.has(p.status)).map((p) => p.channel_id));
    const open = this.channels()!.filter((c) => this.channelAllowed(c) && !c.disconnected && !c.locked && !waiting.has(c.id));
    const mine = open.filter((c) => this.preferred.includes(c.id));
    this.chosen.set(new Set((mine.length ? mine : open).map((c) => c.id)));
  }

  protected async undoAll(): Promise<void> {
    const names = [...new Set(this.undoable().map((p) => this.service(p.service)))].join(', ');
    const sent = this.mine().filter((p) => p.status === 'sent').length;
    if (!(await this.confirm.ask({
      title: 'Undo the publish?',
      message: `The waiting posts (${names}) are deleted from Buffer before they go out. `
        + (sent ? 'Posts already out stay: delete those on the network. ' : '')
        + 'The video is kept, still approved.',
      confirm: 'Undo all', cancel: 'Keep them', danger: true }))) return;
    this.busy.set(true);
    this.api.undoPublish(this.kind(), this.refId()).subscribe({
      next: (result) => {
        this.busy.set(false);
        result.undone.forEach((p) => this.store.replace(p));
        if (result.failed.length) this.toasts.error(`Not undone: ${result.failed.join('; ')}`);
        else this.toasts.ok(`Undone: ${result.undone.length} post${result.undone.length === 1 ? '' : 's'}`);
      },
      error: (err) => {
        this.busy.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected submit(): void {
    const at = this.mode() === 'at' ? new Date(this.when()) : null;
    if (at && Number.isNaN(at.getTime())) {
      this.toasts.error('Choose a date and time');
      return;
    }
    if (at && at.getTime() < Date.now() + 10 * 60_000) {
      this.toasts.error('Choose a time at least 10 minutes from now');
      return;
    }
    this.busy.set(true);
    this.api.schedulePost({
      kind: this.kind(), ref_id: this.refId(), channel_ids: [...this.chosen()], text: this.text(),
      youtube_text: this.youtubeText(), title: this.title(),
      mode: this.mode(), due_at: at ? at.toISOString() : null, ai_label: this.ai(),
    }).subscribe({
      next: () => {
        this.busy.set(false);
        this.open.set(false);
        this.toasts.ok(this.mode() === 'draft' ? 'Sending the draft to Buffer' : 'Sending to Buffer');
        this.store.load();
        this.jobs.refresh();
      },
      error: (err) => {
        this.busy.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected async cancel(post: Post): Promise<void> {
    if (!(await this.confirm.ask({
      title: `Undo the ${this.service(post.service)} post?`,
      message: 'It is deleted from Buffer before it goes out, and the online copy of the video is taken down. '
        + 'The video itself is kept, still approved: you can schedule it again.',
      confirm: 'Undo the post', cancel: 'Keep it', danger: true }))) return;
    this.busy.set(true);
    this.api.cancelPost(post.id).subscribe({
      next: (updated) => {
        this.busy.set(false);
        this.store.replace(updated);
        this.toasts.ok(`${this.service(post.service)} post undone`);
      },
      error: (err) => {
        this.busy.set(false);
        this.toasts.error(err);
      },
    });
  }
}
