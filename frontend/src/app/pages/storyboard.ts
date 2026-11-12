import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { Asset, Chapters, MusicSuggestion, MusicTrack, StoryElement, StoryScene, Storyboard } from '../core/models';
import { Toasts } from '../core/toast';
import { JobCard } from '../shared/job-card';

export const SLOTS = ['top_left', 'top', 'top_right', 'left', 'center', 'right', 'bottom_left', 'bottom', 'bottom_right'];
export const CAST = ['narrator', 'person', 'scientist', 'worker', 'royal', 'fancy', 'kid', 'chef'];
export const POSES = ['stand', 'point_left', 'point_right', 'shrug', 'arms_up', 'think', 'wave', 'facepalm'];
export const EXPRESSIONS = ['neutral', 'happy', 'surprised', 'worried', 'smug', 'deadpan', 'angry', 'sad'];
export const BACKGROUNDS = ['paper', 'grid', 'floor', 'sky', 'yellow', 'blue'];
export const CAMERAS = ['still', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right'];
export const MOTIONS = ['none', 'bounce', 'shake', 'pulse', 'float', 'spin', 'grow'];

function blank(kind: StoryElement['kind']): StoryElement {
  return {
    kind, name: kind === 'character' ? 'narrator' : kind === 'prop' ? '' : '', slot: 'center',
    to_slot: kind === 'arrow' ? 'right' : null, size: 'medium', pose: 'stand', expression: 'neutral',
    facing: 'right', talking: false, text: '', enter: 'pop', enter_word: null, motion: 'none',
  };
}

/** One storyboard: its scenes with a preview frame each, every element editable; render and review. */
@Component({
  selector: 'app-storyboard-page',
  imports: [FormsModule, RouterLink, JobCard, AgoPipe],
  templateUrl: './storyboard.html',
  styleUrl: './storyboard.scss',
})
export class StoryboardPage {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly router = inject(Router);
  protected readonly store = inject(JobsStore);
  private readonly id = Number(inject(ActivatedRoute).snapshot.paramMap.get('id'));

  protected readonly options = { SLOTS, CAST, POSES, EXPRESSIONS, BACKGROUNDS, CAMERAS, MOTIONS };
  protected readonly board = signal<Storyboard | null>(null);
  protected readonly scenes = signal<StoryScene[]>([]);
  protected readonly library = signal<Asset[]>([]);
  protected readonly saving = signal(false);
  /** Bumped after a save or a job, so preview images reload. */
  protected readonly version = signal(Date.now());
  protected readonly openScene = signal<number | null>(null);
  protected readonly music = signal<MusicTrack[]>([]);
  protected readonly chapterList = signal<Chapters | null>(null);
  /** Chapter headings by section key, for the headers between scenes of a long video. */
  private headings = new Map<string, string>();

  protected readonly job = computed(() =>
    this.store.jobs().find((j) => j.storyboard_id === this.id && (j.state === 'queued' || j.state === 'running'))
    ?? this.board()?.job ?? null);
  protected readonly busy = computed(() => this.job() !== null);
  protected readonly dirty = computed(() => {
    const board = this.board();
    return !!board && JSON.stringify(this.scenes()) !== JSON.stringify(board.scenes);
  });

  constructor() {
    this.load();
    this.api.assets().subscribe({ next: (list) => this.library.set(list), error: () => undefined });
    this.api.music().subscribe({ next: (list) => this.music.set(list), error: () => undefined });
    effect(() => {
      const done = this.store.finished().filter((j) => j.storyboard_id === this.id);
      if (done.length) untracked(() => this.load());
    });
  }

  private load(): void {
    this.api.storyboard(this.id).subscribe({
      next: (board) => {
        this.board.set(board);
        if (!this.dirty() || !this.scenes().length) this.scenes.set(structuredClone(board.scenes));
        this.version.set(Date.now());
        if (!this.headings.size) {
          this.api.script(board.script_id).subscribe((script) => {
            this.headings = new Map(script.sections.map((s) => [s.key ?? '', s.heading]));
            this.scenes.update((list) => [...list]);   // redraw the headers
          });
        }
        if (board.format === 'long' && !this.chapterList()) {
          this.api.chapters(this.id).subscribe({ next: (c) => this.chapterList.set(c), error: () => undefined });
        }
      },
      error: (err) => this.toasts.error(err),
    });
  }

  // --- timing helpers ------------------------------------------------------------------

  protected start(index: number): number {
    const board = this.board();
    if (!board || !board.words.length || index === 0) return 0;
    return board.words[Math.min(this.scenes()[index].start_word, board.words.length - 1)].s;
  }

  protected end(index: number): number {
    const board = this.board();
    if (!board) return 0;
    return index + 1 < this.scenes().length ? this.start(index + 1) : board.duration;
  }

  protected spoken(index: number): { n: number; w: string }[] {
    const board = this.board();
    if (!board) return [];
    const from = this.scenes()[index].start_word;
    const to = index + 1 < this.scenes().length ? this.scenes()[index + 1].start_word : board.words.length;
    return board.words.slice(from, to).map((w, k) => ({ n: from + k, w: w.w }));
  }

  protected still(index: number): string {
    const t = Math.min(this.end(index) - 0.05, this.start(index) + Math.max(0.6, (this.end(index) - this.start(index)) * 0.7));
    return this.api.stillUrl(this.id, t, this.version(), this.board()?.format === 'long' ? 400 : 270);
  }

  /**
   * Scenes in collapsible groups so the page stays short: one per chapter for a
   * long video, one per 15 seconds for a Short.
   */
  protected readonly groups = computed(() => {
    const board = this.board();
    const scenes = this.scenes();
    if (!board || !scenes.length) return [];
    const groups: { from: number; label: string; indices: number[]; start: number; end: number }[] = [];
    scenes.forEach((_, i) => {
      const start = this.start(i);
      const chapter = this.chapterOf(i);
      const newGroup = !groups.length
        || (board.format === 'long' ? chapter !== null : start >= groups[groups.length - 1].start + 15);
      if (newGroup) {
        const label = board.format === 'long' ? (chapter || 'Opening') : `Part ${groups.length + 1}`;
        groups.push({ from: i, label, indices: [], start, end: start });
      }
      const group = groups[groups.length - 1];
      group.indices.push(i);
      group.end = this.end(i);
    });
    return groups;
  });
  private readonly openGroups = signal<Set<number> | null>(null);   // null: only the first is open

  protected isOpen(from: number): boolean {
    const open = this.openGroups();
    return open ? open.has(from) : from === 0;
  }

  protected toggleGroup(from: number): void {
    const open = new Set(this.openGroups() ?? [0]);
    if (open.has(from)) open.delete(from);
    else open.add(from);
    this.openGroups.set(open);
  }

  protected openAll(open: boolean): void {
    this.openGroups.set(new Set(open ? this.groups().map((g) => g.from) : []));
  }

  protected clock(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  }

  /** The chapter a scene opens, when it is the first scene of a new section. */
  protected chapterOf(index: number): string | null {
    const board = this.board();
    if (!board || board.format !== 'long') return null;
    const section = board.words[this.scenes()[index].start_word]?.section;
    const before = index ? board.words[this.scenes()[index - 1].start_word]?.section : undefined;
    return section && section !== before ? (this.headings.get(section) ?? '') : null;
  }

  // --- sound and publishing ---------------------------------------------------------------

  protected readonly suggestion = signal<MusicSuggestion | null>(null);
  protected readonly suggesting = signal(false);
  protected readonly previewSrc = signal('');

  /** Ask the quick-tier model which track fits, if any. Costs a fraction of a cent. */
  protected suggest(): void {
    this.suggesting.set(true);
    this.api.suggestMusic(this.id).subscribe({
      next: (s) => {
        this.suggesting.set(false);
        this.suggestion.set(s);
        this.api.music().subscribe((list) => this.music.set(list));   // tracks get measured on the way
      },
      error: (err) => {
        this.suggesting.set(false);
        this.toasts.error(err);
      },
    });
  }

  /** Mix the first 25 seconds with a track (or none) and play it. */
  protected hear(musicId: number | null, musicDb: number): void {
    const board = this.board();
    if (board) this.previewSrc.set(this.api.soundPreviewUrl(this.id, musicId, musicDb, board.effects));
  }

  protected trackName(id: number | null): string {
    return this.music().find((m) => m.id === id)?.name ?? 'a track';
  }

  protected setSound(patch: { music_id?: number | null; music_db?: number; effects?: boolean }): void {
    this.api.soundSettings(this.id, patch).subscribe({
      next: (board) => {
        this.board.set(board);
        this.toasts.ok('Sound saved. Render again to hear it.');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected subtitlesUrl(): string {
    return this.api.subtitlesUrl(this.id);
  }

  protected copy(text: string): void {
    navigator.clipboard.writeText(text).then(
      () => this.toasts.ok('Copied'),
      () => this.toasts.error('Could not copy: select the text and copy it yourself'),
    );
  }

  // --- editing -----------------------------------------------------------------------------

  protected change(scene: number, patch: Partial<StoryScene>): void {
    this.scenes.update((list) => list.map((s, i) => (i === scene ? { ...s, ...patch } : s)));
  }

  protected changeElement(scene: number, element: number, patch: Partial<StoryElement>): void {
    this.scenes.update((list) => list.map((s, i) => (i !== scene ? s : {
      ...s, elements: s.elements.map((e, k) => (k === element ? { ...e, ...patch } : e)),
    })));
  }

  protected addElement(scene: number, kind: StoryElement['kind']): void {
    const s = this.scenes()[scene];
    if (s.elements.length >= 4) {
      this.toasts.error('Four elements per scene at most: keep the picture readable');
      return;
    }
    this.change(scene, { elements: [...s.elements, blank(kind)] });
  }

  protected removeElement(scene: number, element: number): void {
    this.change(scene, { elements: this.scenes()[scene].elements.filter((_, k) => k !== element) });
  }

  protected removeScene(index: number): void {
    if (index === 0) {
      this.toasts.error('The first scene cannot be removed; edit it instead');
      return;
    }
    if (!confirm('Remove this scene? The previous scene stays on screen for its words.')) return;
    this.scenes.update((list) => list.filter((_, i) => i !== index));
  }

  protected splitAt(index: number, word: number): void {
    const scene = this.scenes()[index];
    const copy: StoryScene = { ...structuredClone(scene), start_word: word, note: 'New scene', elements: [] };
    this.scenes.update((list) => [...list.slice(0, index + 1), copy, ...list.slice(index + 1)]);
    this.openScene.set(index + 1);
  }

  protected discard(): void {
    const board = this.board();
    if (board) this.scenes.set(structuredClone(board.scenes));
  }

  protected save(after?: () => void): void {
    this.saving.set(true);
    this.api.saveScenes(this.id, this.scenes()).subscribe({
      next: (board) => {
        this.saving.set(false);
        this.board.set(board);
        this.scenes.set(structuredClone(board.scenes));
        this.version.set(Date.now());
        if (board.missing_props.length) {
          this.toasts.ok(`Saved. New props to draw when you render: ${board.missing_props.join(', ')}`);
        } else {
          this.toasts.ok('Saved');
        }
        after?.();
      },
      error: (err) => {
        this.saving.set(false);
        this.toasts.error(err);
      },
    });
  }

  // --- jobs and review ------------------------------------------------------------------------

  protected render(): void {
    const go = () => this.api.renderStoryboard(this.id).subscribe({
      next: () => {
        this.toasts.ok('Rendering. A one-minute Short takes a couple of minutes.');
        this.store.refresh();
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
    if (this.dirty()) this.save(go);
    else go();
  }

  protected replan(): void {
    if (!confirm('Plan all scenes again from scratch? Your edits are replaced.')) return;
    this.api.replan(this.id).subscribe({
      next: () => {
        this.store.refresh();
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected review(verdict: 'approved' | 'rejected' | 'pending'): void {
    this.api.reviewStoryboard(this.id, verdict).subscribe({
      next: (board) => this.board.set(board),
      error: (err) => this.toasts.error(err),
    });
  }

  protected remove(): void {
    const board = this.board();
    if (!board || !confirm('Delete this storyboard and its video?')) return;
    this.api.deleteStoryboard(this.id).subscribe({
      next: () => this.router.navigate(['/scripts', board.script_id]),
      error: (err) => this.toasts.error(err),
    });
  }

  protected videoUrl(): string {
    const board = this.board();
    return board ? this.api.storyboardVideoUrl(board) : '';
  }

  protected castThumb(e: StoryElement): string {
    return this.api.castUrl(e.name, e.pose, e.expression, 120);
  }

  protected propThumb(name: string): string {
    const asset = this.library().find((a) => a.name === name);
    return asset ? this.api.assetImageUrl(name, asset.updated_at, 96) : '';
  }

  protected label(value: string): string {
    return value.replace(/_/g, ' ');
  }
}
