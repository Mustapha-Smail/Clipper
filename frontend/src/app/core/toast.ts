import { Injectable, signal } from '@angular/core';
import { errorMessage } from './api';

export interface Toast {
  id: number;
  text: string;
  kind: 'ok' | 'error';
}

@Injectable({ providedIn: 'root' })
export class Toasts {
  readonly items = signal<Toast[]>([]);
  private next = 1;

  ok(text: string): void {
    this.push(text, 'ok', 2800);
  }

  error(err: unknown): void {
    this.push(typeof err === 'string' ? err : errorMessage(err), 'error', 7000);
  }

  dismiss(id: number): void {
    this.items.update((items) => items.filter((t) => t.id !== id));
  }

  private push(text: string, kind: Toast['kind'], ms: number): void {
    const id = this.next++;
    this.items.update((items) => [...items.slice(-3), { id, text, kind }]);
    setTimeout(() => this.dismiss(id), ms);
  }
}
