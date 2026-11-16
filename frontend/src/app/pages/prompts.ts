import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Api } from '../core/api';
import { Confirm } from '../core/confirm';
import { PromptInfo } from '../core/models';
import { Toasts } from '../core/toast';

interface DiffLine {
  kind: 'same' | 'add' | 'del';
  text: string;
}

/** Line diff (longest common subsequence), enough for prompts of a few hundred lines. */
function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  const n = a.length;
  const m = b.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: 'del', text: a[i++] });
    } else {
      out.push({ kind: 'add', text: b[j++] });
    }
  }
  while (i < n) out.push({ kind: 'del', text: a[i++] });
  while (j < m) out.push({ kind: 'add', text: b[j++] });
  return out;
}

/** What the AI is told at every step: read it, change it, go back to the default. */
@Component({
  selector: 'app-prompts-page',
  imports: [FormsModule],
  template: `
    <div class="page wide">
      <div class="page-head">
        <div>
          <span class="eyebrow">Workshop</span>
          <h1>Prompts</h1>
          <p class="muted">The instructions each AI step receives. Edits apply from the next call; the default stays one click away.</p>
        </div>
        @if (edited().length) { <span class="pill review">{{ edited().length }} edited</span> }
      </div>

      @if (all(); as list) {
        <div class="layout" [class.reading]="current() !== null">
          <aside class="list">
            <input type="search" placeholder="Filter prompts" [ngModel]="filter()" (ngModelChange)="filter.set($event)"
                   aria-label="Filter prompts" />
            @for (group of groups(); track group.step) {
              <h2>{{ group.step }}</h2>
              @for (p of group.items; track p.name) {
                <button class="entry" [class.on]="current()?.name === p.name" (click)="pick(p.name)">
                  <span class="grow">{{ p.title }}</span>
                  @if (p.overridden) { <span class="mark" title="Edited">●</span> }
                </button>
              }
            } @empty {
              <p class="small muted">No prompt matches.</p>
            }
          </aside>

          <section class="editor">
            @if (current(); as p) {
              <button class="quiet sm back-list" (click)="pick(null)">← All prompts</button>
              <div class="card">
                <div class="row spread">
                  <div class="grow">
                    <span class="eyebrow">{{ p.step }}</span>
                    <h3>{{ p.title }}</h3>
                    <p class="small muted">{{ p.about }}</p>
                  </div>
                  @if (p.overridden) { <span class="pill review">Edited</span> } @else { <span class="pill">Default</span> }
                </div>
                <dl class="meta small">
                  @if (p.task) { <div><dt>Step</dt><dd><code>{{ p.task }}</code>@if (p.tier) { · {{ p.tier }} tier }</dd></div> }
                  <div><dt>Used in</dt><dd><code>{{ p.used_in }}</code></dd></div>

                  @if (p.includes.length) {
                    <div><dt>Includes</dt>
                      <dd class="chips">@for (inc of p.includes; track inc) {
                        <button class="chip link" (click)="pick(inc)">{{ titleOf(inc) }}</button> }</dd></div>
                  }
                </dl>

                @if (p.variables.length) {
                  <div class="vars">
                    <div class="small vars-head"><strong>Values the code fills in</strong>
                      <span class="muted">Each must stay in the text. Click one to put it where the cursor is.</span></div>
                    <ul>
                      @for (v of p.variables; track v) {
                        <li [class.missing]="!draft().includes('{' + v + '}')">
                          <button class="var" (click)="insert(v)" [title]="'Insert {' + v + '} at the cursor'">{{ '{' + v + '}' }}</button>
                          <span class="small">{{ p.variable_help[v] || 'Filled in by the code.' }}</span>
                          <span class="state small">{{ draft().includes('{' + v + '}') ? '✓ in the text' : 'missing' }}</span>
                        </li>
                      }
                    </ul>
                  </div>
                }

                <div class="tabs" role="tablist">
                  <button role="tab" [class.on]="mode() === 'edit'" (click)="mode.set('edit')">Edit</button>
                  <button role="tab" [class.on]="mode() === 'diff'" (click)="mode.set('diff')" [disabled]="!changed() && !p.overridden">
                    Changes from default</button>
                </div>

                @if (mode() === 'edit') {
                  <textarea #editor class="text" spellcheck="false" [ngModel]="draft()" (ngModelChange)="draft.set($event)"
                            aria-label="Prompt text"></textarea>
                } @else {
                  <pre class="diff">@for (line of diff(); track $index) {<span [class]="line.kind">{{ line.kind === 'add' ? '+ ' : line.kind === 'del' ? '− ' : '  ' }}{{ line.text }}
</span>}</pre>
                }

                @for (problem of issues(); track problem) { <div class="notice bad small">{{ problem }}</div> }

                <div class="row">
                  <button class="primary" (click)="save()" [disabled]="!changed() || saving() || issues().length > 0">
                    {{ saving() ? 'Saving…' : 'Save' }}</button>
                  @if (changed()) { <button class="quiet" (click)="draft.set(p.text)">Discard changes</button> }
                  <span class="grow"></span>
                  @if (p.overridden) { <button class="quiet danger" (click)="reset()">Reset to default</button> }
                </div>
                <p class="small muted">Values in braces are filled in by the code: you can move them, not rename or invent
                  them. <code>{{ '{' }}prompt:other{{ '}' }}</code> includes another prompt. Any other brace is plain text.</p>
              </div>
            } @else {
              <div class="empty pick">Choose a prompt to read it.</div>
            }
          </section>
        </div>
      } @else {
        <div class="skeleton" style="height:400px"></div>
      }
    </div>
  `,
  styles: `
    .page.wide { max-width: 1280px; }
    .layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 20px; align-items: start; }
    .list {
      position: sticky; top: 20px; max-height: calc(100vh - 40px); overflow-y: auto; display: grid; gap: 2px;
      padding-right: 4px;
      input { margin-bottom: 6px; }
      h2 { margin: 16px 8px 4px; }
    }
    .entry {
      justify-content: flex-start; text-align: left; white-space: normal; border-color: transparent; background: none;
      box-shadow: none; font-weight: 500; color: var(--muted); padding: 7px 10px;
      &:hover:not(:disabled) { background: var(--sunken); color: var(--text); border-color: transparent; }
      &.on { background: var(--surface); color: var(--text); border-color: var(--line); box-shadow: var(--shadow-sm); font-weight: 600; }
    }
    .mark { color: var(--accent); font-size: 10px; }
    .editor .card { display: grid; gap: 14px; }
    .meta {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 0;
      dt { color: var(--muted); font-size: 12px; } dd { margin: 2px 0 0; }
    }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .chip { padding: 1px 8px; border-radius: 6px; background: var(--sunken); font-size: 12.5px; }
    .chip.link { border: 0; box-shadow: none; font-weight: 600; min-height: 0; }
    .vars {
      display: grid; gap: 8px; padding: 12px 14px; border-radius: var(--radius-sm); background: var(--bg);
      border: 1px solid var(--line);
      .vars-head { display: grid; gap: 2px; }
      ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
      li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: baseline; }
      .var { font: 600 12.5px var(--mono); padding: 2px 8px; min-height: 0; background: var(--accent-soft);
        border-color: transparent; box-shadow: none; }
      .state { color: var(--good); white-space: nowrap; }
      li.missing .state { color: var(--bad); font-weight: 650; }
      li.missing .var { background: var(--bad-soft); color: var(--bad); }
    }
    @media (max-width: 600px) { .vars li { grid-template-columns: auto minmax(0, 1fr); .state { grid-column: 2; } } }
    .text {
      font: 13px/1.6 var(--mono); min-height: 52vh; resize: vertical; white-space: pre-wrap; tab-size: 2;
    }
    .diff {
      font: 12.5px/1.6 var(--mono); margin: 0; padding: 12px; border-radius: var(--radius-sm); background: var(--sunken);
      max-height: 60vh; overflow: auto; white-space: pre-wrap; word-break: break-word;
      .add { background: var(--good-soft); color: var(--good); display: block; }
      .del { background: var(--bad-soft); color: var(--bad); display: block; text-decoration: line-through; }
      .same { display: block; color: var(--muted); }
    }
    .back-list { display: none; margin-bottom: 8px; }
    .pick { padding: 80px 24px; }
    code { font-family: var(--mono); font-size: 12.5px; }
    @media (max-width: 860px) {
      .layout { grid-template-columns: 1fr; }
      .layout.reading .list { display: none; }
      .layout:not(.reading) .editor { display: none; }
      .list { position: static; max-height: none; }
      .back-list { display: inline-flex; }
    }
  `,
})
export class PromptsPage {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly all = signal<PromptInfo[] | null>(null);
  protected readonly filter = signal('');
  protected readonly selected = signal<string | null>(this.route.snapshot.queryParamMap.get('p'));
  protected readonly draft = signal('');
  protected readonly mode = signal<'edit' | 'diff'>('edit');
  protected readonly saving = signal(false);
  private readonly editor = viewChild<ElementRef<HTMLTextAreaElement>>('editor');
  protected readonly includable = computed(() => new Set((this.all() ?? []).map((p) => p.name)));

  /** Why the text cannot be saved as it is: the same rules as the server, checked as you type. */
  protected readonly issues = computed(() => {
    const p = this.current();
    if (!p || !this.changed()) return [];
    const text = this.draft();
    const found: string[] = [];
    if (!text.trim()) found.push('A prompt cannot be empty: use Reset to go back to the default.');
    for (const v of p.variables) {
      if (!text.includes(`{${v}}`)) found.push(`{${v}} is missing: the code fills it in, and without it the model will not see that part.`);
    }
    for (const m of new Set([...text.matchAll(/\{([a-z][a-z0-9_]*)\}/g)].map((x) => x[1]))) {
      if (!p.variables.includes(m)) {
        found.push(`{${m}} is not a value the code fills in for this prompt` +
          (p.variables.length ? ` (available: ${p.variables.map((v) => `{${v}}`).join(', ')}).` : ': it has none.'));
      }
    }
    for (const m of new Set([...text.matchAll(/\{prompt:([a-z][a-z0-9_]*)\}/g)].map((x) => x[1]))) {
      if (!this.includable().has(m)) found.push(`{prompt:${m}} names no prompt.`);
      else if (m === p.name) found.push('A prompt cannot include itself.');
    }
    return found;
  });

  /** Put a value where the cursor is, so it is always spelt right. */
  protected insert(variable: string): void {
    const box = this.editor()?.nativeElement;
    const text = this.draft();
    const at = box ? box.selectionStart : text.length;
    const end = box ? box.selectionEnd : text.length;
    const token = `{${variable}}`;
    this.draft.set(text.slice(0, at) + token + text.slice(end));
    if (box) {
      setTimeout(() => {
        box.focus();
        box.setSelectionRange(at + token.length, at + token.length);
      });
    }
  }

  protected readonly current = computed(() => (this.all() ?? []).find((p) => p.name === this.selected()) ?? null);
  protected readonly changed = computed(() => !!this.current() && this.draft() !== this.current()!.text);
  protected readonly edited = computed(() => (this.all() ?? []).filter((p) => p.overridden));
  protected readonly diff = computed(() => (this.current() ? diffLines(this.current()!.default, this.draft()) : []));
  protected readonly groups = computed(() => {
    const words = this.filter().toLowerCase().split(/\s+/).filter(Boolean);
    const groups: { step: string; items: PromptInfo[] }[] = [];
    for (const p of this.all() ?? []) {
      const hay = `${p.title} ${p.about} ${p.step} ${p.task} ${p.name}`.toLowerCase();
      if (!words.every((w) => hay.includes(w))) continue;
      let group = groups.find((g) => g.step === p.step);
      if (!group) groups.push((group = { step: p.step, items: [] }));
      group.items.push(p);
    }
    return groups;
  });

  constructor() {
    this.api.prompts().subscribe({
      next: (list) => {
        this.all.set(list);
        const wide = window.matchMedia('(min-width: 861px)').matches;
        if (!this.selected() && wide && list.length) this.selected.set(list[0].name);
        this.draft.set(this.current()?.text ?? '');
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected titleOf(name: string): string {
    return (this.all() ?? []).find((p) => p.name === name)?.title ?? name;
  }

  protected async pick(name: string | null): Promise<void> {
    if (this.changed() && !(await this.confirm.ask({ title: 'Leave without saving?',
      message: 'Your changes to this prompt are not saved.', confirm: 'Leave' }))) return;
    this.selected.set(name);
    this.mode.set('edit');
    this.draft.set(this.current()?.text ?? '');
    this.router.navigate([], { queryParams: { p: name }, replaceUrl: true });
  }

  private put(prompt: PromptInfo): void {
    this.all.update((list) => (list ?? []).map((p) => (p.name === prompt.name ? prompt : p)));
    this.draft.set(prompt.text);
  }

  protected save(): void {
    const p = this.current();
    if (!p) return;
    this.saving.set(true);
    this.api.savePrompt(p.name, this.draft()).subscribe({
      next: (saved) => {
        this.saving.set(false);
        this.put(saved);
        this.toasts.ok(saved.problems.length ? 'Saved, with a warning below' : 'Saved: used from the next AI call');
      },
      error: (err) => {
        this.saving.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected async reset(): Promise<void> {
    const p = this.current();
    if (!p) return;
    if (!(await this.confirm.ask({ title: `Reset “${p.title}”?`, message: 'Your version is deleted and the default is used again.',
      confirm: 'Reset', danger: true }))) return;
    this.api.resetPrompt(p.name).subscribe({
      next: (fresh) => {
        this.put(fresh);
        this.toasts.ok('Back to the default');
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
