import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../core/api';
import { Confirm } from '../core/confirm';
import { Place } from '../core/models';
import { Toasts } from '../core/toast';

/** The places behind cartoon videos: six drawn by code, the rest drawn by Claude when a video needs them. */
@Component({
  selector: 'app-places-library',
  imports: [FormsModule],
  template: `
    <p class="muted small intro">Backgrounds for the cartoon look. Six come drawn by code: redraw or delete one and Claude draws it like any other (Use code drawing brings it
      back); for anywhere else the planner names
      a place and Claude draws it the first time a cartoon video needs it (about 4 cents), wide for long videos and
      tall for Shorts. Every later video that names it reuses the drawing.</p>
    @if (places(); as list) {
      <div class="places">
        @for (p of list; track p.name) {
          <article class="card place">
            <div class="thumbs">
              @if (p.wide) { <img class="wide" [src]="image(p, 'wide')" [alt]="p.name + ', wide'" loading="lazy" /> }
              @if (p.tall) { <img class="tall" [src]="image(p, 'tall')" [alt]="p.name + ', tall'" loading="lazy" /> }
              @if (!p.wide && !p.tall) { <div class="blank small muted">Not drawn yet</div> }
            </div>
            <div class="name">{{ label(p.name) }}</div>
            <div class="small muted">{{ p.description }}</div>
            <div class="small muted">{{ status(p) }} · used in {{ p.used_in }}
              {{ p.used_in === 1 ? 'storyboard' : 'storyboards' }}</div>
            @if (editing() === p.name) {
              <input type="text" #how placeholder="What to change: warmer light, more books, a fireplace…"
                     (keydown.enter)="redraw(p, shape(), how.value)" />
              <div class="row">
                <select [ngModel]="shape()" (ngModelChange)="shape.set($event)" aria-label="Which drawing">
                  <option value="wide">Wide (long videos)</option><option value="tall">Tall (Shorts)</option>
                </select>
                <button class="sm primary" (click)="redraw(p, shape(), how.value)" [disabled]="drawing() === p.name">
                  {{ drawing() === p.name ? 'Drawing… about 30 s' : 'Draw' }}</button>
                <button class="sm quiet" (click)="editing.set(null)">Cancel</button>
              </div>
            } @else {
              <div class="row">
                <button class="sm" (click)="editing.set(p.name); shape.set(p.wide || !p.tall ? 'wide' : 'tall')"
                        [disabled]="drawing() === p.name">{{ p.wide || p.tall ? 'Redraw' : 'Draw it' }}</button>
                @if (p.built_in && !p.by_code) {
                  <button class="sm quiet" (click)="remove(p)">Use code drawing</button>
                } @else {
                  <button class="sm quiet danger" (click)="remove(p)">Delete</button>
                }
              </div>
            }
          </article>
        }
      </div>
    } @else {
      <div class="skeleton" style="height:160px"></div>
    }
  `,
  styles: `
    .intro { margin: 0 0 14px; }
    .places { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 1fr)); gap: 12px; margin-bottom: 28px; }
    .place { display: grid; gap: 6px; align-content: start; }
    .thumbs { display: flex; gap: 6px; align-items: flex-end; }
    .thumbs img { border-radius: var(--radius-sm); border: 1px solid var(--line); background: var(--sunken); }
    .thumbs .wide { width: 72%; aspect-ratio: 16 / 9; object-fit: cover; flex: 1; }
    .thumbs .tall { width: 24%; aspect-ratio: 9 / 16; object-fit: cover; }
    .blank { width: 100%; aspect-ratio: 16 / 9; display: grid; place-items: center; border-radius: var(--radius-sm);
             border: 1px dashed var(--line-strong); }
    .name { font-weight: 650; }
    input[type=text] { width: 100%; }
  `,
})
export class PlacesLibrary {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  protected readonly places = signal<Place[] | null>(null);
  protected readonly editing = signal<string | null>(null);
  protected readonly drawing = signal<string | null>(null);
  protected readonly shape = signal<'wide' | 'tall'>('wide');
  private version = Date.now();

  constructor() {
    this.load();
  }

  private load(): void {
    this.api.places().subscribe({ next: (list) => this.places.set(list), error: (err) => this.toasts.error(err) });
  }

  protected image(p: Place, shape: 'wide' | 'tall'): string {
    return this.api.placeImageUrl(p.name, shape, `${p.updated_at ?? ''}-${this.version}`, shape === 'wide' ? 480 : 200);
  }

  protected label(name: string): string {
    return name.replace(/_/g, ' ');
  }

  protected status(p: Place): string {
    if (p.by_code) return 'Drawn by code';
    if (p.wide && p.tall) return 'Drawn wide and tall';
    if (p.wide) return 'Drawn wide; tall when a Short needs it';
    if (p.tall) return 'Drawn tall; wide when a long video needs it';
    return 'Drawn when a cartoon video needs it';
  }

  protected redraw(p: Place, shape: 'wide' | 'tall', instruction: string): void {
    this.drawing.set(p.name);
    this.api.redrawPlace(p.name, shape, instruction).subscribe({
      next: (updated) => {
        this.drawing.set(null);
        this.editing.set(null);
        this.version = Date.now();
        this.places.update((list) => list && list.map((x) => (x.name === updated.name ? updated : x)));
        this.toasts.ok(`${this.label(p.name)} drawn. Render a video again to see it.`);
      },
      error: (err) => {
        this.drawing.set(null);
        this.toasts.error(err);
      },
    });
  }

  protected async remove(p: Place): Promise<void> {
    const name = this.label(p.name);
    const back = p.built_in && !p.by_code;
    const named = p.used_in ? `It is named in ${p.used_in} storyboard${p.used_in === 1 ? '' : 's'}. ` : '';
    if (!(await this.confirm.ask(back
      ? { title: `Use the code drawing of ${name}?`, message: "Claude's drawings of it are deleted.",
          confirm: 'Use code drawing' }
      : p.built_in
        ? { title: `Delete the code drawing of ${name}?`, danger: true, confirm: 'Delete',
            message: `${named}Claude draws it the first time a cartoon video needs it, like any other place. `
              + 'Use code drawing brings it back.' }
        : { title: `Delete ${name}?`, danger: true, confirm: 'Delete',
            message: named ? `${named}They show the plain colour background until it is drawn again.`
              : 'Its drawings are deleted.' }))) return;
    this.api.deletePlace(p.name).subscribe({
      next: () => {
        this.version = Date.now();
        if (p.built_in) this.load();
        else this.places.update((list) => list && list.filter((x) => x.name !== p.name));
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
