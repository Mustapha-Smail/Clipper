import { Component, computed, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { APPEARANCES, AppearanceStore } from './core/appearance';
import { ConfirmHost } from './core/confirm';
import { JobsStore } from './core/jobs-store';
import { ThemesStore } from './core/themes-store';
import { Toasts } from './core/toast';
import { Palette } from './shared/palette';

interface NavItem {
  path: string;
  label: string;
  icon: string;       // an SVG path, drawn with a stroke
}

const COLLAPSED_KEY = 'clipper.menu.collapsed';

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ConfirmHost, Palette],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
  host: { '(document:keydown.escape)': 'menuOpen.set(false)' },
})
export class App {
  protected readonly store = inject(JobsStore);
  protected readonly toasts = inject(Toasts);
  protected readonly themes = inject(ThemesStore);
  protected readonly appearance = inject(AppearanceStore);
  protected readonly appearances = APPEARANCES;
  protected readonly currentAppearance = computed(() =>
    APPEARANCES.find((a) => a.value === this.appearance.mode()) ?? APPEARANCES[0]);
  private readonly router = inject(Router);
  protected readonly isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  /** Desktop: the menu shrinks to a rail of icons. Remembered in this browser. */
  protected readonly collapsed = signal(readCollapsed());
  /** Phone: the menu is a drawer over the page. */
  protected readonly menuOpen = signal(false);
  /** The page shown, once the router has read the address (empty before that). */
  private readonly url = signal('');

  protected readonly homeItem: NavItem = {
    path: '/themes', label: 'Themes', icon: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z' };
  /** The tools behind the work: shared drawings and music, and what the AI is told. */
  protected readonly studioItems: NavItem[] = [
    { path: '/props', label: 'Library', icon: 'M4 4h4v16H4zM10 4h4v16h-4zM16 5l3.5 1 -3 14-3.5-1z' },
    { path: '/prompts', label: 'Prompts', icon: 'M4 6h16M4 12h10M4 18h7M17 14l3 3-3 3' },
  ];
  protected readonly clippingItems: NavItem[] = [
    { path: '/', label: 'Videos', icon: 'M3 6h13v12H3zM16 10l5-3v10l-5-3' },
    { path: '/campaigns', label: 'Campaigns', icon: 'M4 10v4h3l7 4V6L7 10zM18 9a4 4 0 0 1 0 6' },
    { path: '/clips', label: 'Review', icon: 'M5 12l4 4 10-10' },
  ];

  /** Which parts of the app are on (Settings, Features). Until the server answers: the Studio only. */
  protected readonly features = computed(() => {
    const f = this.store.status()?.features;
    return { clipping: f?.['clipping'] ?? false, studio: f?.['studio'] ?? true };
  });

  /** Problems that stop the app from working, most serious first. */
  protected readonly problems = computed(() => {
    if (this.store.offline()) return ['The server is not responding. Check that it is running.'];
    const status = this.store.status();
    if (!status) return [];
    const list: string[] = [];
    if (!status.worker_online) list.push('No worker is running, so queued jobs will not start.');
    const uses = status.ai_providers ?? ['anthropic'];
    if (uses.includes('anthropic') && !status.api_key) {
      list.push('No Claude API key: set ANTHROPIC_API_KEY in .env and restart, or use your Claude plan in Settings.');
    } else if (uses.includes('claude_cli') && !status.claude_cli) {
      list.push('Settings use your Claude plan, but the Claude CLI is not installed where the app runs.');
    } else if (uses.includes('codex_cli') && !status.codex_cli) {
      list.push('Settings use your ChatGPT plan, but the Codex CLI is not installed where the app runs.');
    } else if (status.ai && !status.ai.ok) {
      list.push(`The AI is unavailable: ${status.ai.message} Until it is back, clips are picked and checked by `
        + 'plain rules (if Settings say so) and pasted campaign listings wait to be read.');
    }
    if (!status.tools['ffmpeg']) list.push('ffmpeg is not installed on the server.');
    if (this.features().clipping && !status.tools['yt-dlp']) list.push('yt-dlp is not installed on the server.');
    return list;
  });

  constructor() {
    this.store.start();
    this.themes.load();
    this.router.events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe((e) => {
      this.menuOpen.set(false);
      this.url.set(e.urlAfterRedirects);
      this.themes.load();       // counts change as you work: keep the menu current
    });
    effect(() => {
      // with Clipping off, its home page is not the place to land (deep links are left alone)
      const clipping = this.features().clipping;
      if (!clipping && this.store.status() && this.url().split('?')[0] === '/') this.router.navigateByUrl('/themes');
    });
  }

  protected toggleCollapsed(): void {
    const next = !this.collapsed();
    this.collapsed.set(next);
    try {
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0');
    } catch {
      // private browsing: the choice lasts for this visit only
    }
  }
}
