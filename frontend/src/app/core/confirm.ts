import { Component, ElementRef, Injectable, effect, inject, signal, viewChild } from '@angular/core';

export interface ConfirmOptions {
  title: string;
  /** One or two sentences: what happens, and whether it can be undone. */
  message?: string;
  confirm?: string;
  cancel?: string;
  /** Destructive: the confirm button is red. */
  danger?: boolean;
}

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

/** Asks before something that matters, in a dialog that matches the app, instead of the browser's confirm(). */
@Injectable({ providedIn: 'root' })
export class Confirm {
  readonly current = signal<Pending | null>(null);

  ask(options: ConfirmOptions): Promise<boolean> {
    this.current()?.resolve(false);
    return new Promise((resolve) => this.current.set({ ...options, resolve }));
  }

  answer(ok: boolean): void {
    const pending = this.current();
    if (!pending) return;
    this.current.set(null);
    pending.resolve(ok);
  }
}

@Component({
  selector: 'app-confirm-host',
  template: `
    <dialog #box class="confirm" (cancel)="$event.preventDefault(); confirm.answer(false)"
            (click)="backdrop($event)" aria-labelledby="confirm-title">
      @if (confirm.current(); as c) {
        <form method="dialog" (submit)="$event.preventDefault(); confirm.answer(true)">
          <h2 id="confirm-title">{{ c.title }}</h2>
          @if (c.message) { <p>{{ c.message }}</p> }
          <div class="actions">
            <button type="button" class="quiet" (click)="confirm.answer(false)">{{ c.cancel ?? 'Cancel' }}</button>
            <button type="submit" [class.primary]="!c.danger" [class.destroy]="c.danger" autofocus>
              {{ c.confirm ?? 'Continue' }}</button>
          </div>
        </form>
      }
    </dialog>
  `,
  styles: `
    .confirm {
      width: min(440px, calc(100vw - 32px));
      padding: 0;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--surface);
      color: var(--text);
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
      &::backdrop {
        background: rgba(15, 15, 14, 0.45);
        backdrop-filter: blur(2px);
      }
      &[open] { animation: rise 0.14s ease-out; }
    }
    form {
      display: grid;
      gap: 10px;
      padding: 22px 22px 18px;
    }
    h2 {
      margin: 0;
      font-size: 17px;
      line-height: 1.35;
      color: var(--text);
      text-transform: none;
      letter-spacing: normal;
    }
    p {
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }
    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 8px;
    }
    .destroy {
      background: var(--bad);
      color: #fff;
      border-color: transparent;
      &:hover:not(:disabled) { background: var(--bad); filter: brightness(0.92); }
    }
    @keyframes rise {
      from { opacity: 0; transform: translateY(6px) scale(0.98); }
      to { opacity: 1; transform: none; }
    }
  `,
})
export class ConfirmHost {
  protected readonly confirm = inject(Confirm);
  private readonly box = viewChild.required<ElementRef<HTMLDialogElement>>('box');

  constructor() {
    effect(() => {
      const open = this.confirm.current() !== null;
      const dialog = this.box().nativeElement;
      if (open && !dialog.open) dialog.showModal();
      if (!open && dialog.open) dialog.close();
    });
  }

  /** A click on the dimmed backdrop (outside the box) cancels. */
  protected backdrop(event: MouseEvent): void {
    const rect = this.box().nativeElement.getBoundingClientRect();
    const inside = event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.top && event.clientY <= rect.bottom;
    if (!inside) this.confirm.answer(false);
  }
}
