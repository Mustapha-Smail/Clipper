import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { Confirm } from '../core/confirm';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import {
  BufferChannel, THEME_COLORS, THEME_STAGES, Theme, ThemeBoard, ThemeBoardItem, ThemeColor, ThemeContents, ThemePatch,
  ThemeStage,
} from '../core/models';
import { ThemesStore } from '../core/themes-store';
import { Toasts } from '../core/toast';
import { ThemePerformance } from '../shared/theme-performance';
import { byDate, byText, rememberedSort, sortBy, SortOption } from '../core/sorting';
import { AnimationsPage } from './animations';
import { IdeasPage } from './ideas';

type View = 'work' | 'performance' | 'settings';
type ListFilter = 'waiting' | 'done' | 'all';

/** One theme: its work stage by stage, its numbers and what they taught, and its settings. */
@Component({
  selector: 'app-theme-page',
  imports: [FormsModule, RouterLink, AgoPipe, IdeasPage, AnimationsPage, ThemePerformance],
  templateUrl: './theme.html',
  styleUrl: './theme.scss',
})
export class ThemePage {
  protected readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly themes = inject(ThemesStore);
  protected readonly jobs = inject(JobsStore);

  protected readonly stages = THEME_STAGES;
  protected readonly colors = THEME_COLORS;
  protected readonly id = signal(0);
  protected readonly board = signal<ThemeBoard | null>(null);
  protected readonly theme = computed(() => this.board()?.theme ?? null);
  protected readonly view = signal<View>('work');
  protected readonly stage = signal<ThemeStage | null>(null);
  /** The stage shown: the one you picked, else where the next step is. */
  protected readonly shownStage = computed<ThemeStage>(() => this.stage() ?? this.theme()?.next.stage ?? 'ideas');

  // Script and Voice lists: everything, filtered and sorted
  protected readonly query = signal('');
  protected readonly filter = signal<ListFilter>('all');
  protected readonly sorts: SortOption<ThemeBoardItem>[] = [
    { value: 'waiting', label: 'Waiting first', compare: (a, b) => Number(!this.waiting(a)) - Number(!this.waiting(b))
      || byDate(a.updated_at, b.updated_at) },
    { value: 'recent', label: 'Recently updated', compare: (a, b) => byDate(a.updated_at, b.updated_at) },
    { value: 'oldest', label: 'Least recently updated', compare: (a, b) => byDate(a.updated_at, b.updated_at, false) },
    { value: 'title', label: 'Title, A to Z', compare: (a, b) => byText(a.title, b.title) },
    { value: 'longest', label: 'Longest', compare: (a, b) => b.words - a.words },
  ];
  protected readonly sort = rememberedSort('clipper.theme.sort', this.sorts);

  /** What belongs to the step at all: every script for Script, approved ones for Voice. */
  private readonly base = computed(() => {
    const all = this.board()?.items ?? [];
    return this.shownStage() === 'voice' ? all.filter((i) => i.status === 'approved') : all;
  });

  /** Waiting at this step: a draft for Script, an approved script with no voice for Voice. */
  protected waiting(item: ThemeBoardItem): boolean {
    return this.shownStage() === 'voice' ? !item.voiced : item.status === 'draft';
  }

  protected readonly filters = computed(() => {
    const base = this.base();
    const waiting = base.filter((i) => this.waiting(i)).length;
    const voice = this.shownStage() === 'voice';
    return [
      { value: 'all' as ListFilter, label: 'All', count: base.length },
      { value: 'waiting' as ListFilter, label: voice ? 'Needs a voice' : 'In progress', count: waiting },
      { value: 'done' as ListFilter, label: voice ? 'Voiced' : 'Approved', count: base.length - waiting },
    ];
  });

  protected readonly items = computed(() => {
    const words = this.query().toLowerCase().split(/\s+/).filter(Boolean);
    const list = this.base().filter((i) => (this.filter() === 'all' || (this.filter() === 'waiting') === this.waiting(i))
      && words.every((w) => i.title.toLowerCase().includes(w)));
    return sortBy(list, this.sorts, this.sort());
  });

  protected stageDone(item: ThemeBoardItem): boolean {
    return !this.waiting(item);
  }

  protected stateLabel(item: ThemeBoardItem): string {
    if (this.shownStage() === 'voice') return item.voiced ? 'voiced' : 'needs a voice';
    return item.status === 'approved' ? 'approved' : 'draft';
  }

  protected actionLabel(item: ThemeBoardItem): string {
    if (this.shownStage() === 'voice') return item.voiced ? 'Open the voice' : 'Add the voice';
    return item.status === 'draft' ? 'Edit the script' : 'Open the script';
  }

  protected duration(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }
  /** A batch of ideas is being written for this theme. */
  protected readonly writing = computed(() => this.jobs.active().some((j) => j.kind === 'ideas' && j.theme_id === this.id()));
  protected readonly ideaMode = signal<'fresh' | 'proven' | null>(null);

  // settings form
  protected readonly form = signal<{ name: string; description: string; notes: string }>({ name: '', description: '', notes: '' });
  protected readonly channels = signal<BufferChannel[] | null>(null);
  protected readonly channelsNote = signal('');
  protected readonly mergeInto = signal<number | null>(null);

  constructor() {
    this.route.paramMap.subscribe((params) => {
      this.id.set(Number(params.get('id')));
      this.board.set(null);
      this.load();
      this.contents.set(null);
    });
    this.api.settings().subscribe({ next: (b) => this.banners.set(b.settings.theme_banners), error: () => undefined });
    this.route.queryParamMap.subscribe((q) => {
      this.view.set((q.get('view') as View) || 'work');
      if (this.view() === 'settings' && this.channels() === null) this.loadChannels();
      this.stage.set((q.get('stage') as ThemeStage) || null);
      this.ideaMode.set((q.get('ideas') as 'fresh' | 'proven') || null);
    });
    // A job finished: a script of this theme may have moved on to the next stage.
    effect(() => {
      if (this.jobs.finished().length && untracked(() => this.board())) this.load();
    });
  }

  protected load(): void {
    this.api.themeBoard(this.id()).subscribe({
      next: (board) => {
        this.board.set(board);
        this.themes.put(board.theme);
        const t = board.theme;
        this.form.set({ name: t.name, description: t.description, notes: t.notes });
      },
      error: (err) => {
        this.toasts.error(err);
        if (err?.status === 404) this.router.navigateByUrl('/themes');
      },
    });
  }

  protected setView(view: View): void {
    this.router.navigate([], { queryParams: { view: view === 'work' ? null : view }, queryParamsHandling: 'merge',
      replaceUrl: true });
  }

  protected setStage(stage: ThemeStage): void {
    this.query.set('');
    this.filter.set('all');
    this.router.navigate([], { queryParams: { stage, view: null, ideas: null }, queryParamsHandling: 'merge',
      replaceUrl: true });
  }

  /** From Performance: generate ideas built on what worked. */
  protected buildOnWins(): void {
    this.router.navigate([], { queryParams: { stage: 'ideas', view: null, ideas: 'proven' }, queryParamsHandling: 'merge' });
  }

  protected count(stage: ThemeStage): number {
    const t = this.theme();
    if (!t) return 0;
    return stage === 'ideas' ? t.counts.ideas + t.inbox : t.counts[stage];
  }

  protected open(item: ThemeBoardItem): void {
    const tab = this.shownStage() === 'voice' ? 'voice' : null;
    this.router.navigate(['/scripts', item.id], { queryParams: tab ? { tab } : {} });
  }

  /** A storyboard's own part of its title: "Script · Chapter" shows "Chapter". */
  protected boardTitle(item: ThemeBoardItem, title: string): string {
    if (!title || title === item.title) return 'Whole video';
    return title.startsWith(item.title + ' · ') ? title.slice(item.title.length + 3) : title;
  }

  protected boardLabel(b: ThemeBoardItem['storyboards'][number]): string {
    if (b.published) return 'out';
    if (b.post && b.post !== 'cancelled' && b.post !== 'failed' && b.post !== 'removed') return 'scheduled';
    if (b.review === 'approved') return 'approved';
    if (b.has_video) return 'to review';
    return 'planned';
  }

  // --- settings ---------------------------------------------------------------------

  private save(patch: ThemePatch, done = 'Saved'): void {
    this.api.updateTheme(this.id(), patch).subscribe({
      next: (theme) => {
        this.board.update((b) => (b ? { ...b, theme } : b));
        this.themes.put(theme);
        this.toasts.ok(done);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected saveText(): void {
    const t = this.theme();
    const f = this.form();
    if (!t) return;
    const patch: ThemePatch = {};
    if (f.name.trim() && f.name.trim() !== t.name) patch.name = f.name.trim();
    if (f.description.trim() !== t.description) patch.description = f.description.trim();
    if (f.notes !== t.notes) patch.notes = f.notes;
    if (Object.keys(patch).length) this.save(patch, patch.name ? 'Renamed: new ideas and scripts use the new name' : 'Saved');
  }

  protected setForm(field: 'name' | 'description' | 'notes', value: string): void {
    this.form.update((f) => ({ ...f, [field]: value }));
  }

  protected setColor(color: ThemeColor): void {
    this.save({ color }, 'Colour changed');
  }

  protected setStatus(status: Theme['status']): void {
    const said = { active: 'Active again', paused: 'Paused: hidden from the menu', archived: 'Archived' }[status];
    this.save({ status }, said);
  }

  private loadChannels(): void {
    this.api.postChannels('storyboard').subscribe({
      next: (found) => this.channels.set(found.channels),
      error: (err) => {
        this.channels.set([]);
        this.channelsNote.set(err?.error?.detail ?? 'Buffer is not set up: connect it in .env to choose channels.');
      },
    });
  }

  protected toggleChannel(id: string): void {
    const current = new Set(this.theme()?.channels ?? []);
    if (current.has(id)) current.delete(id);
    else current.add(id);
    this.save({ channels: [...current] }, 'Channels saved');
  }

  // --- banner ------------------------------------------------------------------------

  /** Whether the AI draws banners (Settings); uploading your own picture works either way. */
  protected readonly banners = signal(false);
  protected readonly bannerNote = signal('');

  protected drawBanner(): void {
    this.api.drawBanner(this.id(), this.bannerNote().trim()).subscribe({
      next: () => {
        this.bannerNote.set('');
        this.jobs.refresh();
        this.toasts.ok('Drawing the banner: about a minute');
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected uploadBanner(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.api.uploadBanner(this.id(), file).subscribe({
      next: (theme) => this.applyTheme(theme, 'Banner saved'),
      error: (err) => this.toasts.error(err),
    });
  }

  protected removeBanner(): void {
    this.api.removeBanner(this.id()).subscribe({
      next: (theme) => this.applyTheme(theme, 'Banner removed'),
      error: (err) => this.toasts.error(err),
    });
  }

  private applyTheme(theme: Theme, done: string): void {
    this.board.update((b) => (b ? { ...b, theme } : b));
    this.themes.put(theme);
    this.toasts.ok(done);
  }

  // --- deleting ----------------------------------------------------------------------

  protected readonly contents = signal<ThemeContents | null>(null);
  protected readonly confirmName = signal('');
  protected readonly deleting = signal(false);
  protected readonly nameTyped = computed(() =>
    this.confirmName().trim().toLowerCase() === (this.theme()?.name ?? '').trim().toLowerCase());

  protected askDelete(): void {
    this.confirmName.set('');
    this.api.themeContents(this.id()).subscribe({ next: (c) => this.contents.set(c), error: (err) => this.toasts.error(err) });
  }

  protected readonly others = computed(() => (this.themes.all() ?? []).filter((t) => t.id !== this.id()));

  protected async merge(): Promise<void> {
    const into = this.others().find((t) => t.id === this.mergeInto());
    const t = this.theme();
    if (!into || !t) return;
    if (!(await this.confirm.ask({
      title: `Merge “${t.name}” into “${into.name}”?`,
      message: 'Its ideas and scripts move over and this theme is deleted. This cannot be undone.',
      confirm: 'Merge', danger: true,
    }))) return;
    this.api.mergeTheme(t.id, into.id).subscribe({
      next: (merged) => {
        this.themes.load();
        this.toasts.ok(`Merged into “${merged.name}”`);
        this.router.navigate(['/themes', merged.id]);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected remove(): void {
    const t = this.theme();
    if (!t) return;
    this.deleting.set(true);
    this.api.deleteTheme(t.id, this.confirmName()).subscribe({
      next: () => {
        this.deleting.set(false);
        this.themes.load();
        this.toasts.ok(`“${t.name}” and everything in it were deleted`);
        this.router.navigateByUrl('/themes');
      },
      error: (err) => {
        this.deleting.set(false);
        this.toasts.error(err);
      },
    });
  }

}
