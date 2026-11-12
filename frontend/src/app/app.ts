import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ConfirmHost } from './core/confirm';
import { JobsStore } from './core/jobs-store';
import { Toasts } from './core/toast';

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ConfirmHost],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  protected readonly store = inject(JobsStore);
  protected readonly toasts = inject(Toasts);

  /** Problems that stop the app from working, most serious first. */
  protected readonly problems = computed(() => {
    if (this.store.offline()) return ['The server is not responding. Check that it is running.'];
    const status = this.store.status();
    if (!status) return [];
    const list: string[] = [];
    if (!status.worker_online) list.push('No worker is running, so queued jobs will not start.');
    if (!status.api_key) list.push('No Claude API key: set ANTHROPIC_API_KEY in .env and restart.');
    if (!status.tools['ffmpeg']) list.push('ffmpeg is not installed on the server.');
    if (!status.tools['yt-dlp']) list.push('yt-dlp is not installed on the server.');
    return list;
  });

  constructor() {
    this.store.start();
  }
}
