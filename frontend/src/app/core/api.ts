import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ActivityEvent, AppSettings, Campaign, CampaignPatch, Clip, ClipListItem, ClipStatus, Idea, IdeaPatch, Job, JobEvent,
  MomentInput, NewVideo, Script, ScriptPatch, ScriptSection, ScriptSummary, VoiceOptions, VoiceTake, Asset, Chapters, MusicFound, MusicTrack, StoryScene, Storyboard, StoryboardListItem, StoryboardSummary,
  UsageReport, MusicSuggestion, AiModels, CostOf, CostReport, ArchivePhoto, InkLook, PublishCheck, YoutubeText,
  SettingsBundle, SubtitleMode, SystemStatus, VideoDetail, VideoSummary,
} from './models';

const BASE = '/api/v1';

/** Turns any failed request into one sentence fit to show a person. */
export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    if (err.status === 0) return 'Cannot reach the server. Is it running?';
    const detail = err.error?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ');
    return `Request failed (${err.status})`;
  }
  return err instanceof Error ? err.message : 'Something went wrong';
}

@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);

  status(): Observable<SystemStatus> {
    return this.http.get<SystemStatus>(`${BASE}/system/status`);
  }

  // Videos
  videos(): Observable<VideoSummary[]> {
    return this.http.get<VideoSummary[]>(`${BASE}/videos`);
  }
  video(id: string): Observable<VideoDetail> {
    return this.http.get<VideoDetail>(`${BASE}/videos/${encodeURIComponent(id)}`);
  }
  addVideo(body: NewVideo): Observable<Job> {
    return this.http.post<Job>(`${BASE}/videos`, body);
  }
  /** Emits upload progress (0..1) and finally the server path of the uploaded file. */
  upload(file: File): Observable<{ progress: number; path?: string }> {
    return new Observable((observer) => {
      const form = new FormData();
      form.append('file', file);
      const xhr = new XMLHttpRequest();
      xhr.open('POST', `${BASE}/videos/upload`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) observer.next({ progress: e.loaded / e.total });
      };
      xhr.onload = () => {
        const body = JSON.parse(xhr.responseText || '{}');
        if (xhr.status >= 400) {
          observer.error(new Error(body.detail || 'Upload failed'));
        } else {
          observer.next({ progress: 1, path: body.path });
          observer.complete();
        }
      };
      xhr.onerror = () => observer.error(new Error('Upload failed: cannot reach the server'));
      xhr.send(form);
      return () => xhr.abort();
    });
  }
  updateVideo(id: string, patch: {
    brief?: string; subtitle_mode?: SubtitleMode | 'default'; campaign_id?: number | null; use_campaign_brief?: boolean;
  }): Observable<VideoDetail> {
    return this.http.patch<VideoDetail>(`${BASE}/videos/${encodeURIComponent(id)}`, patch);
  }
  saveMoments(id: string, moments: MomentInput[]): Observable<VideoDetail> {
    return this.http.put<VideoDetail>(`${BASE}/videos/${encodeURIComponent(id)}/moments`, { moments });
  }
  deleteVideo(id: string): Observable<void> {
    return this.http.delete<void>(`${BASE}/videos/${encodeURIComponent(id)}`);
  }
  deleteSourceFile(id: string): Observable<void> {
    return this.http.delete<void>(`${BASE}/videos/${encodeURIComponent(id)}/source-file`);
  }
  select(id: string, body: { n?: number; min_len?: number; max_len?: number }): Observable<Job> {
    return this.http.post<Job>(`${BASE}/videos/${encodeURIComponent(id)}/select`, body);
  }
  render(id: string, momentIds: number[] | null): Observable<Job> {
    return this.http.post<Job>(`${BASE}/videos/${encodeURIComponent(id)}/render`, { moment_ids: momentIds });
  }
  check(id: string, clipIds: number[] | null): Observable<Job> {
    return this.http.post<Job>(`${BASE}/videos/${encodeURIComponent(id)}/check`, { clip_ids: clipIds });
  }
  detectSubtitles(id: string): Observable<Job> {
    return this.http.post<Job>(`${BASE}/videos/${encodeURIComponent(id)}/detect-subtitles`, {});
  }

  // Campaigns
  campaigns(): Observable<Campaign[]> {
    return this.http.get<Campaign[]>(`${BASE}/campaigns`);
  }
  readCampaign(text: string, url: string | null): Observable<Campaign> {
    return this.http.post<Campaign>(`${BASE}/campaigns`, { text, url });
  }
  updateCampaign(id: number, patch: CampaignPatch): Observable<Campaign> {
    return this.http.patch<Campaign>(`${BASE}/campaigns/${id}`, patch);
  }
  deleteCampaign(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/campaigns/${id}`);
  }

  // Studio ideas
  ideas(): Observable<Idea[]> {
    return this.http.get<Idea[]>(`${BASE}/ideas`);
  }
  generateIdeas(count: number, theme: string): Observable<Idea[]> {
    return this.http.post<Idea[]>(`${BASE}/ideas/generate`, { count, theme });
  }
  suggestThemes(hint: string): Observable<{ theme: string; why: string; example: string }[]> {
    return this.http.post<{ theme: string; why: string; example: string }[]>(`${BASE}/ideas/themes`, { hint });
  }
  addIdea(text: string): Observable<Idea> {
    return this.http.post<Idea>(`${BASE}/ideas`, { text });
  }
  validateIdea(id: number): Observable<Idea> {
    return this.http.post<Idea>(`${BASE}/ideas/${id}/validate`, {});
  }
  updateIdea(id: number, patch: IdeaPatch): Observable<Idea> {
    return this.http.patch<Idea>(`${BASE}/ideas/${id}`, patch);
  }
  deleteIdea(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/ideas/${id}`);
  }

  // Studio scripts
  startScript(ideaId: number, video: 'long' | 'short', length: number): Observable<Script> {
    const body = video === 'short' ? { video, target_seconds: length } : { video, target_minutes: length };
    return this.http.post<Script>(`${BASE}/ideas/${ideaId}/script`, body);
  }
  scripts(): Observable<ScriptSummary[]> {
    return this.http.get<ScriptSummary[]>(`${BASE}/scripts`);
  }
  script(id: number): Observable<Script> {
    return this.http.get<Script>(`${BASE}/scripts/${id}`);
  }
  updateScript(id: number, patch: ScriptPatch): Observable<Script> {
    return this.http.patch<Script>(`${BASE}/scripts/${id}`, patch);
  }
  saveSections(id: number, sections: ScriptSection[]): Observable<Script> {
    return this.http.put<Script>(`${BASE}/scripts/${id}/sections`, { sections });
  }
  rewriteSection(id: number, key: string, instruction: string): Observable<{ key: string; text: string }> {
    return this.http.post<{ key: string; text: string }>(`${BASE}/scripts/${id}/rewrite`, { key, instruction });
  }
  factcheck(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/scripts/${id}/factcheck`, {});
  }
  makeSpoken(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/scripts/${id}/spoken`, {});
  }
  undoRewrite(id: number): Observable<Script> {
    return this.http.post<Script>(`${BASE}/scripts/${id}/undo-rewrite`, {});
  }
  redraft(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/scripts/${id}/redraft`, {});
  }
  deleteScript(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/scripts/${id}`);
  }
  scriptTextUrl(id: number): string {
    return `${BASE}/scripts/${id}/text`;
  }

  // Studio voice
  voiceOptions(): Observable<VoiceOptions> {
    return this.http.get<VoiceOptions>(`${BASE}/voices`);
  }
  takes(scriptId: number): Observable<VoiceTake[]> {
    return this.http.get<VoiceTake[]>(`${BASE}/scripts/${scriptId}/takes`);
  }
  createAiTake(scriptId: number, engine: string, voice: string, speed: number, pauses: string): Observable<VoiceTake> {
    return this.http.post<VoiceTake>(`${BASE}/scripts/${scriptId}/takes/ai`, { engine, voice, speed, pauses });
  }
  uploadRecording(scriptId: number, file: Blob, name: string): Observable<VoiceTake> {
    const form = new FormData();
    form.append('file', file, name);
    return this.http.post<VoiceTake>(`${BASE}/scripts/${scriptId}/takes/recording`, form);
  }
  chooseTake(id: number): Observable<VoiceTake> {
    return this.http.post<VoiceTake>(`${BASE}/takes/${id}/choose`, {});
  }
  realignTake(id: number): Observable<VoiceTake> {
    return this.http.post<VoiceTake>(`${BASE}/takes/${id}/align`, {});
  }
  deleteTake(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/takes/${id}`);
  }
  takeAudioUrl(take: VoiceTake): string {
    return `${BASE}/takes/${take.id}/audio?v=${take.duration ?? 0}`;
  }

  // Studio storyboards and props
  createStoryboard(scriptId: number, sectionKey: string | null, format: 'short' | 'long' = 'short',
                   style: 'ink' | 'flat' = 'ink', look: InkLook = 'ink'): Observable<Storyboard> {
    return this.http.post<Storyboard>(`${BASE}/scripts/${scriptId}/storyboards`,
                                      { format, section_key: sectionKey, style, look });
  }
  storyboards(scriptId: number): Observable<StoryboardSummary[]> {
    return this.http.get<StoryboardSummary[]>(`${BASE}/scripts/${scriptId}/storyboards`);
  }
  storyboard(id: number): Observable<Storyboard> {
    return this.http.get<Storyboard>(`${BASE}/storyboards/${id}`);
  }
  saveScenes(id: number, scenes: StoryScene[]): Observable<Storyboard> {
    return this.http.put<Storyboard>(`${BASE}/storyboards/${id}/scenes`, { scenes });
  }
  replan(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/storyboards/${id}/replan`, {});
  }
  renderStoryboard(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/storyboards/${id}/render`, {});
  }
  reviewStoryboard(id: number, review: 'pending' | 'approved' | 'rejected'): Observable<Storyboard> {
    return this.http.patch<Storyboard>(`${BASE}/storyboards/${id}`, { review });
  }
  setLook(id: number, look: InkLook): Observable<Storyboard> {
    return this.http.patch<Storyboard>(`${BASE}/storyboards/${id}`, { look });
  }
  publishCheck(id: number): Observable<PublishCheck> {
    return this.http.get<PublishCheck>(`${BASE}/storyboards/${id}/publish-check`);
  }
  writeYoutubeText(id: number): Observable<YoutubeText> {
    return this.http.post<YoutubeText>(`${BASE}/storyboards/${id}/youtube`, {});
  }
  saveYoutubeText(id: number, text: { title: string; description: string; tags: string[] }): Observable<YoutubeText> {
    return this.http.put<YoutubeText>(`${BASE}/storyboards/${id}/youtube`, text);
  }
  authorshipUrl(id: number): string {
    return `${BASE}/storyboards/${id}/authorship.txt`;
  }
  soundSettings(id: number, patch: { music_id?: number | null; music_db?: number; effects?: boolean;
                                     music_suggested?: boolean }): Observable<Storyboard> {
    return this.http.patch<Storyboard>(`${BASE}/storyboards/${id}`, patch);
  }
  chapters(id: number): Observable<Chapters> {
    return this.http.get<Chapters>(`${BASE}/storyboards/${id}/chapters`);
  }
  subtitlesUrl(id: number): string {
    return `${BASE}/storyboards/${id}/subtitles.srt`;
  }
  music(): Observable<MusicTrack[]> {
    return this.http.get<MusicTrack[]>(`${BASE}/music`);
  }
  uploadMusic(file: File): Observable<MusicTrack> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http.post<MusicTrack>(`${BASE}/music`, form);
  }
  deleteMusic(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/music/${id}`);
  }
  searchMusic(q: string, length: string): Observable<{ count: number; results: MusicFound[] }> {
    return this.http.get<{ count: number; results: MusicFound[] }>(`${BASE}/music/search`, { params: { q, length } });
  }
  importMusic(openverseId: string): Observable<MusicTrack> {
    return this.http.post<MusicTrack>(`${BASE}/music/import`, { openverse_id: openverseId });
  }
  /** A few seconds of `text` in a Kokoro voice, as a playable object URL. */
  voiceSample(voice: string, speed: number, text: string, pauses: string): Observable<Blob> {
    return this.http.post(`${BASE}/voices/sample`, { voice, speed, text, pauses }, { responseType: 'blob' });
  }
  suggestMusic(storyboardId: number, online: boolean): Observable<MusicSuggestion> {
    return this.http.post<MusicSuggestion>(`${BASE}/storyboards/${storyboardId}/suggest-music`, { online });
  }
  soundPreviewUrl(storyboardId: number, musicId: number | null, musicDb: number, effects: boolean, start = 0,
                  openverseId: string | null = null): string {
    const music = openverseId ? `&openverse_id=${openverseId}` : musicId === null ? '' : `&music_id=${musicId}`;
    return `${BASE}/storyboards/${storyboardId}/sound-preview?music_db=${musicDb}&effects=${effects}&start=${start}${music}&t=${Date.now()}`;
  }
  aiModels(refresh = false): Observable<AiModels> {
    return this.http.get<AiModels>(`${BASE}/system/ai-models`, { params: { refresh } });
  }
  allStoryboards(): Observable<StoryboardListItem[]> {
    return this.http.get<StoryboardListItem[]>(`${BASE}/storyboards`);
  }
  photoImageUrl(id: number, version: string | number, size = 320): string {
    return `${BASE}/photos/${id}/image?size=${size}&v=${version}`;
  }
  nextPhoto(id: number, storyboardId: number): Observable<ArchivePhoto> {
    return this.http.post<ArchivePhoto>(`${BASE}/photos/${id}/next`, {}, { params: { storyboard_id: storyboardId } });
  }
  costs(of: CostOf): Observable<CostReport> {
    const params: Record<string, number | string> = {};
    if (of.storyboard) params['storyboard_id'] = of.storyboard;
    else if (of.script) params['script_id'] = of.script;
    else if (of.video) params['video_id'] = of.video;
    return this.http.get<CostReport>(`${BASE}/system/costs`, { params });
  }
  aiUsage(days: number): Observable<UsageReport> {
    return this.http.get<UsageReport>(`${BASE}/system/ai-usage`, { params: { days } });
  }
  musicAudioUrl(id: number): string {
    return `${BASE}/music/${id}/audio`;
  }
  deleteStoryboard(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/storyboards/${id}`);
  }
  stillUrl(id: number, t: number, version: string | number, width = 270): string {
    return `${BASE}/storyboards/${id}/still?t=${t.toFixed(2)}&width=${width}&v=${version}`;
  }
  storyboardVideoUrl(board: StoryboardSummary): string {
    return `${BASE}/storyboards/${board.id}/video?v=${encodeURIComponent(board.rendered_at ?? '')}`;
  }
  assets(): Observable<Asset[]> {
    return this.http.get<Asset[]>(`${BASE}/assets`);
  }
  assetImageUrl(name: string, version: string, size = 256): string {
    return `${BASE}/assets/${name}/image?size=${size}&v=${encodeURIComponent(version)}`;
  }
  castUrl(variant: string, pose: string, expression: string, height = 200): string {
    return `${BASE}/assets/cast.png?variant=${variant}&pose=${pose}&expression=${expression}&height=${height}`;
  }
  redrawAsset(name: string, instruction: string, description?: string): Observable<Asset> {
    return this.http.post<Asset>(`${BASE}/assets/${name}/redraw`, { instruction, description });
  }
  deleteAsset(name: string): Observable<void> {
    return this.http.delete<void>(`${BASE}/assets/${name}`);
  }

  // Clips
  clips(status?: ClipStatus): Observable<ClipListItem[]> {
    return this.http.get<ClipListItem[]>(`${BASE}/clips`, { params: status ? { status } : {} });
  }
  reviewClip(id: number, status: ClipStatus): Observable<Clip> {
    return this.http.patch<Clip>(`${BASE}/clips/${id}`, { status });
  }
  deleteClip(id: number): Observable<void> {
    return this.http.delete<void>(`${BASE}/clips/${id}`);
  }
  clipUrl(clip: Clip, download = false): string {
    const version = encodeURIComponent(clip.rendered_at);
    return `${BASE}/clips/${clip.id}/file?v=${version}${download ? '&download=true' : ''}`;
  }

  // Jobs and activity
  jobs(limit = 40): Observable<Job[]> {
    return this.http.get<Job[]>(`${BASE}/jobs`, { params: { limit } });
  }
  jobEvents(id: number, after = 0): Observable<JobEvent[]> {
    return this.http.get<JobEvent[]>(`${BASE}/jobs/${id}/events`, { params: { after } });
  }
  cancelJob(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/jobs/${id}/cancel`, {});
  }
  retryJob(id: number): Observable<Job> {
    return this.http.post<Job>(`${BASE}/jobs/${id}/retry`, {});
  }
  activity(level?: string): Observable<ActivityEvent[]> {
    return this.http.get<ActivityEvent[]>(`${BASE}/activity`, { params: level ? { level } : {} });
  }

  // Settings
  settings(): Observable<SettingsBundle> {
    return this.http.get<SettingsBundle>(`${BASE}/settings`);
  }
  saveSettings(settings: AppSettings): Observable<SettingsBundle> {
    return this.http.put<SettingsBundle>(`${BASE}/settings`, settings);
  }
}
