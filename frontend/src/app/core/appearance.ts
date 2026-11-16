import { Injectable, effect, signal } from '@angular/core';

export type Appearance = 'system' | 'light' | 'dark';
export const APPEARANCES: { value: Appearance; label: string; icon: string }[] = [
  { value: 'system', label: 'System', icon: 'M4 5h16v11H4zM9 20h6M12 16v4' },
  { value: 'light', label: 'Light', icon: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4' },
  { value: 'dark', label: 'Dark', icon: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z' },
];
const KEY = 'clipper.appearance';
const BAR = { light: '#f6f5f1', dark: '#0e0e0c' };

function saved(): Appearance {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

/**
 * Light, dark, or whatever the system says (the default). Kept in this browser: it is how
 * this screen looks, not a setting of the project. index.html applies it before the app starts.
 */
@Injectable({ providedIn: 'root' })
export class AppearanceStore {
  readonly mode = signal<Appearance>(saved());
  private readonly media = window.matchMedia('(prefers-color-scheme: dark)');

  constructor() {
    effect(() => this.apply(this.mode()));
    this.media.addEventListener('change', () => this.apply(this.mode()));
  }

  set(mode: Appearance): void {
    this.mode.set(mode);
    try {
      if (mode === 'system') localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch {
      // private window: the choice lasts for this visit
    }
  }

  /** The next one, for the one-button switch in the folded menu. */
  cycle(): void {
    const order: Appearance[] = ['system', 'light', 'dark'];
    this.set(order[(order.indexOf(this.mode()) + 1) % order.length]);
  }

  private apply(mode: Appearance): void {
    const root = document.documentElement;
    if (mode === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);
    const dark = mode === 'dark' || (mode === 'system' && this.media.matches);
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? BAR.dark : BAR.light);
  }
}
