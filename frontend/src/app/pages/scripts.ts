import { Component, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { ScriptSummary } from '../core/models';
import { Toasts } from '../core/toast';

/** Every Studio script, newest first. */
@Component({
  selector: 'app-scripts-page',
  imports: [RouterLink, AgoPipe],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Scripts</h1>
          <p class="muted">Narration for your animated videos: researched with sources, written by Claude, edited by you, fact-checked.</p>
        </div>
      </div>

      @if (scripts(); as list) {
        <div class="stack">
          @for (s of list; track s.id) {
            <a class="card item" [routerLink]="['/scripts', s.id]">
              <div class="grow">
                <div class="title">{{ s.title }}</div>
                <div class="small muted">
                  {{ s.format === 'list' ? 'List video' : 'Single topic' }} · target {{ s.target_minutes }} min · updated {{ s.updated_at | ago }}
                </div>
              </div>
              @if (s.job) {
                <span class="pill running">{{ s.job.message }}</span>
              } @else {
                @if (s.claim_counts; as c) {
                  @if (c.contradicted) { <span class="pill failed">{{ c.contradicted }} contradicted</span> }
                  @if (c.unclear) { <span class="pill cancelled">{{ c.unclear }} unclear</span> }
                  @if (!s.checked_current) { <span class="pill">edited since check</span> }
                }
                <span class="pill" [class.approved]="s.status === 'approved'">{{ s.status }}</span>
              }
              <div class="counts small num">
                <div>{{ s.words }} words</div>
                <div class="muted">about {{ s.minutes }} min</div>
              </div>
            </a>
          } @empty {
            <div class="empty">No scripts yet. Approve an idea on the Ideas page, then choose "Write the script".</div>
          }
        </div>
      } @else {
        <div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>
      }
    </div>
  `,
  styles: `
    .item {
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 14px 18px;
      text-decoration: none;
      transition: border-color 0.12s;
    }
    .item:hover {
      border-color: var(--text);
    }
    .title {
      font-weight: 650;
    }
    .counts {
      text-align: right;
      white-space: nowrap;
      min-width: 80px;
    }
    @media (max-width: 600px) {
      .item { flex-wrap: wrap; padding: 12px 14px; }
      .item .grow { flex-basis: 100%; }
      .counts { text-align: left; min-width: 0; display: flex; gap: 8px; margin-left: auto; }
    }
  `,
})
export class ScriptsPage {
  private readonly api = inject(Api);
  private readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);
  protected readonly scripts = signal<ScriptSummary[] | null>(null);

  constructor() {
    // Reload when a script job starts or ends.
    let known = '';
    effect(() => {
      const signature = this.store.jobs().filter((j) => j.script_id).map((j) => `${j.id}:${j.state}`).join();
      if (signature !== known) {
        known = signature;
        untracked(() => this.load());
      }
    });
  }

  private load(): void {
    this.api.scripts().subscribe({ next: (list) => this.scripts.set(list), error: (err) => this.toasts.error(err) });
  }
}
