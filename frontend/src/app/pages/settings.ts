import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, errorMessage } from '../core/api';
import { JobsStore } from '../core/jobs-store';
import { AiModel, AiModels, AppSettings, SettingsBundle, UsageReport, VoiceOptions } from '../core/models';
import { Toasts } from '../core/toast';

@Component({
  selector: 'app-settings-page',
  imports: [FormsModule],
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <h1>Settings</h1>
          <p class="muted">Changes apply to jobs started after you save.</p>
        </div>
      </div>

      @if (form(); as f) {
        <div class="stack">
          <section class="card">
            <h3>Features</h3>
            <p class="small muted">Turn off what you do not use: its pages leave the menu and its settings are hidden here.
              Nothing is deleted, and you can turn it back on at any time.</p>
            <div class="form-grid">
              <label class="check wide"><input type="checkbox" [(ngModel)]="f.feature_studio"
                                               [disabled]="f.feature_studio && !f.feature_clipping" />
                <span>Studio<small>Animated videos for your channel: ideas, scripts, voice, storyboards and renders.</small></span></label>
              <label class="check wide"><input type="checkbox" [(ngModel)]="f.feature_clipping"
                                               [disabled]="f.feature_clipping && !f.feature_studio" />
                <span>Clipping<small>Long videos turned into captioned clips for paid campaigns.</small></span></label>
            </div>
          </section>

          @if (f.feature_studio) {

          <h2 class="group-title">Studio</h2>

          <section class="card">
            <h3>Studio voice</h3>
            <p class="small muted">Defaults for AI voices on the script page. You can still record your own voice.</p>
            <div class="form-grid">
              <label class="field"><span>Engine</span>
                <select [(ngModel)]="f.voice_engine">
                  <option value="kokoro">Kokoro (free, runs here)</option>
                  <option value="elevenlabs">ElevenLabs (paid)</option>
                </select>
              </label>
              @if (voices(); as v) {
                <label class="field"><span>Kokoro voice</span>
                  <select [(ngModel)]="f.kokoro_voice">
                    @for (c of v.kokoro; track c.id) { <option [value]="c.id">{{ c.label }}</option> }
                  </select>
                </label>
                <label class="field"><span>ElevenLabs voice</span>
                  @if (v.elevenlabs.length) {
                    <select [(ngModel)]="f.elevenlabs_voice">
                      <option value="">Choose a voice</option>
                      @for (c of v.elevenlabs; track c.id) { <option [value]="c.id">{{ c.label }}</option> }
                    </select>
                  } @else {
                    <input type="text" [(ngModel)]="f.elevenlabs_voice" placeholder="Voice ID" />
                    <small>{{ v.elevenlabs_error || 'Set ELEVENLABS_API_KEY in .env to pick from your voices.' }}</small>
                  }
                </label>
              }
              <label class="field"><span>ElevenLabs model</span>
                <select [(ngModel)]="f.elevenlabs_model">
                  <option value="eleven_multilingual_v2">Multilingual v2 (most natural)</option>
                  <option value="eleven_v3">v3 (most expressive)</option>
                  <option value="eleven_flash_v2_5">Flash v2.5 (cheapest, fastest)</option>
                </select>
              </label>
              <label class="field"><span>Speed</span>
                <input type="number" min="0.7" max="1.3" step="0.05" [(ngModel)]="f.voice_speed" />
                <small>1 is the voice's natural pace.</small>
              </label>
              <label class="field"><span>Pauses (Kokoro)</span>
                <select [(ngModel)]="f.voice_pauses">
                  <option value="tight">Tight (brisk)</option>
                  <option value="natural">Natural</option>
                  <option value="relaxed">Relaxed</option>
                </select>
                <small>Kokoro reads one sentence at a time and pauses like a narrator: longer after questions and punchlines.</small>
              </label>
              <label class="field"><span>Timing model</span>
                <select [(ngModel)]="f.voice_align_model">
                  <option value="small">Small (recommended)</option>
                  <option value="medium">Medium</option>
                  <option value="large-v3-turbo">Large v3 turbo</option>
                </select>
                <small>Whisper only has to find words the script already knows. Small is quick and light; the large
                  model can run Docker out of memory.</small>
              </label>
            </div>
          </section>

          <section class="card">
            <h3>Ink style</h3>
            <p class="small muted">How hand-drawn the ink videos feel.</p>
            <div class="form-grid">
              <label class="field"><span>Line wobble</span>
                <select [(ngModel)]="f.ink_wobble">
                  <option value="off">Off: still lines</option>
                  <option value="subtle">Subtle (recommended)</option>
                  <option value="strong">Strong: classic hand-drawn boil</option>
                </select>
                <small>Hand-drawn animation redraws lines a little differently each time. Too much of it is tiring
                  to watch; render again after changing it.</small>
              </label>
            </div>
          </section>

          }

          @if (f.feature_clipping) {

          <h2 class="group-title">Clipping</h2>

          <section class="card">
            <h3>Your channel</h3>
            <p class="small muted">Used to rank campaigns. It does not change how clips are made.</p>
            <div class="form-grid">
              <label class="field wide"><span>Theme</span>
                <input type="text" maxlength="300" [(ngModel)]="f.theme" />
                <small>What your account posts about. Campaigns are scored on how well they fit it.</small>
              </label>
              <div class="field wide">
                <span class="label">Platforms you post on</span>
                <div class="row">
                  @for (p of platformChoices; track p) {
                    <label class="check"><input type="checkbox" [checked]="f.platforms.includes(p)"
                                                (change)="togglePlatform(f, p)" /><span>{{ p }}</span></label>
                  }
                </div>
                <small class="muted">A campaign is ranked on the best rate it pays on one of these.</small>
              </div>
              <label class="field"><span>Viewers in the US, UK, Canada and Australia (%)</span>
                <input type="number" min="0" max="100" [(ngModel)]="f.audience_tier1_share" placeholder="Not known yet" />
                <small>From your account analytics. Leave empty until you have the figure: campaigns with a
                  location rule are then flagged instead of ruled out.</small>
              </label>
            </div>
          </section>

          <section class="card">
            <h3>Moments and brief checks</h3>
            <p class="small muted">Defaults for new videos. Each video can override the numbers.</p>
            <div class="form-grid">
              <label class="field"><span>Clips per video</span>
                <input type="number" min="1" max="20" [(ngModel)]="f.clips_per_video" /></label>
              <label class="field"><span>Shortest clip (seconds)</span>
                <input type="number" min="5" max="600" [(ngModel)]="f.min_len" /></label>
              <label class="field"><span>Longest clip (seconds)</span>
                <input type="number" min="10" max="900" [(ngModel)]="f.max_len" /></label>
              <label class="check wide"><input type="checkbox" [(ngModel)]="f.auto_compliance" />
                <span>Check each clip against the campaign brief after rendering
                  <small>Only runs for videos that have a brief. You can always run the check by hand.</small></span>
              </label>
            </div>
          </section>

          <section class="card">
            <h3>Download</h3>
            <p class="small muted">How source videos are fetched from links.</p>
            <div class="form-grid">
              <label class="field"><span>Maximum quality</span>
                <select [(ngModel)]="f.max_height">
                  <option [ngValue]="1080">1080p</option>
                  <option [ngValue]="720">720p (smaller, faster)</option>
                </select>
              </label>
              <label class="field wide"><span>Cookies file</span>
                <input type="text" [(ngModel)]="f.cookies_file" placeholder="Optional path to cookies.txt" />
                <small>Only needed if a site blocks downloads from this machine, which is common on cloud servers.</small>
              </label>
            </div>
          </section>

          <section class="card">
            <h3>Storage</h3>
            <p class="small muted">Where finished clips are written.</p>
            <div class="form-grid">
              <label class="field wide"><span>Output folder</span>
                <input type="text" [(ngModel)]="f.output_dir" [placeholder]="bundle()!.default_clips_dir" />
                <small>
                  Leave empty to use {{ bundle()!.default_clips_dir }}.
                  @if (store.status()?.in_docker) {
                    This app runs in Docker: use a path inside the container, such as /output, and mount a folder there.
                  } @else {
                    Any folder this machine can write to, for example a mounted drive.
                  }
                  Clips already rendered stay where they are.
                </small>
              </label>
              <label class="check wide"><input type="checkbox" [(ngModel)]="f.delete_source_after_render" />
                <span>Delete the downloaded video once all its clips are rendered
                  <small>Saves disk space. Re-rendering a clip later means adding the video again.</small></span>
              </label>
            </div>
          </section>

          }

          <h2 class="group-title">Both</h2>
          <p class="small muted group-note">Used by the Studio and by Clipping.</p>

          <section class="card">
            <h3>AI models</h3>
            <p class="small muted">Every AI task belongs to one of three tiers. Put the quick tier on a small model to spend
              far less; keep a strong model for writing. OpenAI needs OPENAI_API_KEY in .env (or OPENAI_BASE_URL for an
              OpenAI-compatible server such as OpenRouter or Ollama).</p>
            <div class="tiers">
              @for (tier of tiers; track tier.key) {
                <div class="tier">
                  <div>
                    <strong>{{ tier.name }}</strong>
                    <small class="muted">{{ usage()?.tiers?.[tier.key]?.tasks?.join(', ') || tier.hint }}</small>
                  </div>
                  <select [ngModel]="$any(f)['ai_' + tier.key + '_provider']"
                          (ngModelChange)="setProvider(f, tier.key, $event)" [attr.aria-label]="tier.name + ' provider'">
                    <option value="anthropic">Claude</option>
                    <option value="openai">OpenAI or compatible</option>
                  </select>
                  <div class="model">
                    <select [ngModel]="custom().has(tier.key) || !listed(f, tier.key) ? '__other' : model(f, tier.key)"
                            (ngModelChange)="pickModel(f, tier.key, $event)" [attr.aria-label]="tier.name + ' model'">
                      @for (m of modelsFor(f, tier.key); track m.id) {
                        <option [value]="m.id">{{ m.label }}{{ m.price ? ' · $' + m.price[0] + ' / $' + m.price[1] : '' }}</option>
                      }
                      <option value="__other">Other…</option>
                    </select>
                    @if (custom().has(tier.key) || !listed(f, tier.key)) {
                      <input type="text" [ngModel]="model(f, tier.key)" (ngModelChange)="$any(f)['ai_' + tier.key + '_model'] = $event"
                             placeholder="Model id, e.g. llama3.1:8b" [attr.aria-label]="tier.name + ' model id'" />
                    }
                  </div>
                </div>
              }
            </div>
            <p class="small muted">Prices are per million tokens in / out.
              <button class="sm quiet" (click)="loadModels(true)">Refresh the model lists</button>
              @if (models()?.openai?.error; as e) { <span> · OpenAI: {{ e }}</span> }
            </p>
            @if (store.status(); as st) {
              <p class="small muted">Claude key: {{ st.api_key ? 'found' : 'missing' }} · OpenAI key:
                {{ st.openai_key ? 'found' : 'missing' }}{{ st.openai_base_url ? ' · compatible server: ' + st.openai_base_url : '' }}</p>
            }

            @if (usage(); as u) {
              <h4 class="usage-title">Spending, last
                <select [ngModel]="usageDays()" (ngModelChange)="loadUsage(+$event)" aria-label="Period">
                  <option [ngValue]="7">7 days</option><option [ngValue]="30">30 days</option><option [ngValue]="90">90 days</option>
                </select>
                <button class="sm quiet" (click)="loadUsage(usageDays())" [disabled]="refreshing()">
                  {{ refreshing() ? 'Refreshing…' : 'Refresh' }}</button>
              </h4>
              <p class="small">{{ u.calls }} calls{{ u.failed ? ', ' + u.failed + ' failed' : '' }} · about
                <strong>{{ '$' + u.cost_usd.toFixed(2) }}</strong> <span class="muted">(estimated from list prices)</span></p>
              @if (u.by_task.length) {
                <table class="usage">
                  <thead><tr><th>Task</th><th>Tier</th><th class="num">Calls</th><th class="num">Tokens in / out</th>
                    <th class="num">Searches</th><th class="num">Cost</th></tr></thead>
                  <tbody>
                    @for (line of u.by_task; track line.key) {
                      <tr><td>{{ line.label }}</td><td class="muted">{{ line.tier }}</td><td class="num">{{ line.calls }}</td>
                        <td class="num">{{ kilo(line.input_tokens) }} / {{ kilo(line.output_tokens) }}</td>
                        <td class="num">{{ line.searches || '' }}</td>
                        <td class="num">{{ '$' + line.cost_usd.toFixed(3) }}{{ line.unpriced_calls ? ' +?' : '' }}</td></tr>
                    }
                  </tbody>
                </table>
              }
            }
          </section>

          <section class="card">
            <h3>Transcription</h3>
            <p class="small muted">Speech is transcribed on this machine. No audio leaves it.</p>
            <div class="form-grid">
              <label class="field"><span>Spoken language</span>
                <input type="text" maxlength="2" [(ngModel)]="f.language" /><small>Two-letter code, for example en.</small></label>
              <label class="field"><span>Engine</span>
                <select [(ngModel)]="f.whisper_engine">
                  <option value="auto">Automatic</option>
                  <option value="mlx">MLX (Apple Silicon, not in Docker)</option>
                  <option value="faster-whisper">faster-whisper (CPU, Linux and Docker)</option>
                </select>
                <small>Currently using: {{ store.status()?.whisper_engine }}</small>
              </label>
              <label class="field"><span>Model size</span>
                <select [(ngModel)]="f.whisper_model">
                  <option value="large-v3-turbo">Large v3 turbo (most accurate)</option>
                  <option value="medium">Medium</option>
                  <option value="small">Small (fastest)</option>
                </select>
                <small>A smaller model is faster on a CPU-only machine but makes more caption mistakes.</small>
              </label>
            </div>
          </section>

          <section class="card">
            <h3>Render</h3>
            <p class="small muted">How videos are encoded, and how burned-in captions look (clips and Shorts).</p>
            <div class="form-grid">
              <label class="field"><span>Encoder</span>
                <select [(ngModel)]="f.encoder">
                  <option value="auto">Automatic</option>
                  <option value="videotoolbox">VideoToolbox (Mac hardware, not in Docker)</option>
                  <option value="x264">x264 (CPU, any machine)</option>
                </select>
                <small>Currently using: {{ store.status()?.encoder }}</small>
              </label>
              <label class="field"><span>Bitrate (Mbps)</span>
                <input type="number" min="2" max="20" [(ngModel)]="f.bitrate_mbps" />
                <small>8 Mbps is about 1 MB per second of video.</small></label>
              <label class="field"><span>Hook duration (seconds)</span>
                <input type="number" min="0" max="15" step="0.5" [(ngModel)]="f.hook_seconds" /><small>0 hides the hook.</small></label>
              <label class="field"><span>Caption font</span>
                <select [(ngModel)]="f.caption_font">
                  @for (font of bundle()!.fonts; track font.value) {
                    <option [value]="font.value">{{ font.label }}</option>
                  }
                </select>
              </label>
              <label class="field"><span>Highlight colour</span>
                <input type="color" [(ngModel)]="f.highlight_color" /><small>Colour of the word being spoken.</small></label>
              <label class="field"><span>Source subtitles</span>
                <select [(ngModel)]="f.subtitle_mode">
                  <option value="cover">Blur them</option>
                  <option value="crop">Crop the frame above them</option>
                  <option value="keep">Keep them and add no captions</option>
                  <option value="ignore">Leave them as they are</option>
                </select>
                <small>What to do when a video already has text burned in. Each video can override this.</small>
              </label>
              <label class="check wide"><input type="checkbox" [(ngModel)]="f.captions_enabled" />
                <span>Burn captions into the clips<small>Word-by-word captions with the spoken word highlighted.</small></span>
              </label>
            </div>
          </section>

          @if (store.status(); as status) {
            <section class="card">
              <h3>This installation</h3>
              <p class="small muted">Read-only. These are set when the app starts.</p>
              <dl class="facts">
                <dt>Version</dt><dd>{{ status.version }}</dd>
                <dt>Database</dt><dd>{{ status.database }}</dd>
                <dt>Data folder</dt><dd>{{ status.data_dir }}</dd>
                <dt>Clips folder</dt><dd>{{ status.clips_dir }}</dd>
                <dt>Free disk space</dt><dd>{{ status.disk_free_gb }} GB</dd>
                <dt>Claude API key</dt><dd>{{ status.api_key ? 'Found' : 'Missing: set ANTHROPIC_API_KEY in .env and restart' }}</dd>
                <dt>YouTube API key</dt><dd>{{ status.youtube_key ? 'Found' : 'Missing: Studio ideas are scored without YouTube data. Set YOUTUBE_API_KEY in .env and restart' }}</dd>
                <dt>ElevenLabs key</dt><dd>{{ status.elevenlabs_key ? 'Found' : 'Not set: only the free Kokoro voice is available' }}</dd>
                <dt>Worker</dt><dd>{{ status.worker_online ? 'Online' : 'Offline' }}</dd>
                <dt>Running in Docker</dt><dd>{{ status.in_docker ? 'Yes' : 'No' }}</dd>
              </dl>
            </section>
          }

          <div class="card savebar">
            @if (error()) { <span class="small error-text">{{ error() }}</span> }
            <button class="quiet" (click)="reset()">Restore defaults</button>
            <button class="primary" (click)="save()" [disabled]="saving()">Save settings</button>
          </div>
        </div>
      } @else {
        <div class="stack"><div class="skeleton" style="height:160px"></div><div class="skeleton" style="height:160px"></div></div>
      }
    </div>
  `,
  styles: `
    .group-title {
      margin: 18px 0 -4px;
    }
    .group-note {
      margin: -6px 0 0;
    }
    section > p {
      margin: 4px 0 16px;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
      gap: 16px;
    }
    .wide {
      grid-column: 1 / -1;
    }
    .label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 6px;
    }
    .field small {
      display: block;
      font-size: 12.5px;
      margin-top: 5px;
    }
    .facts {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 6px 20px;
      font-size: 13.5px;
      margin: 0;
    }
    .facts dt {
      color: var(--muted);
    }
    .facts dd {
      margin: 0;
      word-break: break-all;
    }
    .tiers {
      display: grid;
      gap: 10px;
    }
    .model {
      display: grid;
      gap: 6px;
    }
    .tier {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) 190px minmax(0, 1.3fr);
      gap: 10px;
      align-items: center;
      small { display: block; font-size: 12px; }
    }
    .usage-title {
      margin: 18px 0 6px;
      select { width: auto; display: inline-block; margin-left: 6px; }
    }
    .usage {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line); }
      th { color: var(--muted); font-weight: 600; font-size: 12px; }
      .num { text-align: right; }
    }
    @media (max-width: 760px) {
      .tier { grid-template-columns: 1fr; }
    }
    .savebar {
      position: sticky;
      bottom: 16px;
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: flex-end;
      padding: 12px 16px;
    }
    .error-text {
      color: var(--bad);
      font-weight: 550;
      margin-right: auto;
    }
  `,
})
export class SettingsPage {
  private readonly api = inject(Api);
  protected readonly store = inject(JobsStore);
  private readonly toasts = inject(Toasts);

  protected readonly bundle = signal<SettingsBundle | null>(null);
  /** A working copy the inputs write into. */
  protected readonly form = signal<AppSettings | null>(null);
  protected readonly saving = signal(false);
  protected readonly voices = signal<VoiceOptions | null>(null);
  protected readonly usage = signal<UsageReport | null>(null);
  protected readonly usageDays = signal(30);
  protected readonly tiers = [
    { key: 'writing', name: 'Writing', hint: 'Scripts, storyboards, moments, ideas' },
    { key: 'quick', name: 'Quick tasks', hint: 'Checks, props, campaign terms' },
    { key: 'research', name: 'Research', hint: 'Web search with sources' },
  ];

  protected readonly refreshing = signal(false);
  protected readonly models = signal<AiModels | null>(null);
  /** Tiers where you chose "Other…" to type a model id. */
  protected readonly custom = signal<Set<string>>(new Set());

  protected loadUsage(days: number): void {
    this.usageDays.set(days);
    this.refreshing.set(true);
    this.api.aiUsage(days).subscribe({
      next: (u) => {
        this.usage.set(u);
        this.refreshing.set(false);
      },
      error: () => this.refreshing.set(false),
    });
  }

  protected loadModels(refresh = false): void {
    this.api.aiModels(refresh).subscribe({ next: (m) => this.models.set(m), error: () => undefined });
  }

  protected model(form: AppSettings, tier: string): string {
    return (form as unknown as Record<string, string>)[`ai_${tier}_model`];
  }

  protected modelsFor(form: AppSettings, tier: string): AiModel[] {
    const provider = (form as unknown as Record<string, 'anthropic' | 'openai'>)[`ai_${tier}_provider`];
    return this.models()?.[provider]?.models ?? [];
  }

  protected listed(form: AppSettings, tier: string): boolean {
    const list = this.modelsFor(form, tier);
    return !list.length || list.some((m) => m.id === this.model(form, tier));
  }

  protected pickModel(form: AppSettings, tier: string, value: string): void {
    const custom = new Set(this.custom());
    if (value === '__other') {
      custom.add(tier);
    } else {
      custom.delete(tier);
      (form as unknown as Record<string, string>)[`ai_${tier}_model`] = value;
    }
    this.custom.set(custom);
  }

  protected kilo(n: number): string {
    return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n);
  }

  /** Switching provider proposes that provider's usual model for the tier. */
  protected setProvider(form: AppSettings, tier: string, provider: 'anthropic' | 'openai'): void {
    const defaults: Record<string, Record<string, string>> = {
      anthropic: { writing: 'claude-opus-5-5', quick: 'claude-haiku-5-5', research: 'claude-sonnet-5-5' },
      openai: { writing: 'gpt-6-astra', quick: 'gpt-6-luna', research: 'gpt-6.1-sol' },
    };
    const f = form as unknown as Record<string, string>;
    f[`ai_${tier}_provider`] = provider;
    f[`ai_${tier}_model`] = defaults[provider][tier];
    const custom = new Set(this.custom());
    custom.delete(tier);
    this.custom.set(custom);
  }
  protected readonly platformChoices = ['tiktok', 'instagram', 'youtube', 'x', 'facebook'];

  protected togglePlatform(form: AppSettings, platform: string): void {
    form.platforms = form.platforms.includes(platform)
      ? form.platforms.filter((p) => p !== platform)
      : [...form.platforms, platform];
  }
  protected readonly error = signal('');

  constructor() {
    this.loadUsage(30);
    this.loadModels();
    this.api.settings().subscribe({ next: (bundle) => this.apply(bundle), error: (err) => this.toasts.error(err) });
    this.api.voiceOptions().subscribe({ next: (v) => this.voices.set(v), error: () => undefined });
  }

  private apply(bundle: SettingsBundle): void {
    this.bundle.set(bundle);
    this.form.set({ ...bundle.settings });
  }

  protected reset(): void {
    const bundle = this.bundle();
    if (bundle) this.form.set({ ...bundle.defaults });
    this.toasts.ok('Defaults restored. Save to keep them.');
  }

  protected save(): void {
    const form = this.form();
    if (!form) return;
    this.saving.set(true);
    this.error.set('');
    this.api.saveSettings(form).subscribe({
      next: (bundle) => {
        this.apply(bundle);
        this.saving.set(false);
        this.toasts.ok('Settings saved');
        this.store.refresh();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }
}
