import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../core/api';
import { Confirm } from '../core/confirm';
import { YoutubeText } from '../core/models';
import { Toasts } from '../core/toast';

/**
 * The YouTube title, description and tags of an approved video: drafted by the AI from the
 * script (chapters, sources and credits added by the app), then edited and copied by you.
 * Nothing is uploaded.
 */
@Component({
  selector: 'app-youtube-text',
  imports: [FormsModule],
  template: `
    <section class="card yt">
      <div class="row spread">
        <div>
          <h3>YouTube title and description</h3>
          <p class="small muted">Written from the script only; chapters, sources and credits are added as they are.
            Finish and save it here; then publish a long video manually or schedule a Short from Animations → Approved.</p>
        </div>
        @if (text()) {
          <button class="sm quiet" (click)="write()" [disabled]="writing()">{{ writing() ? 'Writing…' : 'Write it again' }}</button>
        }
      </div>

      @if (!text()) {
        <div class="row">
          <button class="primary" (click)="write()" [disabled]="writing()">
            {{ writing() ? 'Writing… about 10 s' : 'Write the title and description' }}</button>
          <span class="small muted">About one cent.</span>
        </div>
      } @else {
        @if (options().length > 1) {
          <div class="options">
            <span class="small muted">Title ideas:</span>
            @for (t of options(); track t) {
              <button class="chip-btn" [class.on]="t === title()" (click)="title.set(t)">{{ t }}</button>
            }
          </div>
        }
        <label class="field"><span>Title <em class="count" [class.over]="title().length > 100">{{ title().length }}/100</em></span>
          <input type="text" [ngModel]="title()" (ngModelChange)="title.set($event)" /></label>
        <label class="field"><span>Description <em class="count" [class.over]="description().length > 5000">{{ description().length }}/5000</em></span>
          <textarea rows="14" [ngModel]="description()" (ngModelChange)="description.set($event)"></textarea>
          <small>YouTube shows the first line before "more": make it say what the viewer will learn.</small></label>
        <label class="field"><span>Tags <em class="count" [class.over]="tagLength() > 500">{{ tagLength() }}/500</em></span>
          <input type="text" [ngModel]="tags()" (ngModelChange)="tags.set($event)" placeholder="comma, separated" />
          <small>Separate tags with commas. Tags matter little on YouTube; the title and description matter most.</small></label>
        @for (p of text()!.problems; track p) { <div class="notice small bad"><span>{{ p }}</span></div> }
        <div class="row">
          @if (dirty()) {
            <button class="primary sm" (click)="save()" [disabled]="saving()">{{ saving() ? 'Saving…' : 'Save' }}</button>
            <button class="quiet sm" (click)="reset()">Undo changes</button>
          }
          <span class="grow"></span>
          <button class="sm" (click)="copy(title(), 'Title')">Copy title</button>
          <button class="sm" (click)="copy(description(), 'Description')">Copy description</button>
          <button class="sm" (click)="copy(tags(), 'Tags')" [disabled]="!tags().trim()">Copy tags</button>
        </div>
        <p class="small muted">{{ text()!.edited ? 'Edited by you.' : 'Drafted by the AI, not edited yet.' }}
          Read it before you post: you are the one publishing it.</p>
      }
    </section>
  `,
  styles: `
    .yt { display: grid; gap: 12px; }
    h3 { margin: 0 0 4px; }
    .row.spread { align-items: flex-start; }
    .field > span { display: flex; justify-content: space-between; gap: 8px; }
    .count { font-style: normal; font-weight: 500; color: var(--muted); font-variant-numeric: tabular-nums; }
    .count.over { color: var(--bad); font-weight: 700; }
    textarea { font: inherit; font-size: 14px; line-height: 1.5; resize: vertical; }
    .options { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .chip-btn {
      font-size: 13px; padding: 4px 10px; border-radius: 99px; text-align: left; white-space: normal;
      background: var(--sunken); border: 1px solid var(--line); color: var(--text);
    }
    .chip-btn.on { background: var(--accent); color: var(--accent-ink); border-color: transparent; }
  `,
})
export class YoutubeTextView {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  readonly storyboardId = input.required<number>();
  readonly text = input<YoutubeText | null>(null);
  /** The saved text after writing or saving it, for the page to keep. */
  readonly changed = output<YoutubeText>();

  protected readonly title = signal('');
  protected readonly description = signal('');
  protected readonly tags = signal('');
  protected readonly writing = signal(false);
  protected readonly saving = signal(false);

  protected readonly options = computed(() => this.text()?.titles ?? []);
  protected readonly tagLength = computed(() => this.tagList().join(',').length);
  protected readonly dirty = computed(() => {
    const t = this.text();
    return !!t && (this.title() !== t.title || this.description() !== t.description
      || this.tagList().join(',') !== t.tags.join(','));
  });

  constructor() {
    effect(() => {
      this.text();
      this.reset();
    });
  }

  private tagList(): string[] {
    return this.tags().split(',').map((t) => t.trim()).filter(Boolean);
  }

  protected reset(): void {
    const t = this.text();
    this.title.set(t?.title ?? '');
    this.description.set(t?.description ?? '');
    this.tags.set((t?.tags ?? []).join(', '));
  }

  protected async write(): Promise<void> {
    if (this.text() && !(await this.confirm.ask({
      title: 'Write it again?', message: 'A new draft replaces the title, description and tags, with your edits.',
      confirm: 'Write again', danger: true }))) return;
    this.writing.set(true);
    this.api.writeYoutubeText(this.storyboardId()).subscribe({
      next: (t) => {
        this.writing.set(false);
        this.changed.emit(t);
      },
      error: (err) => {
        this.writing.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected save(): void {
    this.saving.set(true);
    this.api.saveYoutubeText(this.storyboardId(), { title: this.title(), description: this.description(), tags: this.tagList() })
      .subscribe({
        next: (t) => {
          this.saving.set(false);
          this.changed.emit(t);
          this.toasts.ok('Saved');
        },
        error: (err) => {
          this.saving.set(false);
          this.toasts.error(err);
        },
      });
  }

  protected copy(value: string, what: string): void {
    navigator.clipboard.writeText(value).then(
      () => this.toasts.ok(`${what} copied`),
      () => this.toasts.error('Could not copy: select the text and copy it yourself'),
    );
  }
}
