import numpy as np

from clipper.captions import group_words
from clipper.config import OUT_H, OUT_W
from clipper.reframe import SubtitleFix, compose, usable_height
from clipper.render import RenderOptions


def test_caption_groups_hold_at_most_three_words_and_break_on_punctuation():
    words = [{"w": w, "s": 10 + i * 0.3, "e": 10 + i * 0.3 + 0.25}
             for i, w in enumerate(["So", "here", "is", "the", "thing,", "really", "simple."])]
    groups = group_words(words, 10.0, 13.0)
    assert [[w["text"] for w in g["words"]] for g in groups] == [["SO", "HERE", "IS"], ["THE", "THING"],
                                                                 ["REALLY", "SIMPLE"]]
    assert groups[0]["start"] == 0.0                      # times are relative to the clip
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(groups, groups[1:], strict=False))


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
