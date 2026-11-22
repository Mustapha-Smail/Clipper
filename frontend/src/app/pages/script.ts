import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Api, errorMessage } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import {
  ClaimVerdict, HoldRole, Script, ScriptClaim, ScriptSection, StoryMap, StoryMapBeat, StoryMapEdit, StoryPurpose,
  StoryShape, StoryTemplate,
} from '../core/models';
import { ThemesStore } from '../core/themes-store';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { CostReportView } from '../shared/cost-report';
import { JobCard } from '../shared/job-card';
import { StoryboardsPanel } from '../shared/storyboards-panel';
import { VoicePanel } from '../shared/voice-panel';

const WORDS_PER_MINUTE = 150;

type ScriptTab = 'script' | 'voice' | 'videos';

interface Proposal {
  key: string;
  instruction: string;
  text: string | null;   // null while Claude is writing it
}

/** One script: edit the narration section by section, see its fact check, approve it. */
@Component({
  selector: 'app-script-page',
  imports: [FormsModule, RouterLink, JobCard, VoicePanel, StoryboardsPanel, AgoPipe, CostReportView],
  templateUrl: './script.html',
  styleUrl: './script.scss',
})
export class ScriptPage {
  private readonly api = inject(Api);
  protected readonly themes = inject(ThemesStore);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly router = inject(Router);
  protected readonly store = inject(JobsStore);
  private readonly route = inject(ActivatedRoute);
  private readonly id = Number(this.route.snapshot.paramMap.get('id'));
  protected readonly tabs: { value: ScriptTab; label: string }[] = [
    { value: 'script', label: 'Script' }, { value: 'voice', label: 'Voice' }, { value: 'videos', label: 'Videos' },
  ];
  /** Kept in the address (?tab=voice), so a link or a reload opens the same tab. */
  protected readonly tab = signal<ScriptTab>((this.route.snapshot.queryParamMap.get('tab') as ScriptTab) || 'script');

  protected setTab(tab: ScriptTab): void {
    this.tab.set(tab);
    this.router.navigate([], { queryParams: { tab: tab === 'script' ? null : tab }, replaceUrl: true });
  }

  protected readonly script = signal<Script | null>(null);
  /** The sections as you are editing them; saved with "Save changes". */
  protected readonly drafts = signal<ScriptSection[]>([]);
  protected readonly saving = signal(false);
  protected readonly proposal = signal<Proposal | null>(null);
  protected readonly storyMapDraft = signal<StoryMapEdit | null>(null);
  protected readonly storyMapSaving = signal(false);
  protected readonly storyTemplates: { value: StoryTemplate; label: string }[] = [
    { value: 'mechanism', label: 'Mechanism' }, { value: 'story_loop', label: 'Story loop' },
    { value: 'journey', label: 'Journey' }, { value: 'ranking', label: 'Ranking' },
  ];
  protected readonly storyPurposes: StoryPurpose[] = [
    'question', 'evidence', 'answer', 'twist', 'payoff', 'callback',
  ];
  protected readonly storyShapes: { value: StoryShape; label: string }[] = [
    { value: 'parallel', label: 'Parallel lanes' }, { value: 'explain', label: 'Explain' },
    { value: 'scorecard', label: 'Scorecard' }, { value: 'investigation', label: 'Investigation' },
  ];
  protected readonly verdictLabels: Record<ClaimVerdict, string> = {
    supported: 'Supported', contradicted: 'Contradicted', unclear: 'Unclear', joke: 'Joke',
  };

  /** The job working on this script right now, if any. */
  protected readonly job = computed(() =>
    this.store.jobs().find((j) => j.script_id === this.id && (j.state === 'queued' || j.state === 'running'))
    ?? this.script()?.job ?? null);
  protected readonly busy = computed(() => this.job() !== null);

  protected readonly dirty = computed(() => {
    const script = this.script();
    return !!script && JSON.stringify(this.drafts().map(strip)) !== JSON.stringify(script.sections.map(strip));
  });
  protected readonly words = computed(() => this.drafts().reduce((n, s) => n + count(s.text), 0));
  protected readonly minutes = computed(() => this.words() / WORDS_PER_MINUTE);

  /** Claims grouped by section key; claims pointing at no known section go under ''. */
  protected readonly claimsBySection = computed(() => {
    const keys = new Set(this.drafts().map((s) => s.key));
    const groups = new Map<string, ScriptClaim[]>();
    for (const claim of this.script()?.claims ?? []) {
      const key = keys.has(claim.section) ? claim.section : '';
      groups.set(key, [...(groups.get(key) ?? []), claim]);
    }
    return groups;
  });
  protected readonly problems = computed(() =>
    (this.script()?.claims ?? []).filter((c) => c.verdict === 'contradicted' || c.verdict === 'unclear'));
  protected readonly sourceUrl = computed(() =>
    new Map((this.script()?.research?.sources ?? []).map((s) => [s.n, s] as const)));

  constructor() {
    this.load(true);
    // When a job on this script ends, show what it changed.
    effect(() => {
      const done = this.store.finished().filter((j) => j.script_id === this.id);
      if (!done.length) return;
      untracked(() => {
        if (this.dirty()) {
          this.toasts.error('The job finished, but you have unsaved edits. Save or reload to see its result.');
          this.load(false);
        } else {
          this.load(true);
        }
      });
    });
  }

  private load(resetDrafts: boolean): void {
    this.api.script(this.id).subscribe({
      next: (script) => {
        this.script.set(script);
        if (resetDrafts) this.drafts.set(script.sections.map((s) => ({ ...s })));
      },
      error: (err) => this.toasts.error(err),
    });
  }

  // --- editing ---------------------------------------------------------------------

  protected edit(index: number, change: Partial<ScriptSection>): void {
    this.drafts.update((list) => list.map((s, i) => (i === index ? { ...s, ...change } : s)));
  }

  protected add(after: number): void {
    this.drafts.update((list) => [
      ...list.slice(0, after + 1), { key: null, heading: 'New section', text: '', short: false }, ...list.slice(after + 1)]);
  }

  protected async remove(index: number): Promise<void> {
    const section = this.drafts()[index];
    if (section.text.trim() && !(await this.confirm.ask({ title: `Remove the section "${section.heading}"?`,
      message: 'Its text leaves the draft. Nothing is saved until you press Save.', confirm: 'Remove', danger: true }))) {
      return;
    }
    this.drafts.update((list) => list.filter((_, i) => i !== index));
  }

  protected move(index: number, by: number): void {
    const to = index + by;
    this.drafts.update((list) => {
      if (to < 0 || to >= list.length) return list;
      const next = [...list];
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });
  }

  protected async discard(): Promise<void> {
    const script = this.script();
    if (!script || !(await this.confirm.ask({ title: 'Discard your unsaved edits?', message: 'The script goes back to its last saved version.',
      confirm: 'Discard', danger: true }))) return;
    this.drafts.set(script.sections.map((s) => ({ ...s })));
  }

  protected save(): void {
    const sections = this.drafts().filter((s) => s.text.trim() || s.heading.trim());
    if (!sections.length) {
      this.toasts.error('A script needs at least one section');
      return;
    }
    this.saving.set(true);
    this.api.saveSections(this.id, sections).subscribe({
      next: (script) => {
        this.saving.set(false);
        this.script.set(script);
        this.drafts.set(script.sections.map((s) => ({ ...s })));
        this.toasts.ok(script.claims ? 'Saved. Check the facts again before recording.' : 'Saved');
      },
      error: (err) => {
        this.saving.set(false);
        this.toasts.error(err);
      },
    });
  }

  // --- Claude ----------------------------------------------------------------------

  protected rewrite(section: ScriptSection, instruction: string): void {
    if (!section.key) {
      this.toasts.error('Save the script first, then ask for a rewrite of this section');
      return;
    }
    if (instruction.trim().length < 2) {
      this.toasts.error('Say how to rewrite it, for example: shorter, funnier, simpler words');
      return;
    }
    const key = section.key;
    this.proposal.set({ key, instruction, text: null });
    this.api.rewriteSection(this.id, key, instruction.trim()).subscribe({
      next: (result) => this.proposal.set({ key, instruction, text: result.text }),
      error: (err) => {
        this.proposal.set(null);
        this.toasts.error(err);
      },
    });
  }

  protected accept(index: number): void {
    const proposal = this.proposal();
    if (proposal?.text) this.edit(index, { text: proposal.text });
    this.proposal.set(null);
  }

  private queue(request: ReturnType<Api['factcheck']>, done: string): void {
    request.subscribe({
      next: () => {
        this.toasts.ok(done);
        this.store.refresh();
        this.load(false);
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected checkFacts(): void {
    if (this.dirty()) {
      this.toasts.error('Save your edits first, so the check runs on them');
      return;
    }
    this.queue(this.api.factcheck(this.id), 'Fact check queued');
  }

  protected checkHold(): void {
    if (this.dirty()) {
      this.toasts.error('Save your edits first, so the check runs on them');
      return;
    }
    this.queue(this.api.checkHold(this.id), 'Hold check queued. Under a minute.');
  }

  /** The 3 lines that cost the most points, shown without a click. */
  protected readonly holdWorst = computed(() =>
    [...(this.script()?.hold?.lines ?? [])].filter((l) => l.points < 0).sort((a, b) => a.points - b.points).slice(0, 3));

  protected readonly roleLabels: Record<HoldRole, string> = {
    hook: 'Hook', context: 'Context', stakes: 'Stakes', question: 'Question', twist: 'Twist', rehook: 'Rehook',
    fact: 'Fact', consequence: 'Consequence', payoff: 'Payoff', callback: 'Callback', filler: 'Filler',
  };

  protected async makeSimpler(): Promise<void> {
    if (this.dirty()) {
      this.toasts.error('Save your edits first, so the rewrite starts from them');
      return;
    }
    if (!(await this.confirm.ask({ title: 'Rewrite the whole script in plainer words?',
      message: 'Names, numbers and details the main idea does not need may be left out, and terms replaced with plain '
        + 'words; nothing new is added. You can switch back to the previous version. Check the facts again after.',
      confirm: 'Make it simpler' }))) return;
    this.queue(this.api.makeSimpler(this.id), 'Rewrite queued. About a minute.');
  }

  protected async makeSpoken(): Promise<void> {
    if (this.dirty()) {
      this.toasts.error('Save your edits first, so the rewrite starts from them');
      return;
    }
    if (!(await this.confirm.ask({ title: 'Rewrite the whole script so it sounds spoken?',
      message: 'Every fact is kept, and you can switch back to the previous version.', confirm: 'Rewrite' }))) return;
    this.queue(this.api.makeSpoken(this.id), 'Rewrite queued. About a minute.');
  }

  protected undoRewrite(): void {
    this.api.undoRewrite(this.id).subscribe({
      next: (script) => {
        this.script.set(script);
        this.drafts.set(script.sections.map((s) => ({ ...s })));
        this.toasts.ok('Swapped back. Click again to return to the rewrite.');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected async redraft(): Promise<void> {
    const map = this.script()?.story_map;
    const rebuild = !!map && (!map.current || map.problems.length > 0);
    const reason = map?.stale_reasons.join(' ') || map?.problems.join(' ');
    const title = rebuild ? 'Rebuild the story map and write again?' : 'Write the whole script again?';
    const message = rebuild
      ? `${reason} Rebuilding replaces the current map and its edits, then replaces the narration.`
      : map
        ? 'The narration is written again from this story map and research. Your script edits are replaced.'
        : 'A story map is built from the research first, then the narration is written. Your script edits are replaced.';
    if (!(await this.confirm.ask({ title, message, confirm: rebuild ? 'Rebuild and write' : 'Write again', danger: true }))) return;
    this.queue(this.api.redraft(this.id, rebuild), rebuild ? 'Story-map rebuild and redraft queued' : 'Redraft queued');
  }

  // --- story map --------------------------------------------------------------------

  protected editStoryMap(): void {
    const map = this.script()?.story_map;
    if (!map) return;
    this.storyMapDraft.set(cloneStoryMap(map));
  }

  protected cancelStoryMapEdit(): void {
    this.storyMapDraft.set(null);
  }

  protected saveStoryMap(): void {
    const draft = this.storyMapDraft();
    if (!draft) return;
    this.storyMapSaving.set(true);
    this.api.saveStoryMap(this.id, draft).subscribe({
      next: (script) => {
        this.storyMapSaving.set(false);
        this.storyMapDraft.set(null);
        this.script.set(script);
        this.toasts.ok('Story map saved');
      },
      error: (err) => {
        this.storyMapSaving.set(false);
        this.toasts.error(errorMessage(err));
      },
    });
  }

  protected async rebuildStoryMap(): Promise<void> {
    const script = this.script();
    if (!script?.research) {
      this.toasts.error('Research the script before building its story map');
      return;
    }
    const replacing = !!script.story_map;
    if (replacing && !(await this.confirm.ask({
      title: 'Rebuild the story map from research?',
      message: 'The current map and any edits to it are replaced. The narration is not changed until you redraft it.',
      confirm: 'Rebuild map', danger: true,
    }))) return;
    this.storyMapDraft.set(null);
    this.queue(this.api.rebuildStoryMap(this.id, replacing), replacing ? 'Story-map rebuild queued' : 'Story map queued');
  }

  protected moveStoryBeat(index: number, by: number): void {
    const to = index + by;
    this.storyMapDraft.update((map) => {
      if (!map || to < 0 || to >= map.beats.length) return map;
      const beats = map.beats.map((beat) => ({ ...beat }));
      [beats[index], beats[to]] = [beats[to], beats[index]];
      return { ...map, beats };
    });
  }

  protected addStoryBeat(after: number): void {
    this.storyMapDraft.update((map) => {
      if (!map) return map;
      const used = new Set(map.beats.map((beat) => beat.id));
      let number = map.beats.length + 1;
      while (used.has(`beat_${number}`)) number += 1;
      const beat: StoryMapBeat = { id: `beat_${number}`, purpose: 'evidence', summary: '', source_numbers: [],
        opens: [], resolves: [], visual_event: '' };
      return { ...map, beats: [...map.beats.slice(0, after + 1), beat, ...map.beats.slice(after + 1)] };
    });
  }

  protected async removeStoryBeat(index: number): Promise<void> {
    const beat = this.storyMapDraft()?.beats[index];
    if (!beat || !(await this.confirm.ask({
      title: `Remove beat ${index + 1}?`,
      message: `“${beat.summary || beat.id}” leaves the map. Open loops must still resolve before it can be saved.`,
      confirm: 'Remove', danger: true,
    }))) return;
    this.storyMapDraft.update((map) => map ? { ...map, beats: map.beats.filter((_, i) => i !== index) } : map);
  }

  protected setBeatList(index: number, field: 'opens' | 'resolves', value: string): void {
    const items = parseTextList(value);
    this.storyMapDraft.update((map) => map ? {
      ...map, beats: map.beats.map((beat, i) => i === index ? { ...beat, [field]: items } : beat),
    } : map);
  }

  protected setStorySources(where: 'opening' | 'ending', value: string): void {
    const source_numbers = parseNumbers(value);
    this.storyMapDraft.update((map) => map ? { ...map, [where]: { ...map[where], source_numbers } } : map);
  }

  protected setBeatSources(index: number, value: string): void {
    const source_numbers = parseNumbers(value);
    this.storyMapDraft.update((map) => map ? {
      ...map, beats: map.beats.map((beat, i) => i === index ? { ...beat, source_numbers } : beat),
    } : map);
  }

  protected mapTemplateLabel(value: StoryTemplate): string {
    return this.storyTemplates.find((option) => option.value === value)?.label ?? value;
  }

  protected loopSummary(map: StoryMap | StoryMapEdit): string {
    const opened = new Set(map.beats.flatMap((beat) => beat.opens));
    const resolved = new Set(map.beats.flatMap((beat) => beat.resolves));
    if (!opened.size) return 'No named loops';
    const count = [...opened].filter((loop) => resolved.has(loop)).length;
    return count === opened.size ? `All ${opened.size} ${opened.size === 1 ? 'loop resolves' : 'loops resolve'}`
      : `${count} of ${opened.size} loops resolve`;
  }

  protected listText(values: (string | number)[]): string {
    return values.join(', ');
  }

  // --- the script as a whole ---------------------------------------------------------

  protected rename(title: string): void {
    const script = this.script();
    if (!script || !title.trim() || title.trim() === script.title) return;
    this.api.updateScript(this.id, { title: title.trim() }).subscribe({
      next: (updated) => this.script.set(updated),
      error: (err) => this.toasts.error(err),
    });
  }

  protected setTarget(value: string): void {
    const minutes = Number(value);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 30) return;
    this.api.updateScript(this.id, { target_minutes: minutes }).subscribe({
      next: (updated) => this.script.set(updated),
      error: (err) => this.toasts.error(err),
    });
  }

  protected setStoryShape(value: StoryShape | ''): void {
    if (value === this.script()?.story_shape) return;
    this.api.updateScript(this.id, { story_shape: value || null }).subscribe({
      next: (updated) => { this.script.set(updated); this.toasts.ok('Story shape saved'); },
      error: (err) => this.toasts.error(errorMessage(err)),
    });
  }

  protected async approve(on: boolean): Promise<void> {
    const script = this.script();
    if (!script) return;
    if (on && this.dirty()) {
      this.toasts.error('Save your edits first');
      return;
    }
    if (on && (!script.claims || !script.checked_current)) {
      if (!(await this.confirm.ask({ title: 'Approve without a fact check?', message: 'The current text has not been fact-checked.',
        confirm: 'Approve anyway' }))) return;
    } else if (on && script.claim_counts?.contradicted) {
      if (!(await this.confirm.ask({ title: `${script.claim_counts.contradicted} claims are contradicted by the sources`,
        message: 'Approve the script anyway?', confirm: 'Approve anyway', danger: true }))) return;
    }
    this.api.updateScript(this.id, { status: on ? 'approved' : 'draft' }).subscribe({
      next: (updated) => {
        this.script.set(updated);
        this.toasts.ok(on ? 'Script approved. Next: record or generate its voice below.' : 'Back to draft');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected saveNotes(notes: string): void {
    if (notes === this.script()?.notes) return;
    this.api.updateScript(this.id, { notes }).subscribe({
      next: (updated) => {
        this.script.set(updated);
        this.toasts.ok('Notes saved');
      },
      error: (err) => this.toasts.error(errorMessage(err)),
    });
  }

  protected async deleteScript(): Promise<void> {
    if (!(await this.confirm.ask({ title: 'Delete this script?', message: 'Its research and fact check go with it.',
      confirm: 'Delete', danger: true }))) return;
    this.api.deleteScript(this.id).subscribe({
      next: () => this.router.navigate(this.script()?.theme_id ? ['/themes', this.script()!.theme_id] : ['/themes']),
      error: (err) => this.toasts.error(err),
    });
  }

  protected textUrl(): string {
    return this.api.scriptTextUrl(this.id);
  }

  // --- display helpers ----------------------------------------------------------------

  protected count(text: string): number {
    return count(text);
  }

  protected seconds(text: string): number {
    return Math.round((count(text) / WORDS_PER_MINUTE) * 60);
  }

  protected rows(text: string): number {
    return Math.max(3, Math.ceil(text.length / 90) + (text.match(/\n/g)?.length ?? 0));
  }
}

function count(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

function strip(s: ScriptSection): ScriptSection {
  return { key: s.key, heading: s.heading.trim(), text: s.text.trim(), short: s.short };
}

function cloneStoryMap(map: StoryMap): StoryMapEdit {
  return {
    template: map.template,
    story_shape: map.story_shape ?? null,
    parallel: map.parallel ? { ...map.parallel, lanes: map.parallel.lanes.map((lane) => ({ ...lane })) } : null,
    opening: { ...map.opening, source_numbers: [...map.opening.source_numbers] },
    beats: map.beats.map((beat) => ({ ...beat, source_numbers: [...beat.source_numbers],
      opens: [...beat.opens], resolves: [...beat.resolves] })),
    ending: { ...map.ending, source_numbers: [...map.ending.source_numbers] },
    data_cards: map.data_cards?.map((card) => ({ ...card, items: card.items.map((item) => ({ ...item })),
      source_numbers: [...card.source_numbers] })) ?? null,
  };
}

function parseTextList(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))];
}

function parseNumbers(value: string): number[] {
  return [...new Set(value.split(',').map((item) => Number(item.trim()))
    .filter((number) => Number.isInteger(number) && number > 0))];
}
