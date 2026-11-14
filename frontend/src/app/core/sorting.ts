import { effect, signal, WritableSignal } from '@angular/core';

/** One way to order a list: its label in the menu and how two items compare. */
export interface SortOption<T> {
  value: string;
  label: string;
  compare: (a: T, b: T) => number;
}

/** The chosen order, remembered in this browser (a per-viewer convenience: the list works without it). */
export function rememberedSort<T>(key: string, options: SortOption<T>[]): WritableSignal<string> {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(key);
  } catch {
    saved = null;
  }
  const chosen = signal(options.some((o) => o.value === saved) ? saved! : options[0].value);
  effect(() => {
    const value = chosen();
    try {
      localStorage.setItem(key, value);
    } catch {
      // private window or blocked storage: the order is simply not remembered
    }
  });
  return chosen;
}

/** A copy of the list in the chosen order; ties keep their original order. */
export function sortBy<T>(list: T[], options: SortOption<T>[], value: string): T[] {
  const option = options.find((o) => o.value === value) ?? options[0];
  return list.map((item, i) => ({ item, i }))
    .sort((a, b) => option.compare(a.item, b.item) || a.i - b.i)
    .map((x) => x.item);
}

/** Dates from the API; a missing date goes last whichever the direction. */
export function byDate(a: string | null | undefined, b: string | null | undefined, newestFirst = true): number {
  if (!a || !b) return a ? -1 : b ? 1 : 0;
  const diff = Date.parse(a) - Date.parse(b);
  return newestFirst ? -diff : diff;
}

export function byText(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true });
}
