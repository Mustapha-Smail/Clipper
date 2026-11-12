import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../core/api';
import { AgoPipe } from '../core/format';
import { JobsStore } from '../core/jobs-store';
import { Script, VoiceOptions, VoiceTake } from '../core/models';
import { Toasts } from '../core/toast';
import { JobCard } from './job-card';
import { Recorder } from './recorder';

type Mode = 'none' | 'record' | 'ai';

/** The voice of a script: record it, upload a recording, or have an AI voice read it. */
@Component({
  selector: 'app-voice-panel',
  imports: [FormsModule, AgoPipe, JobCard, Recorder],
  templateUrl: './voice-panel.html',
  styleUrl: './voice-panel.scss',
})
export class VoicePanel {
  private readonly api = inject(Api);
  private readonly toasts = inject(Toasts);
  private readonly store = inject(JobsStore);

  readonly script = input.required<Script>();
  /** True while another job (writing, fact check) works on the script. */
  readonly busy = input(false);

  protected readonly takes = signal<VoiceTake[]>([]);
  protected readonly options = signal<VoiceOptions | null>(null);
  protected readonly mode = signal<Mode>('none');
  protected readonly uploading = signal(false);
  protected readonly engine = signal<'kokoro' | 'elevenlabs'>('kokoro');
  protected readonly voice = signal('');
  protected readonly speed = signal(1);

  protected readonly voices = computed(() => {
    const options = this.options();
    if (!options) return [];
    return this.engine() === 'kokoro' ? options.kokoro : options.elevenlabs;
  });
  protected readonly working = computed(() => this.busy() || this.takes().some((t) => t.job));

  constructor() {
    this.api.voiceOptions().subscribe({
      next: (options) => {
        this.options.set(options);
        this.engine.set(options.defaults.engine === 'elevenlabs' && options.elevenlabs_available ? 'elevenlabs' : 'kokoro');
        this.pickDefaultVoice();
        this.speed.set(options.defaults.speed);
      },
      error: (err) => this.toasts.error(err),
    });
    // Load the takes when the script changes, and again whenever a voice job starts or ends.
    let known = '';
    effect(() => {
      const id = this.script().id;
      const signature = id + '|' + this.store.jobs().filter((j) => j.script_id === id && j.take_id)
        .map((j) => `${j.id}:${j.state}:${j.stage}`).join();
      if (signature !== known) {
        known = signature;
        untracked(() => this.load());
      }
    });
  }

  private load(): void {
    this.api.takes(this.script().id).subscribe({ next: (list) => this.takes.set(list), error: () => undefined });
  }

  protected pickDefaultVoice(): void {
    const options = this.options();
    if (!options) return;
    const wanted = this.engine() === 'kokoro' ? options.defaults.kokoro_voice : options.defaults.elevenlabs_voice;
    const list = this.voices();
    this.voice.set(list.some((v) => v.id === wanted) ? wanted : (list[0]?.id ?? ''));
  }

  protected label(take: VoiceTake): string {
    if (take.source === 'recording') return `Your recording · ${take.voice}`;
    const options = this.options();
    const list = take.source === 'kokoro' ? options?.kokoro : options?.elevenlabs;
    const name = list?.find((v) => v.id === take.voice)?.label.split(' · ')[0] ?? take.voice;
    return `${take.source === 'kokoro' ? 'Kokoro' : 'ElevenLabs'} · ${name}${take.speed && take.speed !== 1 ? ` · ${take.speed}×` : ''}`;
  }

  protected time(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined) return '—';
    const s = Math.round(seconds);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  // --- making takes --------------------------------------------------------------------

  protected generate(): void {
    if (!this.voice()) {
      this.toasts.error('Choose a voice first');
      return;
    }
    this.api.createAiTake(this.script().id, this.engine(), this.voice(), this.speed()).subscribe({
      next: () => {
        this.mode.set('none');
        this.toasts.ok('The AI voice is being made. A six-minute script takes about two minutes.');
        this.store.refresh();
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected upload(file: Blob, name: string, picker?: HTMLInputElement): void {
    this.uploading.set(true);
    this.api.uploadRecording(this.script().id, file, name).subscribe({
      next: () => {
        this.uploading.set(false);
        this.mode.set('none');
        if (picker) picker.value = '';
        this.toasts.ok('Recording received. It is being prepared and timed.');
        this.store.refresh();
        this.load();
      },
      error: (err) => {
        this.uploading.set(false);
        if (picker) picker.value = '';
        this.toasts.error(err);
      },
    });
  }

  protected pickFile(picker: HTMLInputElement): void {
    const file = picker.files?.[0];
    if (file) this.upload(file, file.name, picker);
  }

  // --- managing takes ------------------------------------------------------------------

  protected choose(take: VoiceTake): void {
    this.api.chooseTake(take.id).subscribe({
      next: () => {
        this.toasts.ok('This take will be used for the video');
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected realign(take: VoiceTake): void {
    this.api.realignTake(take.id).subscribe({
      next: () => {
        this.store.refresh();
        this.load();
      },
      error: (err) => this.toasts.error(err),
    });
  }

  protected remove(take: VoiceTake): void {
    if (!confirm(`Delete "${this.label(take)}"? Its audio is deleted too.`)) return;
    this.api.deleteTake(take.id).subscribe({
      next: () => this.takes.update((list) => list.filter((t) => t.id !== take.id)),
      error: (err) => this.toasts.error(err),
    });
  }

  protected audio(take: VoiceTake): string {
    return this.api.takeAudioUrl(take);
  }
}
