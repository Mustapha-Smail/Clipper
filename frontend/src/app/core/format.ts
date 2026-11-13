import { Pipe, PipeTransform } from '@angular/core';

/** 276.55 -> "4:36.55" */
export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${(seconds - m * 60).toFixed(2).padStart(5, '0')}`;
}

/** "4:36.5", "1:02:03" or "276.5" -> seconds; NaN when it is not a time. */
export function parseTime(text: string): number {
  const parts = String(text).trim().split(':').map(Number);
  if (!parts.length || parts.some((p) => Number.isNaN(p)) || String(text).trim() === '') return NaN;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

@Pipe({ name: 'length' })
export class LengthPipe implements PipeTransform {
  /** Seconds as a rough human length: "7 min", "1 h 12 min". */
  transform(seconds: number): string {
    const minutes = Math.max(1, Math.round(seconds / 60));
    return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
  }
}

@Pipe({ name: 'megabytes' })
export class MegabytesPipe implements PipeTransform {
  transform(bytes: number): string {
    return `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB`;
  }
}

@Pipe({ name: 'ago' })
export class AgoPipe implements PipeTransform {
  transform(iso: string | null): string {
    if (!iso) return '';
    const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
    return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }
}

@Pipe({ name: 'until' })
export class UntilPipe implements PipeTransform {
  /** A date ahead, as "today", "tomorrow" or "in 12 days (13 Dec)". */
  transform(iso: string | null): string {
    if (!iso) return '';
    const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000);
    if (days <= 0) return 'today';
    if (days === 1) return 'tomorrow';
    return `in ${days} days (${new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })})`;
  }
}

@Pipe({ name: 'clock' })
export class ClockPipe implements PipeTransform {
  transform(iso: string): string {
    return new Date(iso).toLocaleTimeString(undefined, { hour12: false });
  }
}

/** How long a job ran (or has been running), as "42 s" or "3 min 5 s". */
export function elapsed(startIso: string | null, endIso: string | null): string {
  if (!startIso) return '';
  const seconds = Math.max(0, Math.round(((endIso ? new Date(endIso).getTime() : Date.now()) - new Date(startIso).getTime()) / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

export const STAGE_NAMES: Record<string, string> = {
  download: 'Fetch',
  transcribe: 'Transcribe',
  subtitles: 'Subtitles',
  select: 'Choose moments',
  render: 'Render',
  check: 'Brief check',
  research: 'Research',
  write: 'Write',
  factcheck: 'Fact check',
  speak: 'AI voice',
  prepare: 'Prepare',
  align: 'Timing',
  plan: 'Plan scenes',
  draw: 'Props and photos',
  animate: 'Animate',
};

export const KIND_NAMES: Record<string, string> = {
  process: 'Full pipeline',
  select: 'Choose moments',
  render: 'Render',
  check: 'Brief check',
  subtitles: 'Subtitle detection',
  script: 'Script',
  redraft: 'Script redraft',
  spoken: 'Spoken rewrite',
  factcheck: 'Fact check',
  voice_ai: 'AI voice',
  voice_rec: 'Recording',
  align: 'Voice timing',
  storyboard: 'Storyboard',
  animate: 'Animation',
};
