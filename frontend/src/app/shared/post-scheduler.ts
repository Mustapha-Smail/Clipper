import { Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, errorMessage } from '../core/api';
import { Confirm } from '../core/confirm';
import { JobsStore } from '../core/jobs-store';
import { BufferChannel, Post, PostMode, PostStatus } from '../core/models';
import { PostsStore } from '../core/posts-store';
import { Toasts } from '../core/toast';

const SERVICE: Record<string, string> = { instagram: 'Instagram', tiktok: 'TikTok' };
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
const WAITING = new Set<PostStatus>(['queued', 'draft', 'scheduled', 'sending']);
const LIMIT = 2196;   // the shorter of Instagram's and TikTok's caption limits

/**
 * Schedule an approved Short on Instagram and TikTok through Buffer, and show
 * where its posts stand. Only shown for approved videos; the server checks again.
 */
@Component({
  selector: 'app-post-scheduler',
  imports: [FormsModule],
  template: `
    <div class="box">
      <div class="row spread">
        <strong class="small">Post it</strong>
        @if (!open()) {
          <button class="sm" (click)="start()">{{ mine().length ? 'Schedule again' : 'Schedule' }}</button>
        }
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
            <button class="sm quiet danger" (click)="cancel(post)" [disabled]="busy()">Cancel</button>
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
              <div class="notice small"><span>No Instagram or TikTok channel is connected in your Buffer account. Connect one in
                Buffer, then reload.</span></div>
            } @else {
              <div class="channels">
                @for (c of list; track c.id) {
                  <label class="check small">
                    <input type="checkbox" [checked]="chosen().has(c.id)" (change)="toggle(c.id)" [disabled]="c.disconnected || c.locked" />
                    <span>{{ service(c.service) }} <span class="muted">{{ c.name }}</span>
                      @if (c.disconnected || c.locked) { <small>Disconnected or locked in Buffer</small> }
                      @else if (c.queue_paused) { <small>Its queue is paused in Buffer: nothing goes out until you resume it</small> }
                      @else if (mode() === 'queue' && !c.weekly_slots) { <small>No posting times are set in Buffer: choose a date and time instead</small> }
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
              <label class="field"><span>Date and time (your time)</span>
                <input type="datetime-local" [ngModel]="when()" (ngModelChange)="when.set($event)" [min]="soonest()" /></label>
            }
            <label class="field"><span>Caption</span>
              <textarea [ngModel]="text()" (ngModelChange)="text.set($event)" rows="4"></textarea>
              <small [class.over]="text().length > limit">{{ text().length }} / {{ limit }}</small>
            </label>
            <label class="check small"><input type="checkbox" [checked]="ai()" (change)="ai.set(!ai())" />
              <span>Label it as AI-generated<small>Both networks ask for it when the picture or the voice was made by AI.</small></span>
            </label>
            <p class="small muted">A copy of the video goes online under an address nobody can guess, so Buffer can fetch
              it. It is removed once the post is out or cancelled.</p>
            <div class="row">
              <button class="primary sm" (click)="submit()" [disabled]="busy() || !chosen().size || text().length > limit">
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
    .where { font-weight: 650; }
    .where .muted { font-weight: 400; }
    .grow { flex: 1; min-width: 120px; overflow-wrap: anywhere; }
    .channels { display: grid; gap: 6px; }
    .over { color: var(--bad); }
    .link {
      border: 0;
      background: none;
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

  protected readonly limit = LIMIT;
  protected readonly open = signal(false);
  protected readonly busy = signal(false);
  protected readonly channels = signal<BufferChannel[] | null>(null);
  protected readonly chosen = signal<Set<string>>(new Set());
  protected readonly mode = signal<PostMode>('queue');
  protected readonly when = signal('');
  protected readonly text = signal('');
  protected readonly ai = signal(false);
  protected readonly problem = signal('');
  protected readonly refused = signal('');

  protected readonly ready = computed(() => this.jobs.status()?.posting ?? null);

  /** The latest post per channel of this video, without the ones taken back. */
  protected readonly mine = computed(() => {
    const seen = new Set<string>();
    return this.store.posts()
      .filter((p) => (this.kind() === 'clip' ? p.clip_id : p.storyboard_id) === this.refId())
      .filter((p) => p.status !== 'cancelled' && p.status !== 'removed')
      .filter((p) => !seen.has(p.channel_id) && !!seen.add(p.channel_id));
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

  protected cancellable(post: Post): boolean {
    return WAITING.has(post.status) || post.status === 'failed' && post.hosted;
  }

  protected time(iso: string | null): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  /** Ten minutes from now, in the form a datetime-local input wants. */
  protected soonest(): string {
    return this.local(new Date(Date.now() + 10 * 60_000));
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
    this.api.postDraft(this.kind(), this.refId()).subscribe({
      next: (draft) => {
        if (!this.text()) this.text.set(draft.text);
        this.ai.set(draft.ai_label);
        this.refused.set(draft.allowed ? '' : draft.reason);
      },
      error: (err) => this.refused.set(errorMessage(err)),
    });
    if (this.channels() === null) this.loadChannels(false);
  }

  protected loadChannels(fresh: boolean): void {
    this.problem.set('');
    this.channels.set(null);
    this.api.postChannels(fresh).subscribe({
      next: (found) => {
        this.channels.set(found.channels);
        const waiting = new Set(this.mine().filter((p) => WAITING.has(p.status)).map((p) => p.channel_id));
        this.chosen.set(new Set(found.channels
          .filter((c) => !c.disconnected && !c.locked && !waiting.has(c.id)).map((c) => c.id)));
      },
      error: (err) => this.problem.set(errorMessage(err)),
    });
  }

  protected toggle(id: string): void {
    this.chosen.update((set) => {
      const next = new Set(set);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  protected submit(): void {
    const at = this.mode() === 'at' ? new Date(this.when()) : null;
    if (at && Number.isNaN(at.getTime())) {
      this.toasts.error('Choose a date and time');
      return;
    }
    this.busy.set(true);
    this.api.schedulePost({
      kind: this.kind(), ref_id: this.refId(), channel_ids: [...this.chosen()], text: this.text(),
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
      title: `Cancel the ${this.service(post.service)} post?`,
      message: 'It is deleted in Buffer and the hosted copy of the video is taken down. The clip itself is kept.',
      confirm: 'Cancel the post', cancel: 'Keep it', danger: true }))) return;
    this.busy.set(true);
    this.api.cancelPost(post.id).subscribe({
      next: (updated) => {
        this.busy.set(false);
        this.store.replace(updated);
        this.toasts.ok('Post cancelled');
      },
      error: (err) => {
        this.busy.set(false);
        this.toasts.error(err);
      },
    });
  }
}
