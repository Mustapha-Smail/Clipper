import { Component, computed, effect, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ConfirmHost } from './core/confirm';
import { JobsStore } from './core/jobs-store';
import { Toasts } from './core/toast';

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
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ConfirmHost],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
  host: { '(document:keydown.escape)': 'menuOpen.set(false)' },
})
export class App {
  protected readonly store = inject(JobsStore);
  protected readonly toasts = inject(Toasts);
  private readonly router = inject(Router);

  /** Desktop: the menu shrinks to a rail of icons. Remembered in this browser. */
  protected readonly collapsed = signal(readCollapsed());
  /** Phone: the menu is a drawer over the page. */
  protected readonly menuOpen = signal(false);
  /** The page shown, once the router has read the address (empty before that). */
  private readonly url = signal('');

  protected readonly studioItems: NavItem[] = [
    { path: '/ideas', label: 'Ideas', icon: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z' },
    { path: '/scripts', label: 'Scripts', icon: 'M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5' },
    { path: '/animations', label: 'Animations', icon: 'M4 5h16v14H4zM10 9l5 3-5 3z' },
    { path: '/props', label: 'Library', icon: 'M4 4h4v16H4zM10 4h4v16h-4zM16 5l3.5 1 -3 14-3.5-1z' },
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
    if (!status.api_key) list.push('No Claude API key: set ANTHROPIC_API_KEY in .env and restart.');
    if (!status.tools['ffmpeg']) list.push('ffmpeg is not installed on the server.');
    if (this.features().clipping && !status.tools['yt-dlp']) list.push('yt-dlp is not installed on the server.');
    return list;
  });

  constructor() {
    this.store.start();
    this.router.events.pipe(filter((e) => e instanceof NavigationEnd)).subscribe((e) => {
      this.menuOpen.set(false);
      this.url.set(e.urlAfterRedirects);
    });
    effect(() => {
      // with Clipping off, its home page is not the place to land (deep links are left alone)
      const clipping = this.features().clipping;
      if (!clipping && this.store.status() && this.url().split('?')[0] === '/') this.router.navigateByUrl('/ideas');
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
