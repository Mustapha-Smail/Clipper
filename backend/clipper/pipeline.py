"""The pipeline stages. The worker and the command line both call these.

Each stage reads what it needs from the database, does its (long) work with no
transaction open, then writes the result back. Progress and log lines go to the
reporter it is given.
"""

from pathlib import Path

from sqlalchemy import select as sql_select

from . import compliance, llm, subtitles
from . import ingest as ingest_mod
from . import moments as moments_mod
from . import transcribe as transcribe_mod
from .db import session_scope
from .db.models import Campaign, Clip, Moment, Video, utcnow
from .ffutil import ToolError
from .jobs import Reporter
from .settings import Settings, clips_dir, resolve_whisper_engine

SUBTITLE_MODES = ("cover", "crop", "keep", "ignore")


def _video(db, video_id: str) -> Video:
    video = db.get(Video, video_id)
    if video is None:
        raise ToolError(f"Video '{video_id}' not found")
    return video


def remove_clip_files(path: str) -> None:
    clip = Path(path)
    clip.unlink(missing_ok=True)
    clip.with_suffix(".json").unlink(missing_ok=True)


def effective_subtitle_mode(video: Video, settings: Settings) -> str:
    """The handling a render will apply: the video's own choice, else the default
    when subtitles were found, else nothing."""
    if video.subtitle_mode:
        return video.subtitle_mode
    if video.subtitles and video.subtitles.get("detected"):
        return settings.subtitle_mode
    return "ignore"


# --- stages -------------------------------------------------------------------

def ingest(src: str, settings: Settings, rep: Reporter, move: bool = False,
           brief: str | None = None, campaign_id: int | None = None) -> str:
    meta = ingest_mod.ingest(src, settings, progress=rep.progress, info=rep.info, move=move)
    with session_scope() as db:
        video = db.get(Video, meta["id"])
        if video is None:
            video = Video(**meta)
            db.add(video)
        else:
            for key, value in meta.items():
                setattr(video, key, value)
        if brief and brief.strip():
            video.brief = brief.strip()
        if campaign_id is not None and db.get(Campaign, campaign_id) is not None:
            video.campaign_id = campaign_id
    rep.set_video(meta["id"], meta["title"])
    rep.info(f"{meta['title']} ({meta['duration'] / 60:.1f} min, {meta['width']}x{meta['height']})")
    return meta["id"]


def transcribe(video_id: str, settings: Settings, rep: Reporter,
               force: bool = False, isolate: bool = False) -> None:
    src_dir = ingest_mod.source_dir(video_id)
    engine = resolve_whisper_engine(settings)
    if transcribe_mod.transcript_path(src_dir).exists() and not force:
        rep.info("A transcript already exists; reusing it")
    else:
        rep.info(f"Speech engine: {engine}, model: {settings.whisper_model}")
    worker = transcribe_mod.transcribe_isolated if isolate else transcribe_mod.transcribe
    transcript = worker(src_dir, settings.language, engine, settings.whisper_model,
                        force=force, progress=rep.progress)
    rep.info(f"Transcript has {len(transcript['words'])} words")


def detect_subtitles(video_id: str, rep: Reporter, force: bool = False) -> dict:
    with session_scope() as db:
        known = _video(db, video_id).subtitles
    if known is not None and not force:
        rep.info("Subtitle detection was already done; reusing the result")
        return known
    src_dir = ingest_mod.source_dir(video_id)
    words = transcribe_mod.load_transcript(src_dir)["words"]
    result = subtitles.detect(ingest_mod.source_video(src_dir), words, progress=rep.progress)
    with session_scope() as db:
        _video(db, video_id).subtitles = result
    if result["detected"]:
        top, bottom = result["band"]
        rep.info(f"Found {result['kind']} captions in {result['hit_rate']:.0%} of sampled frames, "
                 f"between {top:.0%} and {bottom:.0%} of the frame height")
    else:
        rep.info("No burned-in subtitles found")
    return result


def select(video_id: str, settings: Settings, rep: Reporter, n: int | None = None,
           min_len: int | None = None, max_len: int | None = None) -> int:
    """Ask Claude for moments. Moments whose clip is approved are kept; the rest
    are replaced. Returns how many new moments were added."""
    n, min_len, max_len = n or settings.clips_per_video, min_len or settings.min_len, max_len or settings.max_len
    with session_scope() as db:
        video = _video(db, video_id)
        title, duration, brief = video.title, video.duration, video.brief
        kept = [(m.start_s, m.end_s) for m in video.moments if m.clip and m.clip.status == "approved"]

    words = transcribe_mod.load_transcript(ingest_mod.source_dir(video_id))["words"]
    lines = moments_mod.build_lines(words)
    if not lines:
        raise ToolError("The transcript is empty: no speech was detected in this video.")
    wanted = f"{n} moment{'s' if n != 1 else ''} of {min_len}-{max_len}s"
    rep.info(f"Asking {llm.route('moments')[2]} for up to {wanted}"
             + (" with the campaign brief" if brief.strip() else ""))
    picks = moments_mod.ask_claude(lines, title, n, min_len, max_len, brief.strip() or None)
    found, warnings = moments_mod.picks_to_moments(picks, lines, duration, n, min_len, max_len, taken=kept)
    for warning in warnings:
        rep.warn(warning)

    with session_scope() as db:
        video = _video(db, video_id)
        position = 0
        for moment in list(video.moments):
            if moment.clip and moment.clip.status == "approved":
                position += 1
                moment.position = position
            else:
                if moment.clip:
                    remove_clip_files(moment.clip.path)
                video.moments.remove(moment)
        for item in found:
            position += 1
            video.moments.append(Moment(
                position=position, start_s=item["start"], end_s=item["end"], title=item["title"],
                hook=item["hook"], caption=item["caption"], score=item["score"], reason=item["reason"]))
    chosen = f"Chose {len(found)} moment{'s' if len(found) != 1 else ''}"
    rep.info(chosen + (f"; kept {len(kept)} with approved clips" if kept else ""))
    return len(found)


def render(video_id: str, settings: Settings, rep: Reporter,
           moment_ids: list[int] | None = None) -> list[int]:
    """Render moments (all, or the given ids). Returns the ids of the clips made."""
    from .render import RenderOptions, clip_name, render_clip, write_sidecar

    with session_scope() as db:
        video = _video(db, video_id)
        meta = {"id": video.id, "url": video.url, "title": video.title}
        todo = [{"id": m.id, "start": m.start_s, "end": m.end_s, "title": m.title,
                 "hook": m.hook, "caption": m.caption}
                for m in video.moments if not moment_ids or m.id in moment_ids]
        mode = effective_subtitle_mode(video, settings)
        detected = bool(video.subtitles and video.subtitles.get("detected"))
        band = tuple(video.subtitles["band"]) if detected else None
    if not todo:
        raise ToolError("There is nothing to render: choose moments first.")

    src_dir = ingest_mod.source_dir(video_id)
    source = ingest_mod.source_video(src_dir)
    words = transcribe_mod.load_transcript(src_dir)["words"]
    opts = RenderOptions.from_settings(settings, mode, band)
    out_dir = clips_dir(settings) / video_id
    fix = opts.subtitle_fix()
    rep.info(f"Encoder: {opts.encoder} at {opts.bitrate_mbps} Mbps; source subtitles: "
             + ("keep original" if mode == "keep" else fix.mode if fix else "none to handle"))

    clip_ids = []
    for done, moment in enumerate(todo):
        label = f"Rendering clip {done + 1} of {len(todo)}"
        rep.progress(done / len(todo), label)
        out_path = out_dir / f"{clip_name(moment['id'], moment['title'])}.mp4"
        try:
            stats = render_clip(source, moment, words, out_path, opts,
                                progress=lambda f, d=done, text=label: rep.progress((d + f) / len(todo), text))
        except BaseException:
            out_path.with_name(out_path.stem + ".partial.mp4").unlink(missing_ok=True)
            raise
        write_sidecar(out_path, moment, meta, stats)

        with session_scope() as db:
            clip = db.scalar(sql_select(Clip).where(Clip.moment_id == moment["id"]))
            if clip is not None and clip.path != str(out_path):
                remove_clip_files(clip.path)   # the title changed, so the file name did too
            if clip is None:
                clip = Clip(video_id=video_id, moment_id=moment["id"], path=str(out_path),
                            seconds=0, size_bytes=0)
                db.add(clip)
            clip.path = str(out_path)
            clip.status, clip.compliance, clip.rendered_at = "pending", None, utcnow()
            clip.seconds, clip.size_bytes = stats["seconds"], out_path.stat().st_size
            clip.tracked_share = stats["tracked_share"]
            clip.captions_burned, clip.subtitle_fix = stats["captions_burned"], stats["subtitle_fix"]
            db.flush()
            clip_ids.append(clip.id)
        rep.info(f"Rendered '{moment['title']}': {stats['seconds']:.0f}s, "
                 f"{out_path.stat().st_size / 1e6:.0f} MB, face-tracked {stats['tracked_share']:.0%} of the time")

    if settings.delete_source_after_render and not moment_ids:
        source.unlink(missing_ok=True)
        rep.info("Deleted the source video file, as set in Settings")
    return clip_ids


def check(video_id: str, settings: Settings, rep: Reporter, clip_ids: list[int] | None = None,
          skip_without_brief: bool = False) -> None:
    """Check clips against the video's campaign brief and store the result on each."""
    with session_scope() as db:
        video = _video(db, video_id)
        brief = video.brief.strip()
        todo = [{"id": c.id, "path": c.path, "seconds": c.seconds, "title": c.moment.title,
                 "hook": c.moment.hook, "caption": c.moment.caption, "start": c.moment.start_s,
                 "end": c.moment.end_s, "captions_burned": c.captions_burned}
                for c in video.clips if not clip_ids or c.id in clip_ids]
    if not brief:
        if skip_without_brief:
            rep.info("This video has no campaign brief: nothing to check")
            return
        raise ToolError("Add a campaign brief to this video first.")
    if not todo:
        raise ToolError("There are no rendered clips to check.")

    words = transcribe_mod.load_transcript(ingest_mod.source_dir(video_id))["words"]
    for done, clip in enumerate(todo):
        rep.progress(done / len(todo), f"Checking clip {done + 1} of {len(todo)}")
        spoken = " ".join(w["w"] for w in words if clip["start"] - 0.05 <= w["s"] and w["e"] <= clip["end"] + 0.3)
        result = compliance.check_clip(Path(clip["path"]), clip, spoken, brief,
                                       clip["captions_burned"], settings.hook_seconds)
        with session_scope() as db:
            row = db.get(Clip, clip["id"])
            if row is not None:
                row.compliance = result
        failed = sum(1 for c in result["checks"] if c["status"] == "fail")
        outcome = {"pass": "passes the brief", "review": "needs a manual check",
                   "fail": f"breaks {failed} requirement{'s' if failed != 1 else ''}"}[result["verdict"]]
        (rep.warn if result["verdict"] == "fail" else rep.info)(f"'{clip['title']}' {outcome}")


# --- edits made by a person ------------------------------------------------------

def save_moments(db, video: Video, items: list[dict]) -> None:
    """Apply a hand-edited moment list: update, add (no id) and remove (absent)."""
    existing = {m.id: m for m in video.moments}
    seen = set()
    for position, item in enumerate(items, 1):
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError) as err:
            raise ToolError(f"Clip {position}: start and end must be times") from err
        if start < 0 or end <= start:
            raise ToolError(f"Clip {position}: the end must come after the start")
        if end > video.duration + 0.5:
            raise ToolError(f"Clip {position}: it ends after the video does ({video.duration:.0f}s)")
        moment = existing.get(item.get("id"))
        if moment is None:
            moment = Moment(title="", hook="", caption="", reason="")
            video.moments.append(moment)
        else:
            seen.add(moment.id)
        moment.position = position
        moment.start_s, moment.end_s = round(start, 2), round(min(end, video.duration), 2)
        moment.title = str(item.get("title") or "").strip() or f"Clip {position}"
        moment.hook = str(item.get("hook") or "").strip()
        moment.caption = str(item.get("caption") or "").strip()
    for moment_id, moment in existing.items():
        if moment_id not in seen:
            if moment.clip:
                remove_clip_files(moment.clip.path)
            video.moments.remove(moment)


def delete_video(db, video: Video) -> None:
    """Remove a video, its working files and every clip rendered from it."""
    import shutil

    for clip in video.clips:
        remove_clip_files(clip.path)
    shutil.rmtree(ingest_mod.source_dir(video.id), ignore_errors=True)
    db.delete(video)
