import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { Script, StoryboardSummary } from '../core/models';
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
        <label class="field look"><span>Look</span>
          <select [ngModel]="style()" (ngModelChange)="style.set($event)">
            <option value="ink">Ink sketch: one hand-drawn page, actors act on things</option>
            <option value="flat">Flat colour: the first style, scene by scene</option>
          </select>
        </label>
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

      @for (b of boards(); track b.id) {
        <a class="board" [routerLink]="['/storyboards', b.id]">
          <span class="grow">
            <strong>{{ b.title }}</strong>
            <span class="small muted"> · {{ b.format === 'short' ? 'Short' : 'Long video' }} · {{ b.style === 'ink' ? 'ink' : 'flat' }} ·
              {{ b.duration.toFixed(0) }} s · {{ b.scene_count }} {{ b.style === 'ink' ? 'shots' : 'scenes' }} · {{ b.created_at | ago }}</span>
          </span>
          @if (b.job) {
            <span class="pill running">{{ b.job.message }}</span>
          } @else if (b.has_video) {
            <span class="pill" [class.approved]="b.review === 'approved'" [class.failed]="b.review === 'rejected'">
              {{ b.review === 'pending' ? 'to review' : b.review }}</span>
          } @else {
            <span class="pill">not rendered</span>
          }
        </a>
      }
    </section>
  `,
  styles: `
    .videos {
      display: grid;
      gap: 12px;
      h3 { margin: 0; }
      p { margin: 4px 0 0; }
    }
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
    .pill.tiny {
      font-size: 10.5px;
      padding: 0 6px;
      margin-left: 6px;
    }
    .board {
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 10px 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      text-decoration: none;
      &:hover { border-color: var(--text); }
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
  protected readonly style = signal<'ink' | 'flat'>('ink');

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
    this.api.createStoryboard(this.script().id, null, 'long', this.style()).subscribe({
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
      this.api.createStoryboard(this.script().id, key, 'short', this.style()).subscribe({
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
    this.api.createStoryboard(this.script().id, sectionKey, 'short', this.style()).subscribe({
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
