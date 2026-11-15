import { Injectable, effect, inject, signal, untracked } from '@angular/core';
import { Api } from './api';
import { JobsStore } from './jobs-store';
import { Post } from './models';

const POST_JOBS = new Set(['post', 'post_sync']);

/** Every scheduled post, shared by the pages that show them. Reloads when a posting job ends. */
@Injectable({ providedIn: 'root' })
export class PostsStore {
  private readonly api = inject(Api);
  private readonly jobs = inject(JobsStore);

  readonly posts = signal<Post[]>([]);
  private loadedAt = 0;

  constructor() {
    effect(() => {
      if (this.jobs.finished().some((j) => POST_JOBS.has(j.kind))) untracked(() => this.load());
    });
  }

  /** Read the posts again. `ifOlderThanMs` skips the request when they were just read. */
  load(ifOlderThanMs = 0): void {
    if (Date.now() - this.loadedAt < ifOlderThanMs) return;
    this.loadedAt = Date.now();
    this.api.posts().subscribe({ next: (posts) => this.posts.set(posts), error: () => undefined });
  }

  replace(post: Post): void {
    this.posts.update((all) => all.map((p) => (p.id === post.id ? post : p)));
  }
}
