import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { Asset, MusicFound, MusicTrack } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { CAST, EXPRESSIONS, POSES } from './storyboard';
import { PlacesLibrary } from '../shared/places-library';

/** The prop library and the cast: everything the animated videos are drawn with. */
@Component({
  selector: 'app-props-page',
  imports: [FormsModule, AgoPipe, PlacesLibrary],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Library</h1>
          <p class="muted">What your animated videos are made of: props drawn once in the house style and reused, the
            cast, and background music. Claude draws new props when a storyboard needs them; redraw any you do not like.</p>
        </div>
        <input type="search" placeholder="Find a prop" [ngModel]="query()" (ngModelChange)="query.set($event); page.set(0)" aria-label="Find a prop" />
      </div>

      @if (assets(); as list) {
        <div class="grid">
          @for (a of pageItems(); track a.name) {
            <article class="card prop">
              <img [src]="image(a)" [alt]="a.name" loading="lazy" />
              <div class="name">{{ a.name }}</div>
              <div class="small muted">{{ a.description }}</div>
              <div class="small muted">{{ a.drawn ? 'drawn ' + (a.updated_at | ago) : 'not drawn yet' }} ·
                used in {{ a.used_in }} {{ a.used_in === 1 ? 'storyboard' : 'storyboards' }}</div>
              @if (editing() === a.name) {
                <input type="text" #how placeholder="What to change: rounder, add a cord, more yellow…" (keydown.enter)="redraw(a, how.value)" />
                <div class="row">
                  <button class="sm primary" (click)="redraw(a, how.value)" [disabled]="drawing() === a.name">
                    {{ drawing() === a.name ? 'Drawing…' : 'Redraw' }}</button>
                  <button class="sm quiet" (click)="editing.set(null)">Cancel</button>
                </div>
              } @else {
                <div class="row">
                  <button class="sm" (click)="a.drawn ? editing.set(a.name) : redraw(a, '')" [disabled]="drawing() === a.name">
                    {{ drawing() === a.name ? 'Drawing…' : a.drawn ? 'Redraw' : 'Draw it' }}</button>
                  <button class="sm quiet danger" (click)="remove(a)">Delete</button>
                </div>
              }
            </article>
          } @empty {
            <div class="empty">{{ list.length ? 'No prop matches.' : 'No props yet. They appear when you make your first storyboard.' }}</div>
          }
        </div>
        @if (pages() > 1) {
          <div class="pager">
            <button class="sm" (click)="page.set(page() - 1)" [disabled]="page() === 0">Previous</button>
            <span class="small muted num">Page {{ page() + 1 }} of {{ pages() }} · {{ shown().length }} props</span>
            <button class="sm" (click)="page.set(page() + 1)" [disabled]="page() >= pages() - 1">Next</button>
          </div>
        }
      } @else {
        <div class="skeleton" style="height:200px"></div>
      }

      <app-places-library />

      <h2>Music</h2>
      <p class="muted small">Background tracks, looped under the voice and lowered while it speaks. Upload only music you
        have the rights to use on YouTube, for example from the YouTube Audio Library (YouTube Studio, Audio Library).</p>
      <div class="row" style="margin-bottom:10px">
        <button (click)="musicPicker.click()" [disabled]="uploading()">{{ uploading() ? 'Uploading…' : 'Upload a track' }}</button>
        <input #musicPicker type="file" accept="audio/*,.mp3,.m4a,.flac,.ogg" hidden (change)="uploadMusic(musicPicker)" />
      </div>
      <details class="fold card find" #finder>
        <summary>Find free music online</summary>
        <p class="small muted">Searches Openverse (Jamendo, Freesound, ccMixter and more) for tracks that are safe in a
          monetised video: CC0, public domain, or CC BY (credit the creator; the credit line is kept for your
          description). The YouTube Audio Library has no way in from outside: download from YouTube Studio and upload.</p>
        <div class="row">
          <input type="search" class="grow" #q placeholder="calm piano, upbeat ukulele, lo-fi…" (keydown.enter)="search(q.value, len.value)" />
          <select #len aria-label="Length">
            <option value="">Any length</option><option value="medium">Medium</option><option value="long">Long</option>
          </select>
          <button (click)="search(q.value, len.value)" [disabled]="searching()">{{ searching() ? 'Searching…' : 'Search' }}</button>
        </div>
        @if (found(); as f) {
          <div class="stack results">
            @for (r of f; track r.id) {
              <div class="track found">
                <div class="grow">
                  <strong>{{ r.title }}</strong> <span class="small muted">by {{ r.creator }} · {{ minutes(r.duration) }} · {{ r.source }}</span>
                  <div class="small"><span class="pill tiny">{{ r.license === 'by' ? 'CC BY ' + r.license_version + ' · credit needed' : r.license === 'cc0' ? 'CC0' : 'Public domain' }}</span>
                    @if (r.genres.length) { <span class="muted"> {{ r.genres.join(', ') }}</span> }
                    · <a [href]="r.landing_url" target="_blank" rel="noopener">source</a></div>
                </div>
                <audio controls preload="none" [src]="r.audio_url"></audio>
                <button class="sm" (click)="importTrack(r)" [disabled]="importing() === r.id">{{ importing() === r.id ? 'Adding…' : 'Add' }}</button>
              </div>
            } @empty {
              <p class="small muted">Nothing found. Try broader words: "piano", "ambient", "acoustic".</p>
            }
          </div>
        }
      </details>

      <div class="stack music">
        @for (m of tracks(); track m.id) {
          <div class="card track">
            <div class="grow">
              <strong>{{ m.name }}</strong>
              @if (m.license) {
                <div class="small muted">{{ m.creator }} · {{ m.license === 'by' ? 'CC BY, credit added to your videos' : m.license === 'cc0' ? 'CC0' : 'Public domain' }}</div>
              }
            </div>
            <span class="small muted num">{{ minutes(m.duration) }}</span>
            <audio controls preload="none" [src]="musicUrl(m.id)"></audio>
            <button class="sm quiet danger" (click)="removeMusic(m)">Delete</button>
          </div>
        } @empty {
          <p class="small muted">No music yet.</p>
        }
      </div>

      <h2>The cast</h2>
      <p class="muted small">Drawn by code: any member, in any pose, with any expression. Mouths move with the voice when a
        character talks.</p>
      <div class="cast">
        @for (c of cast; track c) {
          <figure><img [src]="castImage(c, 'stand', 'happy')" [alt]="c" loading="lazy" /><figcaption class="small">{{ c }}</figcaption></figure>
        }
      </div>
      <div class="cast">
        @for (p of poses; track p) {
          <figure><img [src]="castImage('narrator', p, 'neutral')" [alt]="p" loading="lazy" /><figcaption class="small">{{ p.replace('_', ' ') }}</figcaption></figure>
        }
      </div>
      <div class="cast">
        @for (e of expressions; track e) {
          <figure><img [src]="castImage('narrator', 'stand', e)" [alt]="e" loading="lazy" /><figcaption class="small">{{ e }}</figcaption></figure>
        }
      </div>
    </div>
  `,
  styles: `
    .page-head input { max-width: 220px; }
    .pager { display: flex; gap: 12px; align-items: center; justify-content: center; margin: 14px 0 6px; }
    .find { margin-bottom: 12px; display: grid; gap: 10px; }
    .find select { width: auto; }
    .results { margin-top: 10px; }
    .found { border: 1px solid var(--line); border-radius: var(--radius-sm); }
    .pill.tiny { font-size: 11px; padding: 0 7px; }
    .track {
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 10px 14px;
      audio { height: 34px; }
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
      gap: 12px;
    }
    .prop {
      display: grid;
      gap: 6px;
      align-content: start;
      padding: 12px;
      img {
        width: 100%;
        aspect-ratio: 1;
        object-fit: contain;
        background: #fbf7ee;
        border-radius: 6px;
      }
      .name { font-weight: 650; }
    }
    .cast {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
      figure {
        margin: 0;
        padding: 8px;
        background: #fbf7ee;
        border-radius: 8px;
        text-align: center;
        color: #1b1b1b;
      }
      img { height: 130px; }
    }
  `,
})
export class PropsPage {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);

  protected readonly cast = CAST;
  protected readonly poses = POSES;
  protected readonly expressions = EXPRESSIONS;
  protected readonly assets = signal<Asset[] | null>(null);
  protected readonly query = signal('');
  protected readonly editing = signal<string | null>(null);
  protected readonly drawing = signal<string | null>(null);
  protected readonly tracks = signal<MusicTrack[]>([]);
  protected readonly uploading = signal(false);
  protected readonly found = signal<MusicFound[] | null>(null);
  protected readonly searching = signal(false);
  protected readonly importing = signal<string | null>(null);

  protected search(query: string, length: string): void {
    if (query.trim().length < 2) return;
    this.searching.set(true);
    this.api.searchMusic(query.trim(), length).subscribe({
      next: (result) => {
        this.searching.set(false);
        this.found.set(result.results);
      },
      error: (err) => {
        this.searching.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected importTrack(item: MusicFound): void {
    this.importing.set(item.id);
    this.api.importMusic(item.id).subscribe({
      next: (track) => {
        this.importing.set(null);
        this.tracks.update((list) => [...list.filter((t) => t.id !== track.id), track].sort((a, b) => a.name.localeCompare(b.name)));
        this.toasts.ok(`Added "${track.name}"${track.license === 'by' ? '. Its credit line appears on videos that use it.' : ''}`);
      },
      error: (err) => {
        this.importing.set(null);
        this.toasts.error(err);
      },
    });
  }
  protected readonly page = signal(0);
  private readonly perPage = 24;
  protected readonly pages = computed(() => Math.max(1, Math.ceil(this.shown().length / this.perPage)));
  protected readonly pageItems = computed(() => {
    const page = Math.min(this.page(), this.pages() - 1);
    return this.shown().slice(page * this.perPage, (page + 1) * this.perPage);
  });
  protected readonly shown = computed(() => {
    const q = this.query().trim().toLowerCase();
    return (this.assets() ?? []).filter((a) => !q || a.name.includes(q) || a.description.toLowerCase().includes(q));
  });

  constructor() {
    this.load();
    this.api.music().subscribe({ next: (list) => this.tracks.set(list), error: () => undefined });
  }

  protected musicUrl(id: number): string {
    return this.api.musicAudioUrl(id);
  }

  protected minutes(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }

  protected uploadMusic(picker: HTMLInputElement): void {
    const file = picker.files?.[0];
    if (!file) return;
    this.uploading.set(true);
    this.api.uploadMusic(file).subscribe({
      next: (track) => {
        this.uploading.set(false);
        picker.value = '';
        this.tracks.update((list) => [...list, track].sort((a, b) => a.name.localeCompare(b.name)));
        this.toasts.ok(`Added "${track.name}"`);
      },
      error: (err) => {
        this.uploading.set(false);
        picker.value = '';
        this.toasts.error(err);
      },
    });
  }

  protected async removeMusic(track: MusicTrack): Promise<void> {
    if (!(await this.confirm.ask({ title: `Delete "${track.name}"?`, message: 'Videos that use it will render without music.',
      confirm: 'Delete', danger: true }))) return;
    this.api.deleteMusic(track.id).subscribe({
      next: () => this.tracks.update((list) => list.filter((t) => t.id !== track.id)),
      error: (err) => this.toasts.error(err),
    });
  }

  private load(): void {
    this.api.assets().subscribe({ next: (list) => this.assets.set(list), error: (err) => this.toasts.error(err) });
  }

  protected image(a: Asset): string {
    return this.api.assetImageUrl(a.name, a.updated_at, 256);
  }

  protected castImage(variant: string, pose: string, expression: string): string {
    return this.api.castUrl(variant, pose, expression, 260);
  }

  protected redraw(a: Asset, instruction: string): void {
    this.drawing.set(a.name);
    this.api.redrawAsset(a.name, instruction.trim()).subscribe({
      next: (updated) => {
        this.drawing.set(null);
        this.editing.set(null);
        this.assets.update((list) => (list ?? []).map((x) => (x.name === updated.name ? updated : x)));
        this.toasts.ok(`Redrawn: ${a.name}. Render again to use it.`);
      },
      error: (err) => {
        this.drawing.set(null);
        this.toasts.error(err);
      },
    });
  }

  protected async remove(a: Asset): Promise<void> {
    const where = a.used_in ? `It is used in ${a.used_in} storyboards and will be drawn again when they render.`
      : 'It is removed from the library.';
    if (!(await this.confirm.ask({ title: `Delete the prop "${a.name}"?`, message: where, confirm: 'Delete', danger: true }))) return;
    this.api.deleteAsset(a.name).subscribe({
      next: () => this.assets.update((list) => (list ?? []).filter((x) => x.name !== a.name)),
      error: (err) => this.toasts.error(err),
    });
  }
}
