import numpy as np

from clipper import config
from clipper.captions import LAYOUTS, CaptionRenderer, group_words, hook_overlay
from clipper.config import FORMATS
from clipper.reframe import SubtitleFix, analyze, compose, crop_fraction, usable_height
from clipper.render import RenderOptions


def test_caption_groups_hold_at_most_three_words_and_break_on_punctuation():
    words = [{"w": w, "s": 10 + i * 0.3, "e": 10 + i * 0.3 + 0.25}
             for i, w in enumerate(["So", "here", "is", "the", "thing,", "really", "simple."])]
    groups = group_words(words, 10.0, 13.0)
    assert [[w["text"] for w in g["words"]] for g in groups] == [["SO", "HERE", "IS"], ["THE", "THING"],
                                                                 ["REALLY", "SIMPLE"]]
    assert groups[0]["start"] == 0.0                      # times are relative to the clip
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(groups, groups[1:], strict=False))


OUT_W, OUT_H = FORMATS["vertical"]
WIDE = FORMATS["horizontal"]


def frame(width=1920, height=1080):
    image = np.full((height, width, 3), 40, np.uint8)
    image[int(height * 0.8):int(height * 0.9)] = 255      # a bright "subtitle" band
    return image


def test_compose_always_returns_a_full_vertical_frame():
    for is_track in (True, False):
        for fix in (None, SubtitleFix("cover", (0.8, 0.9)), SubtitleFix("crop", (0.8, 0.9))):
            out = compose(frame(), is_track, 0.5, fix)
            assert out.shape == (OUT_H, OUT_W, 3) and out.dtype == np.uint8


def test_cover_blurs_the_band_and_crop_removes_it():
    untouched = compose(frame(), True, 0.5)
    covered = compose(frame(), True, 0.5, SubtitleFix("cover", (0.78, 0.92)))
    cropped = compose(frame(), True, 0.5, SubtitleFix("crop", (0.78, 0.92)))
    rows = slice(int(OUT_H * 0.82), int(OUT_H * 0.88))
    assert untouched[rows].mean() > 250                   # the band is plainly visible
    assert covered[rows].mean() < 215                     # blurred and dimmed
    assert cropped.max() < 60                             # nothing bright is left in frame


def test_crop_is_only_used_for_subtitles_near_the_bottom():
    base = dict(encoder="x264", bitrate_mbps=8, font_path="f", highlight=(0, 0, 0),
                captions_enabled=True, hook_seconds=4.0)
    low = RenderOptions(**base, subtitle_mode="crop", band=(0.8, 0.9)).subtitle_fix()
    high = RenderOptions(**base, subtitle_mode="crop", band=(0.4, 0.5)).subtitle_fix()
    assert (low.mode, high.mode) == ("crop", "cover")
    assert RenderOptions(**base, subtitle_mode="ignore", band=(0.8, 0.9)).subtitle_fix() is None
    assert RenderOptions(**base, subtitle_mode="cover", band=None).subtitle_fix() is None
    assert usable_height(1080, low) == 864 and usable_height(1080, None) == 1080


def test_a_landscape_source_fills_a_horizontal_clip_untouched():
    source = np.random.default_rng(1).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
    for is_track in (True, False):
        out = compose(source, is_track, 0.5, None, WIDE)
        assert out.shape == (1080, 1920, 3) and np.array_equal(out, source)
    # Nothing to choose between, so the face scan is skipped (the file is never opened).
    assert crop_fraction(1920, 1080, WIDE) == 1.0
    plan = analyze("no-such-file.mp4", 0, 30, 1920, 1080, size=WIDE)
    assert len(plan.track) == 0 and plan.at(12) == (False, 0.5)


def test_horizontal_clips_of_other_shapes_keep_the_whole_picture():
    for width, height in ((1440, 1080), (1080, 1920)):      # 4:3, and a phone video
        source = np.full((height, width, 3), 200, np.uint8)
        out = compose(source, False, 0.5, None, WIDE)
        assert out.shape == (1080, 1920, 3)
        shown = round(width * 1080 / height)
        middle = out[:, (1920 - shown) // 2 + 4:(1920 + shown) // 2 - 4]
        assert middle.min() > 190                         # the picture, top to bottom
        assert out[:, :8].max() < 150                     # blurred, darkened sides
    slightly_tall = compose(np.full((1200, 1920, 3), 200, np.uint8), False, 0.5, None, WIDE)   # 16:10
    assert slightly_tall.min() > 190                      # close enough: cropped, no bars


def test_subtitle_fixes_work_in_a_horizontal_clip():
    covered = compose(frame(), False, 0.5, SubtitleFix("cover", (0.78, 0.92)), WIDE)
    assert covered[int(1080 * 0.82):int(1080 * 0.88)].mean() < 215
    # Cropping the band leaves a wider picture: it is cropped to 16:9 around the face, or shown whole.
    fix = SubtitleFix("crop", (0.78, 0.92))
    assert crop_fraction(1920, usable_height(1080, fix), WIDE) < 1.0
    for is_track in (True, False):
        cropped = compose(frame(), is_track, 0.5, fix, WIDE)
        assert cropped.shape == (1080, 1920, 3) and cropped.max() < 60


def test_text_fits_inside_both_formats():
    font = str(sorted(config.FONTS.glob("*.ttf"))[0])
    words = [{"text": "EXTRAORDINARILY", "s": 0.0, "e": 0.4}, {"text": "LONG", "s": 0.4, "e": 0.8}]
    for fmt, (width, height) in FORMATS.items():
        layout = LAYOUTS[fmt]
        assert (layout.width, layout.height) == (width, height)
        canvas = np.zeros((height, width, 3), np.uint8)
        CaptionRenderer([{"words": words, "start": 0.0, "end": 1.0}], font, fmt=fmt).draw(canvas, 0.1)
        rows = np.flatnonzero(canvas.any(axis=(1, 2)))
        assert height * 0.5 < rows.min() and rows.max() < height      # lower half, inside the frame
        hook = hook_overlay("Why nobody tells you this about pricing your product", font, fmt)
        assert hook.x >= 0 and hook.x + hook.w <= width and hook.y + hook.h < height * 0.4


def test_render_options_take_the_format_from_the_video_or_the_settings():
    from clipper.settings import Settings

    assert RenderOptions.from_settings(Settings()).format == "vertical"
    assert RenderOptions.from_settings(Settings(clip_format="horizontal")).format == "horizontal"
    assert RenderOptions.from_settings(Settings(), clip_format="horizontal").format == "horizontal"
