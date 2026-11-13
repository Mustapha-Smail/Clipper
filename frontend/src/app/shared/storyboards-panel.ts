import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { INK_LOOKS, InkLook, Script, StoryboardSummary } from '../core/models';
import { Toasts } from '../core/toast';

/** Animated videos made from a script: Shorts, one per section for now. */
@Component({
  selector: 'app-storyboards-panel',
  imports: [FormsModule, RouterLink, AgoPipe],
  template: `
    <section class="card videos">
      <div>
        <h3>Videos</h3>
        <p class="small muted">Animated from the take marked for the video. Claude plans the scenes and draws any prop the
          library lacks; you edit, render and review. The long video is horizontal (16:9, 1920×1080) and tells the
          whole script; a Short is vertical (9:16, 1080×1920) and tells one section.</p>
      </div>

      @if (!hasTake()) {
        <div class="notice small"><span>Mark a voice take with "Use for the video" first.</span></div>
      } @else {
        <h4>Make a video</h4>
        <label class="field look"><span>Look</span>
          <select [ngModel]="look()" (ngModelChange)="look.set($event)">
            @for (l of looks; track l.value) {
              <option [value]="l.value">{{ l.label }}</option>
            }
            <option value="flat">Flat colour: the first style, scene by scene</option>
          </select>
          <small>{{ lookHint() }}</small>
        </label>
        @if (script().video === 'short') {
          <div class="row">
            <button class="primary" (click)="make(script().sections[0].key!)"
                    [disabled]="busy() || !script().sections.length || starting() !== null">
              {{ starting() ? 'Starting…' : 'Make the Short (9:16)' }}</button>
            <span class="small muted">This idea is Shorts only: no long video.</span>
          </div>
        } @else {
        <div class="row">
          <button class="primary" (click)="makeLong()" [disabled]="busy() || starting() === 'long'">
            {{ starting() === 'long' ? 'Starting…' : 'Make the long video (16:9)' }}</button>
          <button (click)="makeAllShorts()" [disabled]="busy() || !markedShorts()">
            Make Shorts for the {{ markedShorts() }} marked sections (9:16)</button>
        </div>
        <ul class="sections">
          @for (sec of script().sections; track sec.key) {
            <li>
              <span class="grow"><strong>{{ sec.heading }}</strong>
                @if (sec.short) { <span class="pill tiny">Short</span> }</span>
              <button class="sm" [class.primary]="sec.short" (click)="make(sec.key!)" [disabled]="busy() || starting() === sec.key">
                {{ starting() === sec.key ? 'Starting…' : 'Make a Short' }}</button>
            </li>
          }
        </ul>
        }
      }

      @if (boards().length) {
        <h4>Made from this script <span class="muted num">{{ boards().length }}</span></h4>
        <div class="boards">
          @for (b of boards(); track b.id) {
            <a class="board" [routerLink]="['/storyboards', b.id]">
              <span class="thumb" [class.wide]="b.format === 'long'">
                <img [src]="thumb(b)" alt="" loading="lazy" />
              </span>
              <span class="info">
                <strong>{{ b.title }}</strong>
                <span class="small muted">{{ b.format === 'short' ? 'Short' : 'Long video' }} ·
                  {{ b.style === 'ink' ? lookName(b.look) : 'flat' }} · {{ clock(b.duration) }} ·
                  {{ b.scene_count }} {{ b.style === 'ink' ? 'shots' : 'scenes' }} · {{ b.created_at | ago }}</span>
                @if (b.job) {
                  <span class="pill running">{{ b.job.message }}</span>
                } @else if (b.has_video) {
                  <span class="pill" [class.approved]="b.review === 'approved'" [class.failed]="b.review === 'rejected'">
                    {{ b.review === 'pending' ? 'to review' : b.review }}</span>
                } @else {
                  <span class="pill">not rendered</span>
                }
              </span>
            </a>
          }
        </div>
      }
    </section>
  `,
  styles: `
    .videos {
      display: grid;
      grid-template-columns: minmax(0, 1fr);    // never wider than the card, whatever an option says
      gap: 12px;
      h3 { margin: 0; }
      h4 { margin: 10px 0 -2px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
      p { margin: 4px 0 0; }
    }
    .row > button { white-space: normal; text-align: left; }
    .sections {
      list-style: none;
      margin: 0;
      padding: 0;
      li {
        display: flex;
        gap: 10px;
        align-items: center;
        padding: 6px 0;
        border-bottom: 1px solid var(--line);
      }
    }
    .look { max-width: 460px; }
    .look small { display: block; margin-top: 5px; font-size: 12.5px; color: var(--muted); }
    .pill.tiny {
      font-size: 10.5px;
      padding: 0 6px;
      margin-left: 6px;
    }
    .boards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr));
      gap: 10px;
    }
    .board {
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      text-decoration: none;
      min-width: 0;
      &:hover { border-color: var(--text); }
    }
    .thumb {
      flex: none;
      width: 54px;
      aspect-ratio: 9 / 16;
      border-radius: 6px;
      overflow: hidden;
      background: var(--sunken);
      &.wide { width: 112px; aspect-ratio: 16 / 9; }
      img { width: 100%; height: 100%; object-fit: cover; display: block; }
    }
    .info {
      display: grid;
      gap: 4px;
      justify-items: start;
      min-width: 0;
      strong { line-height: 1.3; }
    }
  `,
})
export class StoryboardsPanel {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly store = inject(JobsStore);
  private readonly router = inject(Router);

  readonly script = input.required<Script>();
  readonly busy = input(false);

  protected readonly boards = signal<StoryboardSummary[]>([]);
  protected readonly starting = signal<string | null>(null);
  protected readonly hasTake = signal(true);
  protected readonly looks = INK_LOOKS;
  /** An ink look, or the older flat style. The look can change later; the style cannot. */
  protected readonly look = signal<InkLook | 'flat'>('ink');
  protected readonly style = computed<'ink' | 'flat'>(() => (this.look() === 'flat' ? 'flat' : 'ink'));
  private inkLook(): InkLook {
    const look = this.look();
    return look === 'flat' ? 'ink' : look;
  }

  protected lookHint(): string {
    const look = this.look();
    if (look === 'flat') return 'The first style: scene by scene, flat colour.';
    const hint = INK_LOOKS.find((l) => l.value === look)?.hint ?? '';
    return hint.charAt(0).toUpperCase() + hint.slice(1) + '. You can change it later without planning again.';
  }

  protected thumb(b: StoryboardSummary): string {
    return this.api.stillUrl(b.id, Math.min(2, b.duration / 3), b.rendered_at ?? b.created_at, 180);
  }

  protected clock(seconds: number): string {
    const s = Math.round(seconds);
    return s < 60 ? `${s} s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

    protected lookName(look: InkLook): string {
    return (INK_LOOKS.find((l) => l.value === look)?.label ?? 'Ink sketch').toLowerCase();
  }

  constructor() {
    let known = '';
    effect(() => {
      const id = this.script().id;
      const signature = id + '|' + this.store.jobs().filter((j) => j.script_id === id && (j.storyboard_id || j.take_id))
        .map((j) => `${j.id}:${j.state}:${j.stage}`).join();
      if (signature !== known) {
        known = signature;
        untracked(() => {
          this.api.storyboards(id).subscribe({ next: (list) => this.boards.set(list), error: () => undefined });
          this.api.takes(id).subscribe({ next: (takes) => this.hasTake.set(takes.some((t) => t.chosen && t.ready)),
                                         error: () => undefined });
        });
      }
    });
  }

  protected markedShorts(): number {
    return this.script().sections.filter((s) => s.short).length;
  }

  protected makeLong(): void {
    this.starting.set('long');
    this.api.createStoryboard(this.script().id, null, 'long', this.style(), this.inkLook()).subscribe({
      next: (board) => {
        this.starting.set(null);
        this.store.refresh();
        this.router.navigate(['/storyboards', board.id]);
      },
      error: (err) => {
        this.starting.set(null);
        this.toasts.error(err);
      },
    });
  }

  /** Queue a Short storyboard for every section marked as one; they plan one after another. */
  protected makeAllShorts(): void {
    const keys = this.script().sections.filter((s) => s.short).map((s) => s.key!);
    let queued = 0;
    const next = () => {
      const key = keys.shift();
      if (!key) {
        this.toasts.ok(`${queued} Shorts queued. Each one is planned in turn.`);
        this.store.refresh();
        return;
      }
      this.api.createStoryboard(this.script().id, key, 'short', this.style(), this.inkLook()).subscribe({
        next: () => {
          queued++;
          next();
        },
        error: (err) => this.toasts.error(err),
      });
    };
    next();
  }

  protected make(sectionKey: string): void {
    this.starting.set(sectionKey);
    this.api.createStoryboard(this.script().id, sectionKey, 'short', this.style(), this.inkLook()).subscribe({
      next: (board) => {
        this.starting.set(null);
        this.store.refresh();
        this.router.navigate(['/storyboards', board.id]);
      },
      error: (err) => {
        this.starting.set(null);
        this.toasts.error(err);
      },
    });
  }
}
