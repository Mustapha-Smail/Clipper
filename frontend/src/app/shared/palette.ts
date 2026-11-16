import { Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { Router } from '@angular/router';
import { Api } from '../core/api';
import { ScriptSummary } from '../core/models';
import { ThemesStore } from '../core/themes-store';

interface Entry {
  label: string;
  hint: string;
  kind: 'Page' | 'Theme' | 'Script' | 'Action';
  url: string;
  hue?: string;
}

const PAGES: Entry[] = [
  { label: 'Themes', hint: 'Every channel and what to do next', kind: 'Page', url: '/themes' },
  { label: 'Library', hint: 'Props, places and music', kind: 'Page', url: '/props' },
  { label: 'Prompts', hint: 'What the AI is told at every step', kind: 'Page', url: '/prompts' },
  { label: 'Activity', hint: 'Jobs and their logs', kind: 'Page', url: '/activity' },
  { label: 'Settings', hint: 'Models, voice, render, posting', kind: 'Page', url: '/settings' },
  { label: 'New theme', hint: 'Start a channel theme', kind: 'Action', url: '/themes?new=1' },
];

/** ⌘K: jump to any page, theme or script by typing a few letters. */
@Component({
  selector: 'app-palette',
  host: {
    '(document:keydown)': 'onKey($event)',
  },
  template: `
    @if (open()) {
      <div class="veil" (click)="close()" aria-hidden="true"></div>
      <div class="palette" role="dialog" aria-modal="true" aria-label="Go to">
        <div class="search">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></svg>
          <input #box type="search" placeholder="Go to a theme, a script or a page…" [value]="query()"
                 (input)="query.set($any($event.target).value); index.set(0)" aria-label="Search"
                 role="combobox" aria-controls="palette-list" [attr.aria-activedescendant]="'pal-' + index()" />
          <kbd>esc</kbd>
        </div>
        <ul id="palette-list" role="listbox">
          @for (e of results(); track e.kind + e.url; let i = $index) {
            <li [id]="'pal-' + i" role="option" [attr.aria-selected]="i === index()" [class.on]="i === index()"
                (mouseenter)="index.set(i)" (click)="go(e)">
              @if (e.hue) {
                <span class="swatch" [class]="'swatch hue-' + e.hue"></span>
              } @else {
                <span class="glyph" aria-hidden="true">{{ e.kind === 'Script' ? '¶' : e.kind === 'Action' ? '+' : '→' }}</span>
              }
              <span class="text"><strong>{{ e.label }}</strong><small>{{ e.hint }}</small></span>
              <span class="kind">{{ e.kind }}</span>
            </li>
          } @empty {
            <li class="none">Nothing matches “{{ query() }}”.</li>
          }
        </ul>
      </div>
    }
  `,
  styles: `
    .veil {
      position: fixed;
      inset: 0;
      z-index: 90;
      background: rgba(10, 9, 6, 0.38);
      backdrop-filter: blur(3px);
      animation: fade var(--fast) ease-out;
    }
    .palette {
      position: fixed;
      z-index: 91;
      top: min(14vh, 120px);
      left: 50%;
      width: min(620px, calc(100vw - 32px));
      transform: translateX(-50%);
      background: var(--raised);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow-lg);
      overflow: hidden;
      animation: drop var(--med) var(--ease);
    }
    .search {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      svg { width: 18px; height: 18px; fill: none; stroke: var(--muted); stroke-width: 2; stroke-linecap: round; }
      input {
        border: 0;
        box-shadow: none;
        background: none;
        padding: 6px 0;
        font-size: 16px;
        &:focus { box-shadow: none; }
      }
    }
    kbd {
      font: 600 11px var(--sans);
      color: var(--muted);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 6px;
    }
    ul {
      list-style: none;
      margin: 0;
      padding: 6px;
      max-height: min(60vh, 440px);
      overflow-y: auto;
    }
    li {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 9px 10px;
      border-radius: 10px;
      cursor: pointer;
      &.on { background: var(--sunken); }
      &.none { color: var(--muted); cursor: default; justify-content: center; padding: 24px; }
    }
    .glyph {
      width: 22px;
      height: 22px;
      flex: none;
      display: grid;
      place-items: center;
      border-radius: 6px;
      background: var(--sunken);
      color: var(--muted);
      font-weight: 700;
      font-size: 13px;
    }
    li.on .glyph { background: var(--surface); }
    .swatch { margin: 0 6px; }
    .text {
      display: grid;
      min-width: 0;
      flex: 1;
      strong, small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      small { color: var(--muted); font-size: 12.5px; }
    }
    .kind { font-size: 11.5px; color: var(--faint); font-weight: 600; }
    @keyframes fade { from { opacity: 0; } }
    @keyframes drop { from { opacity: 0; transform: translate(-50%, -8px) scale(0.98); } }
  `,
})
export class Palette {
  private readonly router = inject(Router);
  private readonly api = inject(Api);
  private readonly themes = inject(ThemesStore);
  private readonly box = viewChild<ElementRef<HTMLInputElement>>('box');

  readonly open = signal(false);
  protected readonly query = signal('');
  protected readonly index = signal(0);
  private readonly scripts = signal<ScriptSummary[]>([]);

  protected readonly results = computed(() => {
    const words = this.query().toLowerCase().split(/\s+/).filter(Boolean);
    const themes: Entry[] = (this.themes.all() ?? []).map((t) => ({
      label: t.name, kind: 'Theme', url: `/themes/${t.id}`, hue: t.color,
      hint: t.status === 'active' ? t.next.text : t.status === 'paused' ? 'Paused' : 'Archived',
    }));
    const scripts: Entry[] = this.scripts().map((s) => ({
      label: s.title, kind: 'Script', url: `/scripts/${s.id}`,
      hint: `${s.status === 'approved' ? 'Approved' : 'Draft'} · ${s.video === 'short' ? 'Short' : `${s.minutes} min`}`,
    }));
    const all = [...themes, ...PAGES, ...scripts];
    if (!words.length) return [...themes.slice(0, 6), ...PAGES].slice(0, 14);
    return all.filter((e) => words.every((w) => `${e.label} ${e.hint} ${e.kind}`.toLowerCase().includes(w))).slice(0, 30);
  });

  constructor() {
    effect(() => {
      if (this.open()) setTimeout(() => this.box()?.nativeElement.focus());
    });
  }

  show(): void {
    this.query.set('');
    this.index.set(0);
    this.open.set(true);
    if (!this.themes.all()) this.themes.load();
    this.api.scripts().subscribe({ next: (list) => this.scripts.set(list), error: () => undefined });
  }

  protected close(): void {
    this.open.set(false);
  }

  protected go(entry: Entry): void {
    this.close();
    this.router.navigateByUrl(entry.url);
  }

  protected onKey(event: KeyboardEvent): void {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (this.open()) this.close();
      else this.show();
      return;
    }
    if (!this.open()) return;
    const count = this.results().length;
    if (event.key === 'Escape') {
      event.preventDefault();
      this.close();
    } else if (event.key === 'ArrowDown' && count) {
      event.preventDefault();
      this.index.set((this.index() + 1) % count);
    } else if (event.key === 'ArrowUp' && count) {
      event.preventDefault();
      this.index.set((this.index() - 1 + count) % count);
    } else if (event.key === 'Enter') {
      const entry = this.results()[this.index()];
      if (entry) {
        event.preventDefault();
        this.go(entry);
      }
    }
  }
}
