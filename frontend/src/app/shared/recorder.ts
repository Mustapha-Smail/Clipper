import { Component, OnDestroy, computed, input, output, signal } from '@angular/core';
import { ScriptSection } from '../core/models';

type State = 'idle' | 'asking' | 'recording' | 'paused' | 'recorded';

/**
 * Records your narration in the browser while showing the script as a teleprompter.
 * The browser's own voice processing is turned off: the server evens out loudness,
 * and echo cancellation or automatic gain would colour the voice.
 */
@Component({
  selector: 'app-recorder',
  template: `
    <div class="recorder">
      <div class="prompter" [class.live]="state() === 'recording'">
        @for (s of sections(); track $index) {
          <h4>{{ s.heading }}</h4>
          <p>{{ s.text }}</p>
        }
      </div>

      <div class="controls">
        @if (state() === 'recording' || state() === 'paused') {
          <span class="dot" [class.on]="state() === 'recording'"></span>
          <span class="num time">{{ clock() }}</span>
          <div class="meter" aria-hidden="true"><i [style.width.%]="level() * 100"></i></div>
        }
        <span class="grow"></span>
        @switch (state()) {
          @case ('idle') {
            <span class="small muted">Read at your normal pace. Pauses and retakes are fine: the app finds the script in your recording.</span>
            <button class="primary" (click)="start()">Start recording</button>
          }
          @case ('asking') {
            <span class="small muted">Allow the microphone in your browser…</span>
          }
          @case ('recording') {
            <button (click)="pause()">Pause</button>
            <button class="primary" (click)="stop()">Stop</button>
          }
          @case ('paused') {
            <button (click)="resume()">Resume</button>
            <button class="primary" (click)="stop()">Stop</button>
          }
          @case ('recorded') {
            <audio controls [src]="url()"></audio>
            <button class="quiet" (click)="reset()">Record again</button>
            <button class="primary" (click)="use()">Use this recording</button>
          }
        }
        <button class="quiet" (click)="closed.emit()">Close</button>
      </div>
      @if (error()) { <div class="notice bad small"><span>{{ error() }}</span></div> }
    </div>
  `,
  styles: `
    .recorder {
      display: grid;
      gap: 12px;
    }
    .prompter {
      max-height: 46vh;
      overflow-y: auto;
      padding: 18px 22px;
      border-radius: var(--radius-sm);
      background: var(--sunken);
      font-size: 21px;
      line-height: 1.6;
      &.live {
        box-shadow: inset 0 0 0 2px var(--bad);
      }
      h4 {
        margin: 18px 0 4px;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.07em;
        color: var(--muted);
        &:first-child {
          margin-top: 0;
        }
      }
      p {
        margin: 0;
      }
    }
    .controls {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--muted);
      &.on {
        background: var(--bad);
      }
    }
    .time {
      font-weight: 650;
      min-width: 48px;
    }
    .meter {
      width: 140px;
      height: 6px;
      border-radius: 3px;
      background: var(--sunken);
      overflow: hidden;
      i {
        display: block;
        height: 100%;
        background: var(--good);
        transition: width 0.08s;
      }
    }
    audio {
      height: 36px;
    }
  `,
})
export class Recorder implements OnDestroy {
  readonly sections = input.required<ScriptSection[]>();
  /** The finished recording, ready to upload. */
  readonly recorded = output<{ blob: Blob; name: string }>();
  readonly closed = output<void>();

  protected readonly state = signal<State>('idle');
  protected readonly seconds = signal(0);
  protected readonly level = signal(0);
  protected readonly url = signal<string>('');
  protected readonly error = signal('');
  protected readonly clock = computed(() => {
    const s = this.seconds();
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  });

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private blob: Blob | null = null;
  private timer: ReturnType<typeof setInterval> | undefined;
  private audioContext: AudioContext | null = null;
  private frame = 0;

  protected async start(): Promise<void> {
    this.error.set('');
    this.state.set('asking');
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
      });
    } catch {
      this.state.set('idle');
      this.error.set('The microphone is blocked or missing. Allow it for this site in the browser, then try again.');
      return;
    }
    const type = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find((t) => MediaRecorder.isTypeSupported(t));
    this.recorder = new MediaRecorder(this.stream, type ? { mimeType: type, audioBitsPerSecond: 128_000 } : undefined);
    this.chunks = [];
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.recorder.onstop = () => this.finish();
    this.recorder.start(1000);
    this.seconds.set(0);
    this.timer = setInterval(() => this.state() === 'recording' && this.seconds.update((s) => s + 1), 1000);
    this.meter(this.stream);
    this.state.set('recording');
  }

  private meter(stream: MediaStream): void {
    this.audioContext = new AudioContext();
    const analyser = this.audioContext.createAnalyser();
    analyser.fftSize = 1024;
    this.audioContext.createMediaStreamSource(stream).connect(analyser);
    const data = new Float32Array(analyser.fftSize);
    const tick = () => {
      analyser.getFloatTimeDomainData(data);
      let peak = 0;
      for (const v of data) peak = Math.max(peak, Math.abs(v));
      this.level.set(Math.min(1, peak * 1.4));
      this.frame = requestAnimationFrame(tick);
    };
    tick();
  }

  protected pause(): void {
    this.recorder?.pause();
    this.state.set('paused');
  }

  protected resume(): void {
    this.recorder?.resume();
    this.state.set('recording');
  }

  protected stop(): void {
    this.recorder?.stop();
  }

  private finish(): void {
    this.release();
    this.blob = new Blob(this.chunks, { type: this.recorder?.mimeType || 'audio/webm' });
    if (this.url()) URL.revokeObjectURL(this.url());
    this.url.set(URL.createObjectURL(this.blob));
    this.state.set('recorded');
  }

  protected reset(): void {
    if (this.url()) URL.revokeObjectURL(this.url());
    this.url.set('');
    this.blob = null;
    this.state.set('idle');
  }

  protected use(): void {
    if (!this.blob) return;
    const ext = this.blob.type.includes('mp4') ? 'm4a' : 'webm';
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
    this.recorded.emit({ blob: this.blob, name: `recording-${stamp}.${ext}` });
  }

  private release(): void {
    clearInterval(this.timer);
    cancelAnimationFrame(this.frame);
    this.level.set(0);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.audioContext?.close();
    this.audioContext = null;
  }

  ngOnDestroy(): void {
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.onstop = null;
      this.recorder.stop();
    }
    this.release();
    if (this.url()) URL.revokeObjectURL(this.url());
  }
}
