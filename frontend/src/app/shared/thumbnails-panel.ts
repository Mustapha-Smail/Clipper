import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../core/api';
import { Confirm } from '../core/confirm';
import { Storyboard, ThumbnailAccent, ThumbnailEdit, ThumbnailOption } from '../core/models';
import { Toasts } from '../core/toast';

interface Draft {
  headline: string;
  accent: ThumbnailAccent;
  side: 'left' | 'right';
  title: number;
  mode: 'drawn' | 'frame';
  at: number;
  beats: string;
}

type Key = 'A' | 'B' | 'C';

const ANGLE_LABELS = { reaction: 'Reaction', stakes: 'Stakes', mystery: 'Mystery' };
const ANGLE_HINTS = {
  reaction: 'A face with a strong expression next to the key thing',
  stakes: 'The contrast or the number the video turns on',
  mystery: 'The key thing pointed at or circled, few words or none',
};

/**
 * Three thumbnails of a long video for YouTube's A/B test: planned by the AI from the video's own
 * people and props, drawn by the app, checked with arithmetic you can read, and edited here.
 * Compact by default: three tiles, and the details of the one you pick below them.
 * Nothing is uploaded: you add them in YouTube Studio.
 */
@Component({
  selector: 'app-thumbnails-panel',
  imports: [FormsModule],
  template: `
    <section class="card thumbs">
      <header class="head">
        <div class="intro">
          <h3>Thumbnails</h3>
          <p class="small muted">Three options for YouTube's A/B test.</p>
        </div>
        <button class="sm" [class.primary]="!options().length" [class.quiet]="options().length"
                (click)="make()" [disabled]="busy() || starting()">
          {{ starting() ? 'Starting…' : options().length ? 'Remake all' : 'Make 3 thumbnails' }}</button>
      </header>

      @if (!options().length) {
        <p class="small muted">A few cents of AI. {{ board().style === 'ink'
          ? 'Each picture is drawn as one shot in the ink style, from this video\\'s own people and props.'
          : 'Each picture is a frame of the video.' }}</p>
      } @else {
        @if (board().thumbnails!.by === 'rules') {
          <div class="notice small"><span>AI unavailable: three frames picked by rules, without headlines.</span></div>
        }

        <div class="tiles" role="tablist" aria-label="Thumbnail options">
          @for (o of options(); track o.key) {
            <button class="tile" role="tab" [class.on]="o.key === selected()" [attr.aria-selected]="o.key === selected()"
                    (click)="select(o.key)" [title]="hints[o.angle]">
              <img [src]="url(o)" [alt]="'Thumbnail ' + o.key" loading="lazy" decoding="async" />
              <span class="label">{{ o.key }} · {{ labels[o.angle] }}</span>
              <span class="score" [class.good]="o.check.score >= 80" [class.bad]="o.check.score < 60">
                {{ o.check.score }}@if (o.check.problems.length) { <i class="dot" aria-label="Has problems"></i> }</span>
            </button>
          }
        </div>

        @if (current(); as o) {
          <div class="detail">
            <div class="facts">
              <div class="phone">
                <img [src]="url(o)" alt="" width="168" height="94" decoding="async" />
                <span class="small muted">Phone size</span>
              </div>
              <div class="about">
                <p class="small"><span class="muted">Title:</span> {{ titleOf(o) }}</p>
                @if (o.why) { <p class="small muted">{{ o.why }}</p> }
                @for (p of o.check.problems; track p) { <p class="small bad">{{ p }}</p> }
                @if (o.look && !(o.look.clear && o.look.honest)) { <p class="small warn">AI: {{ o.look.note }}</p> }
                @if (costs(o).length) {
                  <ul class="costs small">
                    @for (line of costs(o); track $index) { <li><span>{{ line.text }}</span><em>{{ line.points }}</em></li> }
                  </ul>
                } @else {
                  <p class="small good">Every check passes.</p>
                }
              </div>
            </div>

            <div class="actions">
              <button class="sm" [class.on]="editing()" (click)="editing.set(!editing())">{{ editing() ? 'Close' : 'Edit' }}</button>
              <a class="btn sm quiet" [href]="url(o)" [attr.download]="'thumbnail-' + o.key + '.jpg'">Download</a>
              <details class="more">
                <summary class="small">All check lines</summary>
                <ul class="costs small">
                  @for (line of o.check.lines; track $index) { <li><span>{{ line.text }}</span><em>{{ line.points || '' }}</em></li> }
                  @for (d of o.dropped; track d) { <li class="muted"><span>Left out: {{ d }}</span></li> }
                </ul>
              </details>
            </div>

            @if (editing() && draft(o.key); as d) {
              <div class="edit">
                <label class="field wide"><span>Headline <em class="count" [class.over]="words(d.headline) > 4">{{ words(d.headline) }}/4</em></span>
                  <input type="text" maxlength="80" [ngModel]="d.headline" (ngModelChange)="set(o.key, 'headline', $event)"
                         placeholder="Empty: picture only" />
                  <small>*Stars* colour a word.</small></label>
                <label class="field"><span>Colour</span>
                  <select [ngModel]="d.accent" (ngModelChange)="set(o.key, 'accent', $event)">
                    @for (c of accents; track c.value) { <option [value]="c.value">{{ c.label }}</option> }
                  </select></label>
                <label class="field"><span>Text side</span>
                  <select [ngModel]="d.side" (ngModelChange)="set(o.key, 'side', $event)">
                    <option value="left">Left</option><option value="right">Right</option>
                  </select></label>
                <label class="field wide"><span>Title</span>
                  <select [ngModel]="d.title" (ngModelChange)="set(o.key, 'title', +$event)">
                    @for (t of titles(); track $index) { <option [value]="$index + 1">{{ t }}</option> }
                  </select></label>
                <label class="field wide"><span>Picture</span>
                  <select [ngModel]="d.mode" (ngModelChange)="set(o.key, 'mode', $event)">
                    @if (board().style === 'ink') { <option value="drawn">Drawn shot</option> }
                    <option value="frame">Frame of the video</option>
                  </select></label>
                @if (d.mode === 'drawn') {
                  <label class="field wide"><span>Beat lines</span>
                    <textarea rows="4" spellcheck="false" [ngModel]="d.beats" (ngModelChange)="set(o.key, 'beats', $event)"></textarea>
                    <small>e.g. actor narrator surprised · draw PROP large · circle PROP bad</small></label>
                } @else {
                  <label class="field wide"><span>Moment <em class="count">{{ d.at.toFixed(1) }} s</em></span>
                    <input type="range" min="0" [max]="board().duration" step="0.1" [ngModel]="d.at"
                           (ngModelChange)="set(o.key, 'at', +$event)" />
                    <img class="pick" [src]="stillUrl(d.at)" alt="" decoding="async" /></label>
                }
                <div class="row wide buttons">
                  <button class="primary sm" (click)="save(o)" [disabled]="!!saving() || busy() || !changedDraft(o)">
                    {{ saving() === o.key ? 'Drawing…' : 'Redraw' }}</button>
                  @if (changedDraft(o)) { <button class="quiet sm" (click)="reset(o)">Undo</button> }
                </div>
                <div class="row wide again">
                  <input type="text" maxlength="500" placeholder="Or tell the AI what to change"
                         [ngModel]="notes()[o.key] ?? ''" (ngModelChange)="setNote(o.key, $event)" />
                  <button class="sm" (click)="again(o)" [disabled]="busy() || starting()">Ask AI</button>
                </div>
              </div>
            }
          </div>
        }

        <details class="how small">
          <summary>How to run the test</summary>
          <p class="muted">In YouTube Studio on a computer: the video's details, Thumbnail, then A/B testing. Add all
            three; YouTube keeps the one watched most. It needs a verified channel and is reportedly not offered
            on videos made for kids. Check each one first: YouTube removes misleading thumbnails.</p>
        </details>
      }
    </section>
  `,
  styles: `
    .thumbs { display: grid; gap: 14px; min-width: 0; }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .intro { min-width: 0; }
    h3 { margin: 0; }
    p { margin: 0; }

    /* three tiles side by side; a swipeable row on a phone */
    .tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .tile {
      position: relative; display: block; padding: 0; border: 2px solid transparent; border-radius: var(--radius-sm); box-shadow: none;
      background: var(--sunken); overflow: hidden; cursor: pointer; text-align: left;
      transition: border-color 0.15s ease, transform 0.15s ease;
    }
    .tile:hover { transform: translateY(-1px); }
    .tile.on { border-color: var(--accent); }
    .tile img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; }
    .label {
      position: absolute; left: 6px; bottom: 6px; padding: 2px 8px; border-radius: 99px; font-size: 12px;
      font-weight: 600; background: rgb(0 0 0 / 0.62); color: #fff;
    }
    .score {
      position: absolute; right: 6px; top: 6px; padding: 1px 8px; border-radius: 99px; font-size: 12px; font-weight: 700;
      font-variant-numeric: tabular-nums; background: var(--warn-soft); color: var(--warn);
      display: inline-flex; align-items: center; gap: 4px;
    }
    .score.good { background: var(--good-soft); color: var(--good); }
    .score.bad { background: var(--bad-soft); color: var(--bad); }
    .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--bad); }

    .detail { display: grid; gap: 12px; padding-top: 2px; }
    .facts { display: flex; gap: 14px; align-items: flex-start; min-width: 0; }
    .phone { display: grid; gap: 4px; justify-items: center; flex: none; }
    .phone img { width: 168px; height: 94px; border-radius: 4px; object-fit: cover; }
    .about { display: grid; gap: 6px; min-width: 0; }
    .bad { color: var(--bad); font-weight: 500; }
    .warn { color: var(--warn); }
    .good { color: var(--good); }
    .costs { list-style: none; padding: 0; margin: 0; display: grid; gap: 3px; }
    .costs li { display: flex; justify-content: space-between; gap: 10px; }
    .costs em { font-style: normal; font-variant-numeric: tabular-nums; color: var(--bad); flex: none; }

    .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .actions .more { flex-basis: 100%; }
    .actions .more .costs { margin-top: 6px; }
    .actions .more .costs em { color: var(--muted); }
    summary { cursor: pointer; color: var(--muted); }

    .edit {
      display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 12px;
      border: 1px solid var(--line); border-radius: var(--radius-sm);
    }
    .edit .wide { grid-column: 1 / -1; }
    .field { min-width: 0; }
    .field > span { display: flex; justify-content: space-between; gap: 8px; }
    .field select, .field input, .field textarea { width: 100%; min-width: 0; }
    .count { font-style: normal; color: var(--muted); font-variant-numeric: tabular-nums; }
    .count.over { color: var(--bad); font-weight: 700; }
    textarea { font-family: ui-monospace, monospace; font-size: 13px; resize: vertical; }
    .pick { width: 100%; max-width: 360px; aspect-ratio: 16 / 9; border-radius: 4px; margin-top: 6px; display: block; }
    .buttons, .again { gap: 8px; }
    .again input { flex: 1; min-width: 0; }
    .how p { margin-top: 6px; }

    @media (max-width: 640px) {
      .tiles {
        display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scroll-padding: 0 2px;
        -webkit-overflow-scrolling: touch; padding-bottom: 4px; scrollbar-width: none;
      }
      .tiles::-webkit-scrollbar { display: none; }
      .tile { flex: 0 0 82%; scroll-snap-align: start; }
      .phone { display: none; }          /* on a phone the tile above is already about that size */
      .edit { padding: 10px; }           /* colour and side stay side by side: both are short */
    }
  `,
})
export class ThumbnailsPanel {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  readonly board = input.required<Storyboard>();
  /** A job is running on this storyboard. */
  readonly busy = input(false);
  /** The page's preview version, bumped after a save or a job, so stills reload. */
  readonly version = input<number>(0);
  /** The board after an edit, for the page to keep. */
  readonly changed = output<Storyboard>();
  /** A job was queued: the page refreshes its jobs. */
  readonly started = output<void>();

  protected readonly labels = ANGLE_LABELS;
  protected readonly hints = ANGLE_HINTS;
  protected readonly accents: { value: ThumbnailAccent; label: string }[] = [
    { value: 'gold', label: 'Yellow' }, { value: 'warm', label: 'Orange' },
    { value: 'bad', label: 'Red' }, { value: 'good', label: 'Blue' },
  ];
  protected readonly starting = signal(false);
  protected readonly saving = signal<string | null>(null);
  protected readonly selected = signal<Key>('A');
  protected readonly editing = signal(false);
  protected readonly drafts = signal<Record<string, Draft>>({});
  protected readonly notes = signal<Record<string, string>>({});

  protected readonly options = computed(() => this.board().thumbnails?.options ?? []);
  protected readonly current = computed(() =>
    this.options().find((o) => o.key === this.selected()) ?? this.options()[0] ?? null);
  protected readonly titles = computed(() => {
    const b = this.board();
    return b.youtube?.titles?.length ? b.youtube.titles : [b.title];
  });

  constructor() {
    effect(() => {
      const fresh: Record<string, Draft> = {};
      for (const o of this.options()) fresh[o.key] = this.toDraft(o);
      this.drafts.set(fresh);
    });
  }

  private toDraft(o: ThumbnailOption): Draft {
    return { headline: o.headline, accent: o.accent, side: o.side, title: o.title, mode: o.mode,
             at: o.at ?? 0, beats: o.beats };
  }

  protected select(key: Key): void {
    if (key !== this.selected()) this.editing.set(false);
    this.selected.set(key);
  }

  protected draft(key: string): Draft | undefined {
    return this.drafts()[key];
  }

  protected changedDraft(o: ThumbnailOption): boolean {
    const d = this.drafts()[o.key];
    return !!d && JSON.stringify(d) !== JSON.stringify(this.toDraft(o));
  }

  protected set<K extends keyof Draft>(key: string, field: K, value: Draft[K]): void {
    this.drafts.update((all) => ({ ...all, [key]: { ...all[key], [field]: value } }));
  }

  protected setNote(key: string, value: string): void {
    this.notes.update((all) => ({ ...all, [key]: value }));
  }

  /** Only the lines that cost points: the rest is behind "All check lines". */
  protected costs(o: ThumbnailOption) {
    return o.check.lines.filter((l) => l.points < 0);
  }

  protected words(text: string): number {
    return text.replace(/\*/g, '').split(/\s+/).filter(Boolean).length;
  }

  protected url(o: ThumbnailOption): string {
    return this.api.thumbnailUrl(this.board().id, o.key, o.version);
  }

  protected stillUrl(t: number): string {
    return this.api.stillUrl(this.board().id, t, this.version(), 360);
  }

  protected titleOf(o: ThumbnailOption): string {
    return o.title_text || this.titles()[o.title - 1] || this.board().title;
  }

  protected reset(o: ThumbnailOption): void {
    this.drafts.update((all) => ({ ...all, [o.key]: this.toDraft(o) }));
  }

  protected async make(): Promise<void> {
    if (this.options().length && !(await this.confirm.ask({
      title: 'Remake all three?', message: 'New plans replace the three thumbnails, with your edits.',
      confirm: 'Remake', danger: true }))) return;
    this.queue();
  }

  protected again(o: ThumbnailOption): void {
    this.queue(o.key, (this.notes()[o.key] ?? '').trim());
  }

  private queue(only?: Key, note = ''): void {
    this.starting.set(true);
    this.api.makeThumbnails(this.board().id, only, note).subscribe({
      next: () => {
        this.starting.set(false);
        this.editing.set(false);
        this.toasts.ok(only ? `Planning ${only} again…` : 'Making the thumbnails… about a minute');
        this.started.emit();
      },
      error: (err) => {
        this.starting.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected save(o: ThumbnailOption): void {
    const d = this.drafts()[o.key];
    const edit: ThumbnailEdit = { headline: d.headline, accent: d.accent, side: d.side, title: d.title, mode: d.mode };
    if (d.mode === 'frame') edit.at = d.at;
    else if (d.beats !== o.beats) edit.beats = d.beats;
    this.saving.set(o.key);
    this.api.editThumbnail(this.board().id, o.key, edit).subscribe({
      next: (board) => {
        this.saving.set(null);
        this.changed.emit(board);
        this.toasts.ok(`Thumbnail ${o.key} redrawn`);
      },
      error: (err) => {
        this.saving.set(null);
        this.toasts.error(err);
      },
    });
  }
}
