import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { PublishCheck } from '../core/models';

/**
 * Before you publish: a checklist, your input on record, how close the video is to your
 * recent ones, and advice on YouTube's "made for kids" and "altered content" questions.
 * It advises; nothing here approves or posts anything.
 */
@Component({
  selector: 'app-publish-check',
  imports: [RouterLink],
  template: `
    <section class="card before">
      <div class="row spread">
        <h3>Before you publish</h3>
        <a class="btn sm quiet" [href]="logUrl()" download>Download the authorship log</a>
      </div>
      @if (check(); as c) {
        <ul class="checklist">
          @for (item of c.checklist; track item.text) {
            <li [class.done]="item.done === true" [class.todo]="item.done === false">
              <span class="tick" aria-hidden="true">{{ item.done === true ? '✓' : item.done === false ? '!' : '·' }}</span>
              <span>{{ item.text }}</span>
            </li>
          }
        </ul>

        <div class="grid">
          <div class="part">
            <h4>Your part <span class="muted small">{{ c.authorship.yours }} of {{ c.authorship.parts }}</span></h4>
            <ul class="lines">
              @for (l of c.authorship.lines; track $index) {
                <li><span class="who" [class.you]="l.yours">{{ l.yours ? 'you' : 'AI' }}</span>
                  <span><strong>{{ l.part }}</strong> {{ l.text }}</span></li>
              }
            </ul>
            @for (a of c.authorship.advice; track a) { <p class="small advice">{{ a }}</p> }
            @if (c.authorship.events.length) {
              <details>
                <summary class="small">Every step ({{ c.authorship.events.length }})</summary>
                <ol class="events small">
                  @for (e of c.authorship.events; track $index) {
                    <li><span class="muted">{{ when(e.at) }}</span> <span class="who" [class.you]="e.by === 'you'">{{ e.by === 'you' ? 'you' : e.by === 'ai' ? 'AI' : 'app' }}</span> {{ e.summary }}</li>
                  }
                </ol>
              </details>
            }
          </div>

          <div class="part">
            <h4>Like your recent videos?
              @if (c.similarity.verdict !== 'first') {
                <span class="pill" [class.approved]="c.similarity.verdict === 'different'"
                      [class.failed]="c.similarity.verdict === 'close'">{{ c.similarity.score }}/100</span>
              }
            </h4>
            <p class="small">{{ c.similarity.note }}</p>
            @if (c.similarity.nearest; as n) {
              <p class="small muted">Closest: <a [routerLink]="['/storyboards', n.id]">{{ n.title }}</a></p>
            }
            @if (c.similarity.lines.length) {
              <ul class="points small">
                @for (l of c.similarity.lines; track l.text) { <li><span class="num">+{{ l.points }}</span>{{ l.text }}</li> }
              </ul>
            }
            @if (c.similarity.variety) { <p class="small muted">{{ c.similarity.variety }}</p> }
          </div>

          <div class="part">
            <h4>Made for kids?</h4>
            <p class="small">{{ c.kids.advice }}</p>
            <ul class="points small">
              @for (l of c.kids.lines; track l.text) {
                <li><span class="num">{{ (l.points ?? 0) > 0 ? '+' : '' }}{{ l.points }}</span>{{ l.text }}</li>
              }
            </ul>
            <p class="small muted">{{ c.kids.note }}</p>
          </div>

          <div class="part">
            <h4>Altered or synthetic content label</h4>
            <p class="small">{{ c.disclosure.advice }}</p>
            <ul class="points small">
              @for (l of c.disclosure.lines; track l.text) {
                <li><span class="num">{{ l.needs === true ? 'yes' : l.needs === null ? '?' : 'no' }}</span>{{ l.text }}</li>
              }
            </ul>
          </div>
        </div>
        <p class="small muted">Advice from plain rules, every point shown. The decisions in YouTube Studio are yours.</p>
      } @else if (failed()) {
        <p class="small muted">Could not load the checks. Reload the page to try again.</p>
      } @else {
        <p class="small muted">Checking…</p>
      }
    </section>
  `,
  styles: `
    .before { display: grid; gap: 14px; }
    h3, h4 { margin: 0; }
    h4 { display: flex; align-items: center; gap: 8px; font-size: 14px; }
    .checklist { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
    .checklist li { display: flex; gap: 10px; align-items: baseline; }
    .tick {
      flex: none; width: 20px; height: 20px; border-radius: 99px; display: inline-grid; place-items: center;
      font-size: 12px; font-weight: 700; background: var(--sunken); color: var(--muted);
    }
    .done .tick { background: var(--good-soft); color: var(--good); }
    .todo .tick { background: var(--warn-soft); color: var(--warn); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); gap: 14px; }
    .part { display: grid; gap: 8px; align-content: start; padding: 12px; border: 1px solid var(--line); border-radius: var(--radius-sm); }
    .part p { margin: 0; }
    .lines, .points, .events { margin: 0; padding: 0; list-style: none; display: grid; gap: 4px; }
    .lines li { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
    .points li { display: flex; gap: 8px; }
    .points .num { flex: none; min-width: 30px; text-align: right; font-weight: 650; }
    .who {
      flex: none; font-size: 11px; font-weight: 650; padding: 0 6px; border-radius: 99px;
      background: var(--sunken); color: var(--muted);
    }
    .who.you { background: var(--accent); color: var(--accent-ink); }
    .advice { color: var(--text); background: var(--sunken); padding: 6px 8px; border-radius: 6px; }
    .events { margin-top: 6px; max-height: 240px; overflow: auto; }
    details summary { cursor: pointer; }
  `,
})
export class PublishCheckView {
  private readonly api = inject(Api);

  readonly storyboardId = input.required<number>();
  /** Changes whenever the storyboard does, so the checks are fetched again. */
  readonly version = input<unknown>(0);

  protected readonly check = signal<PublishCheck | null>(null);
  protected readonly failed = signal(false);

  constructor() {
    effect(() => {
      const id = this.storyboardId();
      this.version();
      untracked(() => this.api.publishCheck(id).subscribe({
        next: (c) => {
          this.check.set(c);
          this.failed.set(false);
        },
        error: () => this.failed.set(true),
      }));
    });
  }

  protected logUrl(): string {
    return this.api.authorshipUrl(this.storyboardId());
  }

  protected when(at: string): string {
    return new Date(at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
}
