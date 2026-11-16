import { Component, computed, input, model, signal } from '@angular/core';
import { BufferChannel } from '../core/models';
import { SERVICE_NAMES } from '../core/posts-store';

const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
const HOURS = Array.from({ length: 24 }, (_, h) => h);
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5);
const MAX_DAYS = 365;

const pad = (n: number) => String(n).padStart(2, '0');

/** A date as the "YYYY-MM-DD" of the viewer's calendar. */
export function dayKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "YYYY-MM-DDTHH:MM" in the viewer's time, as a datetime-local input would hold it. */
export function localValue(date: Date): string {
  return `${dayKey(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** How far `tz` is ahead of UTC at `utcMs`, in milliseconds. */
function zoneOffset(utcMs: number, tz: string): number {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(utcMs)).map((p) => [p.type, p.value]));
  const asUtc = Date.UTC(+parts['year'], +parts['month'] - 1, +parts['day'], +parts['hour'], +parts['minute'], +parts['second']);
  return asUtc - utcMs;
}

/** A wall-clock time in a timezone (Buffer's slots are in the channel's), as a real instant. */
export function zonedTime(day: string, hhmm: string, tz: string): Date {
  const [y, m, d] = day.split('-').map(Number);
  const [h, mi] = hhmm.split(':').map(Number);
  const guess = Date.UTC(y, m - 1, d, h, mi);
  let utc = guess - zoneOffset(guess, tz);
  utc = guess - zoneOffset(utc, tz);       // second pass settles a daylight-saving change
  return new Date(utc);
}

/** The next posting time of a channel's Buffer schedule after `from`; null when it has none. */
export function nextSlot(channel: BufferChannel, from = new Date()): Date | null {
  for (let ahead = 0; ahead < 8; ahead++) {
    const day = new Date(from.getFullYear(), from.getMonth(), from.getDate() + ahead);
    const times = channel.schedule.find((s) => s.day === DAYS[day.getDay()])?.times ?? [];
    for (const time of times) {
      const at = zonedTime(dayKey(day), time, channel.timezone);
      if (at > from) return at;
    }
  }
  return null;
}

interface Cell { key: string; date: Date; day: number; inMonth: boolean; past: boolean; far: boolean; today: boolean }

/**
 * A month calendar and a time, with the posting times of the chosen Buffer
 * channels offered for the selected day. The value is "YYYY-MM-DDTHH:MM" in
 * the viewer's time; `marks` counts posts already scheduled per day.
 */
@Component({
  selector: 'app-when-picker',
  template: `
    <div class="picker">
      <div class="calendar">
        <div class="month">
          <button type="button" class="nav" (click)="shift(-1)" [disabled]="!canGoBack()" aria-label="Previous month">‹</button>
          <strong>{{ monthLabel() }}</strong>
          <button type="button" class="nav" (click)="shift(1)" [disabled]="!canGoOn()" aria-label="Next month">›</button>
        </div>
        <div class="grid" role="grid" [attr.aria-label]="monthLabel()">
          @for (d of weekdays; track d) { <span class="weekday">{{ d }}</span> }
          @for (c of cells(); track c.key) {
            <button type="button" class="day" role="gridcell"
                    [class.out]="!c.inMonth" [class.today]="c.today" [class.on]="c.key === selectedDay()"
                    [disabled]="c.past || c.far" (click)="pickDay(c.key)"
                    [attr.aria-selected]="c.key === selectedDay()"
                    [attr.aria-label]="c.date.toDateString() + (marks()[c.key] ? ', ' + marks()[c.key] + ' already scheduled' : '')"
                    [title]="marks()[c.key] ? marks()[c.key] + ' post' + (marks()[c.key] > 1 ? 's' : '') + ' already scheduled' : ''">
              {{ c.day }}
              @if (marks()[c.key]) { <span class="dot"></span> }
            </button>
          }
        </div>
      </div>

      <div class="times">
        <div class="clock">
          <span class="small muted">Time</span>
          <select [value]="hour()" (change)="setTime(+$any($event.target).value, minute())" aria-label="Hour">
            @for (h of hours; track h) { <option [value]="h" [selected]="h === hour()">{{ two(h) }}</option> }
          </select>
          <span class="colon">:</span>
          <select [value]="minute()" (change)="setTime(hour(), +$any($event.target).value)" aria-label="Minute">
            @for (m of minuteChoices(); track m) { <option [value]="m" [selected]="m === minute()">{{ two(m) }}</option> }
          </select>
        </div>

        @for (group of slots(); track group.channel.id) {
          <div class="slots">
            <span class="small muted">{{ group.name }}'s Buffer times</span>
            <div class="chips">
              @for (slot of group.times; track slot.label) {
                <button type="button" class="chip" [class.on]="slot.value === value()" [disabled]="slot.past"
                        (click)="value.set(slot.value)">{{ slot.label }}</button>
              } @empty {
                <span class="small muted">None on this day</span>
              }
            </div>
          </div>
        }

        <div class="summary" [class.bad]="tooSoon()">
          @if (chosen(); as at) {
            <strong>{{ longDate(at) }}</strong>
            <span class="small">{{ tooSoon() ? 'Too soon: choose a time at least 10 minutes from now' : fromNow(at) }}</span>
          } @else {
            <span class="small muted">Choose a day</span>
          }
        </div>
      </div>
    </div>
  `,
  styles: `
    /* Laid out by the room it gets, not the window: it sits in narrow cards and wide pages alike. */
    :host { display: block; container-type: inline-size; }
    .picker {
      display: grid;
      grid-template-columns: minmax(0, 300px) minmax(0, 1fr);
      gap: 16px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 14px;
      background: var(--surface);
    }
    @container (max-width: 560px) {
      .picker { grid-template-columns: minmax(0, 1fr); }
      .calendar { max-width: 340px; width: 100%; justify-self: center; }
    }
    .month {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
      strong { font-size: 14px; text-transform: capitalize; }
    }
    /* The app's buttons are inline-flex aligned to the start: the circles need their content centred. */
    .nav, .day, .chip { justify-content: center; text-align: center; }
    .nav {
      width: 32px;
      height: 32px;
      padding: 0;
      border-radius: 50%;
      font-size: 18px;
      line-height: 1;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 2px;
    }
    .weekday {
      text-align: center;
      font-size: 11px;
      font-weight: 650;
      color: var(--muted);
      padding: 4px 0;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .day {
      position: relative;
      aspect-ratio: 1;
      padding: 0;
      border: 1px solid transparent;
      border-radius: 50%;
      background: none;
      box-shadow: none;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      cursor: pointer;
      &:hover:not(:disabled):not(.on) { background: var(--sunken); }
      &.out { color: var(--muted); }
      &.today { border-color: var(--line-strong); font-weight: 700; }
      &.on { background: var(--accent); color: var(--accent-ink); font-weight: 700; border-color: var(--accent); }
      &:disabled { color: var(--line-strong); cursor: default; background: none; }
    }
    .dot {
      position: absolute;
      bottom: 3px;
      left: 50%;
      width: 5px;
      height: 5px;
      margin-left: -2.5px;
      border-radius: 50%;
      background: var(--good);
    }
    .day.on .dot { background: var(--accent-ink); }
    .times {
      display: grid;
      gap: 12px;
      align-content: start;
    }
    .clock {
      display: flex;
      align-items: center;
      gap: 6px;
      select { width: auto; font-variant-numeric: tabular-nums; }
      .muted { margin-right: 4px; }
    }
    .colon { font-weight: 700; }
    .slots { display: grid; gap: 6px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip {
      padding: 4px 12px;
      border-radius: 99px;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      &.on { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
    }
    .summary {
      display: grid;
      gap: 2px;
      padding: 10px 12px;
      border-radius: var(--radius-sm);
      background: var(--sunken);
      &.bad { background: var(--bad-soft); color: var(--bad); }
    }
  `,
})
export class WhenPicker {
  /** "YYYY-MM-DDTHH:MM" in the viewer's time. */
  readonly value = model('');
  readonly channels = input<BufferChannel[]>([]);
  readonly marks = input<Record<string, number>>({});

  protected readonly hours = HOURS;
  /** Monday first: 1 January 2024 was a Monday. */
  protected readonly weekdays = Array.from({ length: 7 }, (_, i) =>
    new Date(2024, 0, 1 + i).toLocaleDateString(undefined, { weekday: 'short' }).slice(0, 2));

  /** The first day of the month on display. */
  private readonly shown = signal(this.firstOfMonth(this.parse(this.value()) ?? new Date()));
  private readonly now = signal(new Date());

  protected readonly chosen = computed(() => this.parse(this.value()));
  protected readonly selectedDay = computed(() => this.value().slice(0, 10));
  protected readonly hour = computed(() => this.chosen()?.getHours() ?? 18);
  protected readonly minute = computed(() => this.chosen()?.getMinutes() ?? 0);
  /** Five-minute steps, plus the current minute when a Buffer slot set one in between. */
  protected readonly minuteChoices = computed(() =>
    MINUTES.includes(this.minute()) ? MINUTES : [...MINUTES, this.minute()].sort((a, b) => a - b));
  protected readonly tooSoon = computed(() => {
    const at = this.chosen();
    return !!at && at.getTime() < this.now().getTime() + 10 * 60_000;
  });

  protected readonly monthLabel = computed(() =>
    this.shown().toLocaleDateString(undefined, { month: 'long', year: 'numeric' }));
  protected readonly canGoBack = computed(() => this.shown() > this.firstOfMonth(this.now()));
  protected readonly canGoOn = computed(() => {
    const last = new Date(this.now().getTime() + MAX_DAYS * 86_400_000);
    return this.shown() < this.firstOfMonth(last);
  });

  protected readonly cells = computed<Cell[]>(() => {
    const first = this.shown();
    const offset = (first.getDay() + 6) % 7;          // weeks start on Monday
    const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
    const today = dayKey(this.now());
    const startOfToday = new Date(this.now().getFullYear(), this.now().getMonth(), this.now().getDate());
    const last = new Date(startOfToday.getTime() + MAX_DAYS * 86_400_000);
    return Array.from({ length: 42 }, (_, i) => {
      const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
      const key = dayKey(date);
      return { key, date, day: date.getDate(), inMonth: date.getMonth() === first.getMonth(),
               past: date < startOfToday, far: date > last, today: key === today };
    });
  });

  /** Each chosen channel's Buffer posting times on the selected day, in the viewer's time. */
  protected readonly slots = computed(() => {
    const day = this.selectedDay();
    if (!day) return [];
    const weekday = DAYS[new Date(`${day}T12:00`).getDay()];
    return this.channels().map((channel) => ({
      channel,
      name: SERVICE_NAMES[channel.service] ?? channel.service,
      times: (channel.schedule.find((s) => s.day === weekday)?.times ?? []).map((time) => {
        const at = zonedTime(day, time, channel.timezone);
        return { value: localValue(at), label: at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
                 past: at.getTime() < this.now().getTime() + 10 * 60_000 };
      }),
    }));
  });

  protected two(n: number): string {
    return pad(n);
  }

  protected shift(months: number): void {
    const s = this.shown();
    this.shown.set(new Date(s.getFullYear(), s.getMonth() + months, 1));
  }

  protected pickDay(key: string): void {
    this.now.set(new Date());
    this.value.set(`${key}T${pad(this.hour())}:${pad(this.minute())}`);
  }

  protected setTime(hour: number, minute: number): void {
    const day = this.selectedDay() || dayKey(new Date());
    this.now.set(new Date());
    this.value.set(`${day}T${pad(hour)}:${pad(minute)}`);
  }

  protected longDate(at: Date): string {
    return at.toLocaleString(undefined, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' });
  }

  protected fromNow(at: Date): string {
    const minutes = Math.round((at.getTime() - this.now().getTime()) / 60_000);
    if (minutes < 60) return `in ${minutes} min, your time`;
    const hours = Math.round(minutes / 60);
    if (hours < 36) return `in ${hours} h, your time`;
    return `in ${Math.round(hours / 24)} days, your time`;
  }

  private parse(text: string): Date | null {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) return null;
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  private firstOfMonth(date: Date): Date {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }
}
