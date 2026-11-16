import { Injectable, computed, inject, signal } from '@angular/core';
import { Api } from './api';
import { Theme } from './models';

/** The themes, shared by the menu, the quick switcher and the pages. Reloaded after any change. */
@Injectable({ providedIn: 'root' })
export class ThemesStore {
  private readonly api = inject(Api);
  readonly all = signal<Theme[] | null>(null);
  readonly active = computed(() => (this.all() ?? []).filter((t) => t.status === 'active'));
  private loading = false;

  load(): void {
    if (this.loading) return;
    this.loading = true;
    this.api.themes().subscribe({
      next: (list) => {
        this.loading = false;
        this.all.set(list);
      },
      error: () => (this.loading = false),
    });
  }

  /** Put one theme back after a change, without waiting for the whole list. */
  put(theme: Theme): void {
    this.all.update((list) => {
      const rest = (list ?? []).filter((t) => t.id !== theme.id);
      return [theme, ...rest];
    });
  }

  byId(id: number): Theme | undefined {
    return (this.all() ?? []).find((t) => t.id === id);
  }

  /** What waits for you in a theme: ideas to sort and videos ready to go out. */
  attention(theme: Theme): number {
    return theme.inbox + theme.counts.ready;
  }
}
