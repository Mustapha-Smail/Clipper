import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { Api } from './api';
import { JobsStore } from './jobs-store';
import { Post, PostStatus } from './models';

const POST_JOBS = new Set(['post', 'post_sync']);

/** On its way to being posted: handed to Buffer and not taken back. */
export const WAITING = new Set<PostStatus>(['queued', 'draft', 'scheduled', 'sending']);
export const SERVICE_NAMES: Record<string, string> = { instagram: 'Instagram', tiktok: 'TikTok', youtube: 'YouTube' };

/** Posts that move a Short from Approved into the Published phase: waiting, or already posted. */
export function handedOver(posts: Post[] | undefined): Post[] {
  return (posts ?? []).filter((p) => WAITING.has(p.status) || p.status === 'sent');
}

/** The earliest time a waiting post goes out; null when none has a time (queue not placed yet, drafts). */
export function nextDue(posts: Post[] | undefined): string | null {
  const times = (posts ?? []).filter((p) => WAITING.has(p.status) && p.status !== 'draft' && p.due_at)
    .map((p) => p.due_at!).sort();
  return times[0] ?? null;
}

export function lastSent(posts: Post[] | undefined): string | null {
  const times = (posts ?? []).filter((p) => p.status === 'sent' && p.sent_at).map((p) => p.sent_at!).sort();
  return times.at(-1) ?? null;
}

/** The viewer's calendar day of an instant, "YYYY-MM-DD". */
export function dayKeyOf(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** "Mon 17 Nov, 18:07" in the viewer's time. */
export function shortTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/** One line for a card: where it goes and when. */
export function postLine(posts: Post[] | undefined): string {
  const live = handedOver(posts);
  if (!live.length) return '';
  const names = (list: Post[]) => [...new Set(list.map((p) => SERVICE_NAMES[p.service] ?? p.service))].join(', ');
  const waiting = live.filter((p) => WAITING.has(p.status));
  const sent = live.filter((p) => p.status === 'sent');
  const parts: string[] = [];
  if (waiting.length) {
    const due = nextDue(waiting);
    const drafts = waiting.every((p) => p.status === 'draft');
    parts.push(`${drafts ? 'Draft in Buffer' : 'Scheduled'}: ${names(waiting)}${due ? ` · ${shortTime(due)}` : ''}`);
  }
  if (sent.length) parts.push(`Posted: ${names(sent)}`);
  return parts.join(' · ');
}

/** Every scheduled post, shared by the pages that show them. Reloads when a posting job ends. */
@Injectable({ providedIn: 'root' })
export class PostsStore {
  private readonly api = inject(Api);
  private readonly jobs = inject(JobsStore);

  readonly posts = signal<Post[]>([]);
  private loadedAt = 0;

  /** Posts of each clip and each Studio video, newest first. */
  readonly byClip = computed(() => this.group((p) => p.clip_id));
  readonly byBoard = computed(() => this.group((p) => p.storyboard_id));

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

  /** Show a post the server just created or changed, without waiting for the next read. */
  replace(post: Post): void {
    this.posts.update((all) => all.some((p) => p.id === post.id) ? all.map((p) => (p.id === post.id ? post : p)) : [post, ...all]);
  }

  private group(key: (p: Post) => number | null): Map<number, Post[]> {
    const map = new Map<number, Post[]>();
    for (const post of this.posts()) {
      const id = key(post);
      if (id !== null) map.set(id, [...(map.get(id) ?? []), post]);
    }
    return map;
  }
}
