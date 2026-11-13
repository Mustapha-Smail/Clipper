import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { JobCard } from '../shared/job-card';
import { Campaign, CampaignDecision, CampaignPatch } from '../core/models';
import { Toasts } from '../core/toast';
import { Confirm } from '../core/confirm';

type Tab = 'consider' | 'joined' | 'skipped' | 'closed';

/** Campaigns found on marketplaces, ranked by what a clip is likely to earn. */
@Component({
  selector: 'app-campaigns-page',
  imports: [FormsModule, RouterLink, AgoPipe, JobCard],
  templateUrl: './campaigns.html',
  styleUrl: './campaigns.scss',
})
export class CampaignsPage {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly confirm = inject(Confirm);
  private readonly store = inject(JobsStore);

  protected readonly tabs: { value: Tab; label: string }[] = [
    { value: 'consider', label: 'To consider' },
    { value: 'joined', label: 'Joined' },
    { value: 'skipped', label: 'Skipped' },
    { value: 'closed', label: 'Closed' },
  ];
  protected readonly tab = signal<Tab>('consider');
  protected readonly campaigns = signal<Campaign[] | null>(null);

  protected readonly url = signal('');
  protected readonly text = signal('');
  protected readonly reading = signal(false);

  /** Joined campaigns stay under Joined even after they close; you may still have clips to submit. */
  private bucket(c: Campaign): Tab {
    if (c.decision === 'joined') return 'joined';
    if (c.decision === 'skipped') return 'skipped';
    return c.joinable ? 'consider' : 'closed';
  }

  protected readonly shown = computed(() => (this.campaigns() ?? []).filter((c) => this.bucket(c) === this.tab()));

  protected readonly counts = computed(() => {
    const counts: Record<Tab, number> = { consider: 0, joined: 0, skipped: 0, closed: 0 };
    for (const c of this.campaigns() ?? []) counts[this.bucket(c)]++;
    return counts;
  });

  /** The latest scout job (the job list is newest first). */
  protected readonly scoutJob = computed(() => this.store.jobs().find((j) => j.kind === 'scout') ?? null);
  protected readonly scouting = computed(() => ['queued', 'running'].includes(this.scoutJob()?.state ?? ''));
  /** The job card shows while the scout runs, and after a run started from this page. */
  protected readonly startedHere = signal(false);

  constructor() {
    this.load();
    effect(() => {
      if (this.store.finished().some((j) => j.kind === 'scout')) untracked(() => this.load());
    });
  }

  protected scout(): void {
    this.api.scoutCampaigns().subscribe({
      next: () => {
        this.startedHere.set(true);
        this.store.refresh();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  private load(): void {
    this.api.campaigns().subscribe({ next: (list) => this.campaigns.set(list), error: (err) => this.toasts.error(err) });
  }

  protected money(c: Campaign, value: number | null, digits = 0): string {
    if (value === null) return '—';
    const symbol = c.currency === 'USD' ? '$' : c.currency === 'EUR' ? '€' : `${c.currency} `;
    return symbol + value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  protected read(fold: HTMLDetailsElement): void {
    const text = this.text().trim();
    if (text.length < 40) {
      this.toasts.error('Paste the text of the campaign page first');
      return;
    }
    this.reading.set(true);
    this.api.readCampaign(text, this.url().trim() || null).subscribe({
      next: (campaign) => {
        this.reading.set(false);
        this.url.set('');
        this.text.set('');
        fold.open = false;
        if (campaign.read_by === 'waiting') {
          this.toasts.ok(`Saved "${campaign.title}" without reading it: the AI is unavailable. Press Read again later.`);
        } else {
          this.toasts.ok(`Read "${campaign.title}"`);
        }
        this.tab.set(this.bucket(campaign));
        this.load();
      },
      error: (err) => {
        this.reading.set(false);
        this.toasts.error(err);
      },
    });
  }

  private patch(campaign: Campaign, patch: CampaignPatch, done?: string): void {
    this.api.updateCampaign(campaign.id, patch).subscribe({
      next: () => {
        if (done) this.toasts.ok(done);
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected readonly rereading = signal<number | null>(null);

  /** A listing added while the AI was unavailable: read its stored text now. */
  protected readAgain(campaign: Campaign): void {
    this.rereading.set(campaign.id);
    this.api.readCampaignAgain(campaign.id).subscribe({
      next: (updated) => {
        this.rereading.set(null);
        this.load();
        this.toasts.ok(`Read: ${updated.title}`);
      },
      error: (err) => {
        this.rereading.set(null);
        this.toasts.error(err);
      },
    });
  }

  protected decide(campaign: Campaign, decision: CampaignDecision): void {
    this.patch(campaign, { decision: campaign.decision === decision ? 'new' : decision });
  }

  protected saveNotes(campaign: Campaign, notes: string): void {
    if (notes !== campaign.notes) this.patch(campaign, { notes }, 'Notes saved');
  }

  protected correct(campaign: Campaign, rate: string, max: string, left: string, status: string): void {
    const number = (value: string) => (value.trim() === '' ? null : Number(value));
    const values = { rate_per_1k: number(rate), max_payout: number(max), budget_left: number(left) };
    if (Object.values(values).some((v) => v !== null && (Number.isNaN(v) || v < 0))) {
      this.toasts.error('Use plain numbers, for example 2.5');
      return;
    }
    this.patch(campaign, { ...values, listing_status: status as Campaign['listing_status'] }, 'Terms updated');
  }

  protected async remove(campaign: Campaign): Promise<void> {
    if (!(await this.confirm.ask({ title: `Remove "${campaign.title}" from the list?`, message: 'Videos made for it are kept.',
      confirm: 'Remove', danger: true }))) return;
    this.api.deleteCampaign(campaign.id).subscribe({
      next: () => {
        this.toasts.ok('Campaign removed');
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }
}
