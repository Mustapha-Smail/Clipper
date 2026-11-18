import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Api } from '../core/api';
import { Confirm } from '../core/confirm';
import { AgoPipe } from '../core/format';
import { YoutubeStatus } from '../core/models';
import { Toasts } from '../core/toast';

/** The "Connect YouTube" card: sign in once with Google so Learning can read watch time and retention. */
@Component({
  selector: 'app-youtube-connect',
  imports: [AgoPipe],
  template: `
    <section class="card" id="studio-youtube">
      <div class="head">
        <h3>Connect YouTube</h3>
        @if (s(); as st) {
          <span class="pill" [class.approved]="st.connected" [class.rejected]="st.needs_reconnect">
            {{ st.connected ? 'Connected' : st.needs_reconnect ? 'Reconnect needed' : 'Not connected' }}</span>
        }
      </div>
      <p class="small muted">Reads your own channel's watch time and retention from YouTube Analytics, once a day,
        for Learning. Read-only: the app never uploads or changes anything on YouTube.</p>

      @if (s(); as st) {
        @if (st.connected || st.needs_reconnect) {
          <div class="channel">
            <span class="avatar" aria-hidden="true">{{ (st.channel_title || '?').charAt(0) }}</span>
            <div class="who">
              <strong>{{ st.channel_title || 'Your channel' }}</strong>
              <small class="muted">
                @if (st.connected_at) { Connected {{ st.connected_at | ago }} }
                · @if (st.last_fetch) { last read {{ st.last_fetch | ago }}, {{ st.videos_with_numbers }}
                  {{ st.videos_with_numbers === 1 ? 'video' : 'videos' }} with numbers } @else { not read yet }
              </small>
            </div>
          </div>
        }

        @if (st.problem) {
          <div class="notice" [class.bad]="st.needs_reconnect" role="status">{{ st.problem }}</div>
        }

        @if (!st.configured) {
          <div class="notice" role="status">
            <span>Add <code>YOUTUBE_OAUTH_CLIENT_ID</code> and <code>YOUTUBE_OAUTH_CLIENT_SECRET</code> to <code>.env</code>
              and restart. The README's "Connect YouTube" section shows how to create them.</span>
          </div>
        }

        <div class="actions">
          @if (st.connected) {
            <button class="danger" (click)="disconnect()" [disabled]="busy()">Disconnect</button>
          } @else {
            <button class="primary" (click)="connect()" [disabled]="busy() || !st.configured">
              {{ busy() ? 'Opening Google…' : st.needs_reconnect ? 'Reconnect' : 'Connect YouTube' }}</button>
          }
        </div>

        <details class="fold">
          <summary class="muted small">What it reads, and what it cannot</summary>
          <ul class="small">
            <li>Per video: views, minutes watched, average view duration and average percentage viewed.</li>
            <li>The retention curve, so Learning can show how many people are still watching at the first twist and
              the payoff.</li>
            <li>Not available through Google's API: impressions, thumbnail click-through rate and the Shorts
              "viewed vs swiped away" share. They stay in YouTube Studio.</li>
            <li>YouTube's numbers arrive 2 to 3 days after publishing.</li>
            <li>{{ st.testing_note }}</li>
            <li>Google sends you back to <code>{{ st.redirect_uri }}</code>: the client in Google Cloud must list this exact address.</li>
          </ul>
        </details>
      } @else {
        <div class="skeleton line"></div>
      }
    </section>
  `,
  styles: `
    section { scroll-margin-top: 20px; }
    section > p { margin: 4px 0 16px; }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
      h3 { margin: 0; } }
    .channel { display: flex; align-items: center; gap: 12px; margin: 4px 0 12px; min-width: 0; }
    .avatar { flex: none; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center;
      background: var(--accent-soft); color: var(--text); font-weight: 700; font-size: 17px; }
    .who { display: grid; min-width: 0;
      strong, small { overflow: hidden; text-overflow: ellipsis; } strong { white-space: nowrap; } }
    .notice { margin-bottom: 12px; overflow-wrap: anywhere; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
    code { overflow-wrap: anywhere; }
    ul { margin: 8px 0 0; padding-left: 18px; display: grid; gap: 4px; }
    .skeleton.line { height: 44px; }
    @media (max-width: 520px) { .actions button { flex: 1; justify-content: center; } }
  `,
})
export class YoutubeConnect {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly router = inject(Router);

  protected readonly s = signal<YoutubeStatus | null>(null);
  protected readonly busy = signal(false);

  constructor() {
    const result = inject(ActivatedRoute).snapshot.queryParamMap.get('youtube');
    this.load(() => {
      if (result === 'connected') this.toasts.ok(`YouTube connected: ${this.s()?.channel_title ?? 'your channel'}`);
      // the callback's result is shown once, then dropped from the address
      // and the card is brought into view once the router has scrolled for that navigation
      if (result) {
        this.router.navigate([], { queryParams: { youtube: null }, queryParamsHandling: 'merge', replaceUrl: true, preserveFragment: true })
          .then(() => setTimeout(() => document.getElementById('studio-youtube')?.scrollIntoView({ block: 'start' }), 50));
      }
    });
  }

  private load(then?: () => void): void {
    this.api.youtubeStatus().subscribe({
      next: (st) => {
        this.s.set(st);
        then?.();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected connect(): void {
    this.busy.set(true);
    this.api.youtubeConnect().subscribe({
      next: ({ url }) => window.location.assign(url),
      error: (err) => {
        this.busy.set(false);
        this.toasts.error(err);
      },
    });
  }

  protected async disconnect(): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Disconnect YouTube?',
      message: 'The app asks Google to revoke its access and deletes the saved token. Numbers already read stay; connect again at any time.',
      confirm: 'Disconnect', danger: true,
    });
    if (!ok) return;
    this.busy.set(true);
    this.api.youtubeDisconnect().subscribe({
      next: (st) => {
        this.busy.set(false);
        this.s.set(st);
        this.toasts.ok(st.revoked ? 'Disconnected and revoked at Google' : 'Disconnected. Google did not confirm the revoke: remove the app at myaccount.google.com/permissions');
      },
      error: (err) => {
        this.busy.set(false);
        this.toasts.error(err);
      },
    });
  }
}
