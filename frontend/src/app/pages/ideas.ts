import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Api, errorMessage } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { AUDIENCES, AppSettings, Audience, Idea, IdeaPatch, IdeaStatus, IdeaVideo } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';

type Tab = 'generate' | 'review' | 'approved' | 'parked' | 'skipped';

/** Ideas for the animated channel: proposed by Claude, checked against YouTube, decided by you. */
@Component({
  selector: 'app-ideas-page',
  imports: [FormsModule, RouterLink, AgoPipe],
  templateUrl: './ideas.html',
  styleUrl: './ideas.scss',
})
export class IdeasPage {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  protected readonly store = inject(JobsStore);
  private readonly router = inject(Router);
  protected readonly starting = signal<number | null>(null);

  protected readonly tabs: { value: Tab; label: string }[] = [
    { value: 'generate', label: 'Generate' },
    { value: 'review', label: 'To review' },
    { value: 'approved', label: 'Approved' },
    { value: 'parked', label: 'Parked' },
    { value: 'skipped', label: 'Skipped' },
  ];
  protected readonly tab = signal<Tab>('generate');
  protected readonly ideas = signal<Idea[] | null>(null);

  private settings: AppSettings | null = null;
  protected readonly theme = signal('');
  protected readonly audiences = AUDIENCES;
  protected readonly audience = signal<Audience>('everyone');
  protected readonly audienceHint = computed(() => {
    const hint = AUDIENCES.find((a) => a.value === this.audience())?.hint ?? '';
    return hint.charAt(0).toUpperCase() + hint.slice(1);
  });
  protected readonly count = signal(8);
  protected readonly generating = signal(false);
  protected readonly ownText = signal('');
  protected readonly adding = signal(false);
  /** Ideas being checked right now, and the progress of a "check all" run. */
  protected readonly checking = signal<Set<number>>(new Set());
  protected readonly batch = signal<{ done: number; total: number } | null>(null);

  /** The magic wand: theme suggestions when inspiration runs out. */
  protected readonly themeIdeas = signal<{ theme: string; why: string; example: string }[] | null>(null);
  protected readonly wanding = signal(false);
  protected readonly hintUsed = signal('');

  protected wand(): void {
    // What is already typed is taken as a direction, unless it is just the current saved theme.
    const typed = this.theme().trim();
    const hint = typed && typed !== this.settings?.studio_theme ? typed : '';
    this.wanding.set(true);
    this.api.suggestThemes(hint).subscribe({
      next: (list) => {
        this.wanding.set(false);
        this.hintUsed.set(hint);
        this.themeIdeas.set(list);
      },
      error: (err) => {
        this.wanding.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected useTheme(theme: string): void {
    this.theme.set(theme);
    this.themeIdeas.set(null);
    this.toasts.ok('Theme set. Choose how many ideas, then Suggest ideas.');
  }

  /** '' shows every theme. */
  protected readonly themeFilter = signal('');

  private bucket(idea: Idea): Tab {
    return idea.status === 'inbox' ? 'generate' : idea.status === 'new' ? 'review' : idea.status;
  }

  private inTheme(idea: Idea): boolean {
    return !this.themeFilter() || idea.theme === this.themeFilter();
  }

  protected readonly shown = computed(() =>
    (this.ideas() ?? []).filter((i) => this.bucket(i) === this.tab() && this.inTheme(i)));
  protected readonly counts = computed(() => {
    const counts: Record<Tab, number> = { generate: 0, review: 0, approved: 0, parked: 0, skipped: 0 };
    for (const idea of this.ideas() ?? []) if (this.inTheme(idea)) counts[this.bucket(idea)]++;
    return counts;
  });
  /** Every theme with ideas, most ideas first, for the filter. */
  protected readonly themes = computed(() => {
    const counts = new Map<string, number>();
    for (const idea of this.ideas() ?? []) counts.set(idea.theme, (counts.get(idea.theme) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  });
  /** The shown ideas grouped by theme, keeping the score order inside each group. */
  protected readonly groups = computed(() => {
    const groups = new Map<string, Idea[]>();
    for (const idea of this.shown()) groups.set(idea.theme, [...(groups.get(idea.theme) ?? []), idea]);
    return [...groups.entries()].map(([theme, ideas]) => ({ theme, ideas }));
  });
  protected readonly unchecked = computed(() => this.shown().filter((i) => i.score === null));

  constructor() {
    this.load();
    this.api.settings().subscribe({
      next: (bundle) => {
        this.settings = bundle.settings;
        this.theme.set(bundle.settings.studio_theme);
        this.audience.set(bundle.settings.audience ?? 'everyone');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  /** Saved at once in Settings: the next ideas, research and scripts are written for this audience. */
  protected setAudience(audience: Audience): void {
    this.audience.set(audience);
    if (!this.settings) return;
    const updated = { ...this.settings, audience };
    this.api.saveSettings(updated).subscribe({
      next: () => {
        this.settings = updated;
        this.toasts.ok(`New ideas and scripts are now written for: ${AUDIENCES.find((a) => a.value === audience)?.label}`);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  private load(): void {
    this.api.ideas().subscribe({ next: (list) => this.ideas.set(list), error: (err) => this.toasts.error(err) });
  }

  /** Replace one idea in the list without reloading the rest. */
  private put(idea: Idea): void {
    this.ideas.update((list) => (list ?? []).map((i) => (i.id === idea.id ? idea : i)));
  }

  protected generate(): void {
    const theme = this.theme().trim();
    if (!theme) {
      this.toasts.error('Write the theme of your channel first');
      return;
    }
    this.generating.set(true);
    // The theme is remembered in Settings, so the next visit starts from it.
    if (this.settings && theme !== this.settings.studio_theme) {
      const updated = { ...this.settings, studio_theme: theme };
      this.api.saveSettings(updated).subscribe({ next: () => (this.settings = updated), error: () => undefined });
    }
    this.api.generateIdeas(this.count(), theme).subscribe({
      next: (made) => {
        this.generating.set(false);
        this.toasts.ok(`${made.length} new ideas under Generate. Keep the ones you like; they move to To review.`);
        this.tab.set('generate');
        this.load();
      },
      error: (err) => {
        this.generating.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected addOwn(fold: HTMLDetailsElement): void {
    const text = this.ownText().trim();
    if (text.length < 3) {
      this.toasts.error('Describe your idea in a few words first');
      return;
    }
    this.adding.set(true);
    this.api.addIdea(text).subscribe({
      next: (idea) => {
        this.adding.set(false);
        this.ownText.set('');
        fold.open = false;
        this.toasts.ok(`Added "${idea.title}"`);
        this.tab.set('review');
        this.load();
      },
      error: (err) => {
        this.adding.set(false);
        this.toasts.error(err);
      },
    });
  }

  private setChecking(id: number, on: boolean): void {
    this.checking.update((set) => {
      const next = new Set(set);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  /** Check one idea. Resolves to false when it failed, so a batch can stop. */
  protected check(idea: Idea): Promise<boolean> {
    this.setChecking(idea.id, true);
    return new Promise((resolve) =>
      this.api.validateIdea(idea.id).subscribe({
        next: (checked) => {
          this.setChecking(idea.id, false);
          this.put(checked);
          resolve(true);
        },
        error: (err) => {
          this.setChecking(idea.id, false);
          this.toasts.error(`${idea.title}: ${errorMessage(err)}`);
          resolve(false);
        },
      }),
    );
  }

  /** Check every unchecked idea in the tab, one after another. */
  protected async checkAll(): Promise<void> {
    const queue = this.unchecked();
    this.batch.set({ done: 0, total: queue.length });
    for (const idea of queue) {
      if (!(await this.check(idea))) break;
      this.batch.update((b) => (b ? { ...b, done: b.done + 1 } : b));
    }
    this.batch.set(null);
    this.load();   // re-sort now that scores exist
  }

  private patch(idea: Idea, patch: IdeaPatch, done?: string): void {
    this.api.updateIdea(idea.id, patch).subscribe({
      next: (updated) => {
        if (done) this.toasts.ok(done);
        this.put(updated);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  /** The length to start from: what the check suggested, else the usual length for the format. */
  protected suggestedMinutes(idea: Idea): number {
    return idea.length?.minutes ?? (idea.format === 'list' ? 10 : 6);
  }

  protected suggestedSeconds(idea: Idea): number {
    return idea.length?.seconds ?? 50;
  }

  /** A long video (with Shorts cut from its sections), or Shorts only. */
  protected setVideo(idea: Idea, video: 'long' | 'short'): void {
    if (idea.video !== video) this.patch(idea, { video });
  }

  /** Research and write the script for an approved idea, then open it. */
  protected writeScript(idea: Idea, length: number): void {
    const short = idea.video === 'short';
    const [low, high] = short ? [15, 90] : [1, 30];
    if (!Number.isInteger(length) || length < low || length > high) {
      this.toasts.error(short ? 'Choose a length between 15 and 90 seconds' : 'Choose a length between 1 and 30 minutes');
      return;
    }
    this.starting.set(idea.id);
    this.api.startScript(idea.id, idea.video, length).subscribe({
      next: (script) => {
        this.starting.set(null);
        this.store.refresh();
        this.router.navigate(['/scripts', script.id]);
      },
      error: (err) => {
        this.starting.set(null);
        this.toasts.error(err);
      },
    });
  }

  /** Inbox: keep an idea (it moves to To review) or let it go (Skipped, so it is not suggested again). */
  protected keep(idea: Idea, keep: boolean): void {
    this.patch(idea, { status: keep ? 'new' : 'skipped' });
  }

  protected async keepAll(keep: boolean): Promise<void> {
    const list = this.shown().filter((i) => i.status === 'inbox');
    if (!keep && !(await this.confirm.ask({ title: `Let go of all ${list.length} ideas here?`,
      message: 'They move to Skipped, where you can bring any of them back.', confirm: 'Let them go' }))) return;
    for (const idea of list) this.keep(idea, keep);
  }

  protected decide(idea: Idea, status: IdeaStatus): void {
    this.patch(idea, { status: idea.status === status ? 'new' : status });
  }

  protected saveNotes(idea: Idea, notes: string): void {
    if (notes !== idea.notes) this.patch(idea, { notes }, 'Notes saved');
  }

  protected saveEdits(idea: Idea, title: string, hook: string, query: string): void {
    const patch: IdeaPatch = {};
    if (title.trim() && title.trim() !== idea.title) patch.title = title.trim();
    if (hook.trim() !== idea.hook) patch.hook = hook.trim();
    if (query.trim().length >= 2 && query.trim() !== idea.search_query) patch.search_query = query.trim();
    if (!Object.keys(patch).length) return;
    this.patch(idea, patch, 'Saved. Check it again to update the score.');
  }

  protected async remove(idea: Idea): Promise<void> {
    if (!(await this.confirm.ask({ title: `Remove "${idea.title}"?`, message: 'The idea is deleted. Scripts written from it are kept.',
      confirm: 'Remove', danger: true }))) return;
    this.api.deleteIdea(idea.id).subscribe({
      next: () => this.ideas.update((list) => (list ?? []).filter((i) => i.id !== idea.id)),
      error: (err) => this.toasts.error(err),
    });
  }

  protected views(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—';
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
    return String(n);
  }

  protected age(video: IdeaVideo): string {
    const years = (Date.now() - new Date(video.published).getTime()) / (365.25 * 864e5);
    return years >= 1 ? `${Math.floor(years)} y` : `${Math.max(1, Math.floor(years * 12))} mo`;
  }

  protected length(video: IdeaVideo): string {
    const m = Math.floor(video.duration_s / 60);
    return m ? `${m} min` : `${video.duration_s} s`;
  }

  /** The YouTube results with the most-viewed first. */
  protected competition(idea: Idea): IdeaVideo[] {
    return [...(idea.research?.videos ?? [])].sort((a, b) => b.views - a.views);
  }
}
