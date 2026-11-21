import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { Asset, MusicFound, MusicTrack, SoundCandidate, SoundKind, StudioSound } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';
import { byDate, byText, rememberedSort, sortBy, SortOption } from '../core/sorting';
import { CAST, EXPRESSIONS, POSES } from './storyboard';
import { PlacesLibrary } from '../shared/places-library';

const LIBRARY_TABS = ['props', 'places', 'music', 'sounds', 'cast'] as const;
type LibraryTab = (typeof LIBRARY_TABS)[number];

/** The library, one kind per tab: props, places, music and the cast. */
@Component({
  selector: 'app-props-page',
  imports: [FormsModule, AgoPipe, PlacesLibrary],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Library</h1>
          <p class="muted">What your animated videos are made of, each drawn or chosen once and reused by every video.</p>
          </div>
        </div>

      <div class="tabs page-tabs" role="tablist">
        @for (t of tabs; track t.value) {
          <button role="tab" [class.on]="tab() === t.value" [attr.aria-selected]="tab() === t.value" (click)="setTab(t.value)">
            {{ t.label }}@if (tabCount(t.value) !== null) { <span class="muted num">{{ tabCount(t.value) }}</span> }
          </button>
        }
      </div>

      @switch (tab()) {
      @case ('props') {
      <div class="row tab-head">
        <p class="muted small grow">Single objects drawn once in the house style. Claude draws new props when a storyboard
          needs them; redraw any you do not like.</p>
        <div class="row list-tools">
          <input type="search" class="find-prop" placeholder="Find a prop" [ngModel]="query()" (ngModelChange)="query.set($event); page.set(0)" aria-label="Find a prop" />
          <select class="sort" [ngModel]="sort()" (ngModelChange)="sort.set($event); page.set(0)" aria-label="Sort props">
            @for (o of sorts; track o.value) { <option [value]="o.value">{{ o.label }}</option> }
          </select>
        </div>
      </div>

      @if (assets(); as list) {
        <div class="grid">
          @for (a of pageItems(); track a.name) {
            <article class="card prop">
              <img [src]="image(a)" [alt]="a.name" loading="lazy" />
              <div class="name">{{ a.name }}</div>
              <div class="small muted desc" [title]="a.description">{{ a.description }}</div>
              <div class="small muted">{{ a.drawn ? 'drawn ' + (a.updated_at | ago) : 'not drawn yet' }} ·
                added {{ a.created_at | ago }} · used in {{ a.used_in }} {{ a.used_in === 1 ? 'storyboard' : 'storyboards' }}</div>
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

      }
      @case ('places') {
      <app-places-library />
      }
      @case ('music') {
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

      }
      @case ('sounds') {
      <section class="sounds-library" aria-labelledby="sounds-heading">
        <div class="row tab-head">
          <div class="grow">
            <h2 id="sounds-heading">Sound effects</h2>
            <p class="muted small">Search Openverse first, or Wikimedia Commons as a fallback. Only CC0 and CC BY sounds
              can be imported. Every sound is downloaded and normalized locally, then needs your approval before a render can use it.</p>
          </div>
          @if (soundLoading()) { <span class="small muted" role="status">Loading library…</span> }
        </div>
        <div class="card sound-search">
          <div class="row">
            <label class="field grow"><span>Search</span>
              <input type="search" #soundQuery placeholder="pop, whoosh, pencil, impact…"
                     (keydown.enter)="searchSounds(soundQuery.value)" aria-describedby="sound-search-help" />
            </label>
            <label class="field"><span>Provider</span>
              <select [ngModel]="soundProvider()" (ngModelChange)="soundProvider.set($event)">
                <option value="openverse">Openverse</option>
                <option value="wikimedia">Wikimedia Commons</option>
              </select>
            </label>
            <label class="field"><span>Kind</span>
              <select [ngModel]="soundKind()" (ngModelChange)="soundKind.set($event)">
                @for (kind of soundKinds; track kind.value) { <option [value]="kind.value">{{ kind.label }}</option> }
              </select>
            </label>
            <button (click)="searchSounds(soundQuery.value)" [disabled]="soundSearching() || soundQuery.value.trim().length < 2">
              {{ soundSearching() ? 'Searching…' : 'Search' }}</button>
          </div>
          <p id="sound-search-help" class="small muted">Search results are metadata until you choose Add. Check the source page and licence before importing.</p>
          @if (soundLoadError(); as error) { <p class="notice bad" role="alert">{{ error }}</p> }
          @if (soundSearchError(); as error) { <p class="notice bad" role="alert">{{ error }}</p> }
        </div>
        @if (soundSearching()) { <div class="skeleton" style="height:80px" role="status" aria-label="Searching sound effects"></div> }
        @if (soundResults(); as results) {
          <div class="stack sound-results" aria-live="polite">
            @for (candidate of results; track candidate.provider + ':' + candidate.provider_id) {
              <article class="card sound-result">
                <div class="grow">
                  <strong>{{ candidate.title }}</strong>
                  <div class="small muted">{{ candidate.creator || 'Creator unknown' }} · {{ soundClock(candidate.duration) }}</div>
                  <div class="small"><span class="pill tiny">{{ candidate.license === 'by' ? 'CC BY ' + candidate.license_version + ' · credit' : 'CC0' }}</span>
                    · <a [href]="candidate.landing_url" target="_blank" rel="noopener">inspect source and licence</a></div>
                </div>
                <audio controls preload="none" [src]="candidate.media_url" [attr.aria-label]="'Preview ' + candidate.title"></audio>
                <button class="sm" (click)="importSound(candidate)" [disabled]="soundImporting() === candidate.provider_id">
                  {{ soundImporting() === candidate.provider_id ? 'Adding…' : 'Add to library' }}</button>
              </article>
            } @empty {
              <p class="small muted">No safe CC0 or CC BY results. Try a broader search.</p>
            }
          </div>
        }
        <div class="sound-library-list">
          <h3>Imported sounds <span class="small muted">({{ sounds().length }})</span></h3>
          @if (!soundLoading() && !sounds().length) { <p class="small muted">No sound effects yet. Search above to add one.</p> }
          @for (sound of sounds(); track sound.id) {
            <article class="card sound-item">
              <div class="grow">
                <strong>{{ sound.name }}</strong>
                <div class="small muted">{{ soundKindLabel(sound.kind) }} · {{ sound.creator || 'Creator unknown' }} · {{ soundClock(sound.duration) }}</div>
                <div class="small"><span class="pill tiny" [class.sound-approved]="sound.approved_at" [class.sound-rejected]="sound.rejected_at">
                  {{ sound.approved_at ? 'Approved' : sound.rejected_at ? 'Rejected' : 'Pending review' }}</span>
                  <span class="muted"> · {{ sound.license === 'by' ? 'CC BY ' + sound.license_version + ' · credit required' : 'CC0' }}</span>
                  @if (!sound.cached) { <span class="muted"> · local file missing</span> }
                </div>
                @if (sound.rejection_note) { <div class="small muted">Reason: {{ sound.rejection_note }}</div> }
                <a class="small" [href]="sound.landing_url" target="_blank" rel="noopener">Source and licence</a>
              </div>
              @if (sound.cached) { <audio controls preload="none" [src]="soundAudio(sound.id)" [attr.aria-label]="'Preview ' + sound.name"></audio> }
              <div class="row sound-actions">
                @if (!sound.approved_at) {
                  <button class="sm primary" (click)="approveSound(sound)" [disabled]="!sound.cached || soundAction() === sound.id">
                    {{ soundAction() === sound.id ? 'Saving…' : 'Approve' }}</button>
                } @else { <span class="small muted">Ready for storyboard cues</span> }
                @if (!sound.rejected_at) {
                  <button class="sm quiet danger" (click)="rejectSound(sound)" [disabled]="soundAction() === sound.id">Reject</button>
                } @else {
                  <button class="sm quiet" (click)="approveSound(sound)" [disabled]="!sound.cached || soundAction() === sound.id">Approve again</button>
                }
              </div>
            </article>
          }
        </div>
      </section>
      }
      @case ('cast') {
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
      }
      }
    </div>
  `,
  styles: `
    .page-tabs { margin: 0 0 16px; }
    .tab-head { margin-bottom: 12px; align-items: flex-end; }
    .tab-head p { margin: 0; }
    .find-prop { max-width: 240px; }
    .sort { width: auto; }
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
      flex-wrap: wrap;
      audio { height: 34px; flex: 1 1 220px; min-width: 0; }
    }
    .sounds-library { display: grid; gap: 14px; }
    .sounds-library h2, .sounds-library h3 { margin: 0; }
    .sound-search { display: grid; gap: 8px; }
    .sound-search .row { align-items: end; }
    .sound-search .field { min-width: 130px; }
    .sound-search .field.grow { min-width: 180px; }
    .sound-search p { margin: 0; }
    .sound-results, .sound-library-list { display: grid; gap: 8px; }
    .sound-library-list h3 { margin-top: 6px; }
    .sound-result, .sound-item { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding: 12px 14px; }
    .sound-result audio, .sound-item audio { height: 34px; flex: 1 1 220px; min-width: 180px; }
    .sound-result strong, .sound-item strong { overflow-wrap: anywhere; }
    .sound-result .small, .sound-item .small { overflow-wrap: anywhere; }
    .sound-actions { flex: 0 0 auto; }
    .pill.sound-approved { background: var(--good-soft); color: var(--good); }
    .pill.sound-rejected { background: var(--bad-soft); color: var(--bad); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(160px, 100%), 1fr));
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
      min-width: 0;
      .name { font-weight: 650; overflow-wrap: anywhere; }
      .desc {
        display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
        overflow-wrap: break-word;
      }
    }
    @media (max-width: 520px) {
      .prop { padding: 10px; .desc { -webkit-line-clamp: 3; } }
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

  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly tabs: { value: LibraryTab; label: string }[] = [
    { value: 'props', label: 'Props' }, { value: 'places', label: 'Places' },
    { value: 'music', label: 'Music' }, { value: 'sounds', label: 'Sound effects' }, { value: 'cast', label: 'Cast' },
  ];
  /** Kept in the address (?tab=music), so a link or a reload opens the same tab. */
  protected readonly tab = signal<LibraryTab>(LIBRARY_TABS.includes(this.route.snapshot.queryParamMap.get('tab') as LibraryTab)
    ? this.route.snapshot.queryParamMap.get('tab') as LibraryTab : 'props');

  protected setTab(tab: LibraryTab): void {
    this.tab.set(tab);
    this.router.navigate([], { queryParams: { tab: tab === 'props' ? null : tab }, replaceUrl: true });
  }

  protected tabCount(tab: LibraryTab): number | null {
    if (tab === 'props') return this.assets()?.length ?? null;
    if (tab === 'music') return this.tracks().length;
    if (tab === 'sounds') return this.sounds().length;
    if (tab === 'cast') return this.cast.length;
    return null;
  }

  protected readonly cast = CAST;
  protected readonly poses = POSES;
  protected readonly expressions = EXPRESSIONS;
  protected readonly assets = signal<Asset[] | null>(null);
  protected readonly query = signal('');
  protected readonly editing = signal<string | null>(null);
  protected readonly drawing = signal<string | null>(null);
  protected readonly tracks = signal<MusicTrack[]>([]);
  protected readonly sounds = signal<StudioSound[]>([]);
  protected readonly soundLoading = signal(true);
  protected readonly soundLoadError = signal('');
  protected readonly soundSearching = signal(false);
  protected readonly soundSearchError = signal('');
  protected readonly soundResults = signal<SoundCandidate[] | null>(null);
  protected readonly soundProvider = signal<'openverse' | 'wikimedia'>('openverse');
  protected readonly soundKind = signal<SoundKind>('pop');
  protected readonly soundImporting = signal<string | null>(null);
  protected readonly soundAction = signal<number | null>(null);
  protected readonly soundKinds: { value: SoundKind; label: string }[] = [
    { value: 'pop', label: 'Pop' }, { value: 'whoosh', label: 'Whoosh' }, { value: 'impact', label: 'Impact' },
    { value: 'scribble', label: 'Scribble' }, { value: 'step', label: 'Step' }, { value: 'riser', label: 'Riser' },
    { value: 'character', label: 'Character' }, { value: 'arrow', label: 'Arrow' }, { value: 'bounce', label: 'Bounce' },
    { value: 'glide', label: 'Glide' },
  ];
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
  protected readonly sorts: SortOption<Asset>[] = [
    { value: 'used', label: 'Most used', compare: (a, b) => b.used_in - a.used_in || byText(a.name, b.name) },
    { value: 'unused', label: 'Least used', compare: (a, b) => a.used_in - b.used_in || byText(a.name, b.name) },
    { value: 'new', label: 'Newest', compare: (a, b) => byDate(a.created_at, b.created_at) },
    { value: 'old', label: 'Oldest', compare: (a, b) => byDate(a.created_at, b.created_at, false) },
    { value: 'drawn', label: 'Recently drawn', compare: (a, b) => Number(b.drawn) - Number(a.drawn) || byDate(a.updated_at, b.updated_at) },
    { value: 'undrawn', label: 'Not drawn first', compare: (a, b) => Number(a.drawn) - Number(b.drawn) || byText(a.name, b.name) },
    { value: 'yours', label: 'Yours first', compare: (a, b) => Number(b.origin === 'you') - Number(a.origin === 'you') || byText(a.name, b.name) },
    { value: 'az', label: 'A to Z', compare: (a, b) => byText(a.name, b.name) },
    { value: 'za', label: 'Z to A', compare: (a, b) => byText(b.name, a.name) },
  ];
  protected readonly sort = rememberedSort('clipper.props.sort', this.sorts);
  protected readonly shown = computed(() => {
    const q = this.query().trim().toLowerCase();
    const found = (this.assets() ?? []).filter((a) => !q || a.name.includes(q) || a.description.toLowerCase().includes(q));
    return sortBy(found, this.sorts, this.sort());
  });

  constructor() {
    this.load();
    this.api.music().subscribe({ next: (list) => this.tracks.set(list), error: () => undefined });
    this.api.sounds().subscribe({ next: (list) => { this.sounds.set(list); this.soundLoading.set(false); },
      error: (err) => { this.soundLoading.set(false); this.soundLoadError.set(this.errorMessage(err, 'Sound library could not be loaded.')); } });
  }

  protected musicUrl(id: number): string {
    return this.api.musicAudioUrl(id);
  }

  protected minutes(seconds: number): string {
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }

  protected soundClock(seconds: number): string {
    if (!seconds) return 'duration unknown';
    return `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  }

  protected soundKindLabel(kind: SoundKind): string {
    return this.soundKinds.find((item) => item.value === kind)?.label ?? kind;
  }

  protected soundAudio(id: number): string {
    return this.api.soundAudioUrl(id);
  }

  protected searchSounds(query: string): void {
    const value = query.trim();
    if (value.length < 2) return;
    this.soundSearching.set(true);
    this.soundSearchError.set('');
    this.api.searchSounds(value, this.soundProvider()).subscribe({
      next: (result) => { this.soundSearching.set(false); this.soundResults.set(result.results as SoundCandidate[]); },
      error: (err) => { this.soundSearching.set(false); this.soundSearchError.set(this.errorMessage(err, 'Sound search failed.')); },
    });
  }

  protected importSound(candidate: SoundCandidate): void {
    this.soundImporting.set(candidate.provider_id);
    this.api.importSound(candidate, this.soundKind()).subscribe({
      next: (sound) => {
        this.soundImporting.set(null);
        this.sounds.update((list) => [...list.filter((item) => item.id !== sound.id), sound].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name)));
        this.toasts.ok(`Added “${sound.name}”. Review it before assigning it to a storyboard.`);
      },
      error: (err) => { this.soundImporting.set(null); this.toasts.error(err); },
    });
  }

  protected approveSound(sound: StudioSound): void {
    this.soundAction.set(sound.id);
    this.api.approveSound(sound.id).subscribe({
      next: (updated) => { this.soundAction.set(null); this.sounds.update((list) => list.map((item) => item.id === updated.id ? updated : item)); this.toasts.ok(`Approved “${sound.name}”`); },
      error: (err) => { this.soundAction.set(null); this.toasts.error(err); },
    });
  }

  protected async rejectSound(sound: StudioSound): Promise<void> {
    if (!(await this.confirm.ask({ title: `Reject “${sound.name}”?`, message: 'It will stay in the library as rejected and cannot be used in a render.', confirm: 'Reject', danger: true }))) return;
    this.soundAction.set(sound.id);
    this.api.rejectSound(sound.id).subscribe({
      next: (updated) => { this.soundAction.set(null); this.sounds.update((list) => list.map((item) => item.id === updated.id ? updated : item)); this.toasts.ok(`Rejected “${sound.name}”`); },
      error: (err) => { this.soundAction.set(null); this.toasts.error(err); },
    });
  }

  private errorMessage(error: unknown, fallback: string): string {
    const value = error as { error?: { detail?: string } } | null;
    return value?.error?.detail || fallback;
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
