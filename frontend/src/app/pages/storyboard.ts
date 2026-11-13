import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe, MegabytesPipe, UntilPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import {
  ArchivePhoto, Asset, Chapters, INK_LOOKS, InkLook, MusicSuggestion, MusicTrack, StoryElement, StoryScene, Storyboard,
  SuggestedTrack, YoutubeText, Place, ExportInfo,
} from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { CostReportView } from '../shared/cost-report';
import { PublishCheckView } from '../shared/publish-check';
import { YoutubeTextView } from '../shared/youtube-text';
import { JobCard } from '../shared/job-card';

export const SLOTS = ['top_left', 'top', 'top_right', 'left', 'center', 'right', 'bottom_left', 'bottom', 'bottom_right'];
export const CAST = ['narrator', 'person', 'scientist', 'worker', 'royal', 'fancy', 'kid', 'chef', 'woman', 'elder'];
export const POSES = ['stand', 'point_left', 'point_right', 'shrug', 'arms_up', 'think', 'wave', 'facepalm'];
export const EXPRESSIONS = ['neutral', 'happy', 'surprised', 'worried', 'smug', 'deadpan', 'angry', 'sad'];
export const BACKGROUNDS = ['paper', 'grid', 'floor', 'sky', 'yellow', 'blue'];
export const CAMERAS = ['still', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right'];
export const MOTIONS = ['none', 'bounce', 'shake', 'pulse', 'float', 'spin', 'grow'];

const INK_CHEAT_SHEET = `N shot "what the viewer sees"   (use Split to start a shot)
N actor WHO [PLACE] [POSE] [FACE] [talk] [small|large] [as ID]
N draw PROP [PLACE] [small|large] ["label"] [as ID]
N write "1 to 6 words" [PLACE] [big] [COLOUR] [as ID]
N do ACTOR POSE|ACTION [TARGET] [FACE]      N face ACTOR FACE
N move ACTOR|PROP PLACE|TARGET [walk|run|march|zombie]
N arrow FROM TO [COLOUR]             N circle|underline|cross|tick TARGET [COLOUR]
N shake|bounce|pulse|spin|grow|shrink TARGET
N erase TARGET|all                   N look TARGET|all   (the camera moves in, or back out)
N flag COUNTRY    N map COUNTRY...    N timeline "label" "label"...    (COUNTRY: ro, it, bg...)
N who ID "Full Name" TRAIT...  then  N actor ID    (traits: tonsure robe mitre laurel toga crown beard...)
N photo "search words" [PLACE] [small|large|full]   (a real picture from Wikimedia Commons)

WHO      ${CAST.join(' ')}
PLACE    ${SLOTS.join(' ')}
POSE     stand point hold push pull carry drop shrug think facepalm cheer wave surprised sad
         crossed hips present scared   (point, hold, push, pull, carry take a TARGET;
         the actor walks up to it; carry takes it along until drop)
ACTION   jump kick throw pickup stumble laugh dance cartwheel jacks signal
         (recorded movements, played once; kick and throw send a TARGET flying)
FACE     ${EXPRESSIONS.join(' ')}
COLOUR   warm bad good gold green
TARGET   an actor's name, a prop's name, an ID given with "as", or a PLACE
SETTING  where a shot happens, in the cartoon look: the Setting field above (study, church, town,
         hills, night, idea, or any snake_case name: Claude draws new ones). Empty = same as before.`;

function blank(kind: StoryElement['kind']): StoryElement {
  return {
    kind, name: kind === 'character' ? 'narrator' : kind === 'prop' ? '' : '', slot: 'center',
    to_slot: kind === 'arrow' ? 'right' : null, size: 'medium', pose: 'stand', expression: 'neutral',
    facing: 'right', talking: false, text: '', enter: 'pop', enter_word: null, motion: 'none',
  };
}

const BOARD_TABS = ['video', 'scenes', 'sound', 'publish'] as const;
type BoardTab = (typeof BOARD_TABS)[number];

/** One storyboard in four tabs: the video and its review, the scenes, the sound, and publishing. */
@Component({
  selector: 'app-storyboard-page',
  imports: [FormsModule, RouterLink, JobCard, AgoPipe, UntilPipe, MegabytesPipe, CostReportView, PublishCheckView, YoutubeTextView],
  templateUrl: './storyboard.html',
  styleUrl: './storyboard.scss',
})
export class StoryboardPage {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly router = inject(Router);
  protected readonly store = inject(JobsStore);
  private readonly route = inject(ActivatedRoute);
  private readonly id = Number(this.route.snapshot.paramMap.get('id'));

  protected readonly tabs: { value: BoardTab; label: string }[] = [
    { value: 'video', label: 'Video' }, { value: 'scenes', label: 'Scenes' },
    { value: 'sound', label: 'Sound' }, { value: 'publish', label: 'Publish' },
  ];
  /** Kept in the address (?tab=scenes), so a link or a reload opens the same tab. */
  protected readonly tab = signal<BoardTab>(BOARD_TABS.includes(this.route.snapshot.queryParamMap.get('tab') as BoardTab)
    ? this.route.snapshot.queryParamMap.get('tab') as BoardTab : 'video');

  protected setTab(tab: BoardTab): void {
    this.tab.set(tab);
    this.router.navigate([], { queryParams: { tab: tab === 'video' ? null : tab }, replaceUrl: true });
  }

  protected readonly options = { SLOTS, CAST, POSES, EXPRESSIONS, BACKGROUNDS, CAMERAS, MOTIONS };
  protected readonly Math = Math;
  protected readonly inkCameras = [
    { value: 'wide', label: 'Wide, follows the action' }, { value: 'push', label: 'Push: slowly moves in' },
    { value: 'drift', label: 'Drift: a calm slide' }, { value: 'close', label: 'Close: frames small things tightly' },
  ];
  protected readonly cheatSheet = INK_CHEAT_SHEET;
  protected readonly looks = INK_LOOKS;
  protected readonly board = signal<Storyboard | null>(null);
  protected readonly scenes = signal<StoryScene[]>([]);
  protected readonly library = signal<Asset[]>([]);
  protected readonly placeList = signal<Place[]>([]);
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
  protected readonly ink = computed(() => this.board()?.style === 'ink');
  protected readonly dirty = computed(() => {
    const board = this.board();
    return !!board && JSON.stringify(this.scenes()) !== JSON.stringify(board.scenes);
  });

  constructor() {
    this.load();
    this.api.assets().subscribe({ next: (list) => this.library.set(list), error: () => undefined });
    this.api.places().subscribe({ next: (list) => this.placeList.set(list), error: () => undefined });
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
    // An ink shot builds up as it goes: show it near its end, with everything drawn.
    // (before the camera starts gliding to the next shot, about 0.3 s before it begins)
    const t = this.ink() ? Math.max(this.start(index), this.end(index) - 0.45)
      : Math.min(this.end(index) - 0.05, this.start(index) + Math.max(0.6, (this.end(index) - this.start(index)) * 0.7));
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
  protected readonly searchOnline = signal(true);
  protected readonly adding = signal<string | null>(null);

  protected chosen(sg: MusicSuggestion): SuggestedTrack | null {
    return sg.ranking.find((r) => r.ref === sg.choice) ?? null;
  }

  protected licence(code: string): string {
    return code === 'by' ? 'CC BY · credit' : code === 'cc0' ? 'CC0' : 'public domain';
  }

  protected hearTrack(r: SuggestedTrack, level: number): void {
    const board = this.board();
    if (!board) return;
    this.previewSrc.set(this.api.soundPreviewUrl(this.id, r.track_id, level, board.effects, 0, r.openverse_id));
  }

  /** Use a suggested track: online ones are added to the library first, with their credit. */
  protected useTrack(r: SuggestedTrack, level: number): void {
    if (r.track_id !== null) {
      this.setSound({ music_id: r.track_id, music_db: level, music_suggested: true });
      return;
    }
    this.adding.set(r.ref);
    this.api.importMusic(r.openverse_id!).subscribe({
      next: (track) => {
        this.adding.set(null);
        this.music.update((list) => [...list.filter((m) => m.id !== track.id), track]);
        this.suggestion.update((sg) => sg && {
          ...sg, ranking: sg.ranking.map((x) => (x.ref === r.ref ? { ...x, source: 'library', track_id: track.id } : x)),
        });
        this.setSound({ music_id: track.id, music_db: level, music_suggested: true });
      },
      error: (err) => {
        this.adding.set(null);
        this.toasts.error(err);
      },
    });
  }

  /** Ask the quick-tier model which track fits, if any. Costs a fraction of a cent. */
  protected suggest(): void {
    this.suggesting.set(true);
    this.api.suggestMusic(this.id, this.searchOnline()).subscribe({
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

  /** The YouTube text was written or saved: keep it, and let the checks see it. */
  protected setYoutube(text: YoutubeText): void {
    this.board.update((b) => b && { ...b, youtube: text });
    this.version.set(Date.now());
  }

  /** The setting shot i is in: its own, or the last one named before it. */
  protected placeOf(i: number): string {
    const list = this.scenes();
    for (let k = Math.min(i, list.length - 1); k >= 0; k--) if (list[k].place) return list[k].place!;
    return '';
  }

  protected lookName(look: InkLook): string {
    return (INK_LOOKS.find((l) => l.value === look)?.label ?? 'Ink sketch').toLowerCase();
  }

  protected lookHint(look: InkLook): string {
    const hint = INK_LOOKS.find((l) => l.value === look)?.hint ?? '';
    return hint.charAt(0).toUpperCase() + hint.slice(1);
  }

  /** Draw the same plan another way. Previews change at once; the video needs a render. */
  protected setLook(look: InkLook): void {
    this.api.setLook(this.id, look).subscribe({
      next: (board) => {
        this.board.set(board);
        this.version.set(Date.now());
        this.toasts.ok(`${this.lookName(look).replace(/^./, (c) => c.toUpperCase())} look saved. Render again to see it in the video.`);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected setSound(patch: { music_id?: number | null; music_db?: number; effects?: boolean;
                              music_suggested?: boolean }): void {
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

  /** The beats of an ink shot, as typed: blank lines are kept while editing and dropped on save. */
  protected setLines(scene: number, text: string): void {
    this.change(scene, { lines: text.split('\n') });
  }

  protected async removeScene(index: number): Promise<void> {
    const what = this.ink() ? 'shot' : 'scene';
    if (index === 0) {
      this.toasts.error(`The first ${what} cannot be removed; edit it instead`);
      return;
    }
    const after = this.ink() ? 'Its beats go too.' : 'The previous scene stays on screen for its words.';
    if (!(await this.confirm.ask({ title: `Remove this ${what}?`, message: after, confirm: 'Remove', danger: true }))) return;
    this.scenes.update((list) => list.filter((_, i) => i !== index));
  }

  protected splitAt(index: number, word: number): void {
    const scene = this.scenes()[index];
    if (this.ink()) {
      // beats from that word on move to the new shot
      const lines = scene.lines ?? [];
      const keep = lines.filter((l) => parseInt(l, 10) < word || isNaN(parseInt(l, 10)));
      const moved = lines.filter((l) => parseInt(l, 10) >= word);
      const shot: StoryScene = { ...structuredClone(scene), start_word: word, note: 'New shot', camera: 'wide', page: false,
                                 lines: moved };
      this.scenes.update((list) => [...list.slice(0, index), { ...scene, lines: keep }, shot, ...list.slice(index + 1)]);
      this.openScene.set(index + 1);
      return;
    }
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
    const scenes = this.scenes().map((s) => (s.lines ? { ...s, lines: s.lines.map((l) => l.trim()).filter(Boolean) } : s));
    this.api.saveScenes(this.id, scenes).subscribe({
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

  // --- archive pictures -------------------------------------------------------------------------

  protected readonly swapping = signal<number | null>(null);

  protected photoUrl(p: ArchivePhoto): string {
    return this.api.photoImageUrl(p.id, `${p.rank}-${this.version()}`, 280);
  }

  protected nextPhoto(p: ArchivePhoto): void {
    this.swapping.set(p.id);
    this.api.nextPhoto(p.id, this.id).subscribe({
      next: () => {
        this.swapping.set(null);
        this.toasts.ok('Another picture chosen. Render again to see it in the video.');
        this.load();
      },
      error: (err) => {
        this.swapping.set(null);
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

  protected async replan(): Promise<void> {
    if (!(await this.confirm.ask({ title: 'Plan again from scratch?', message: 'Your edits are replaced by a new plan.',
      confirm: 'Plan again', danger: true }))) return;
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

  protected readonly exporting = signal(false);

  /** The exported folder with its files under it, one per line. */
  protected exportListing(x: ExportInfo): string {
    return [x.folder, ...x.files.map((f) => `  ${f}`)].join('\n');
  }

  /** Copy the approved video and its publishing files to the output folder. */
  protected exportVideo(): void {
    this.exporting.set(true);
    this.api.exportStoryboard(this.id).subscribe({
      next: (info) => {
        this.exporting.set(false);
        this.board.update((b) => b && { ...b, export: info });
        this.version.set(Date.now());       // the authorship log has a new line
        this.toasts.ok('Exported to the output folder');
      },
      error: (err) => {
        this.exporting.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected async remove(): Promise<void> {
    const board = this.board();
    if (!board || !(await this.confirm.ask({ title: 'Delete this storyboard?', message: 'Its rendered video is deleted too.',
      confirm: 'Delete', danger: true }))) return;
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
