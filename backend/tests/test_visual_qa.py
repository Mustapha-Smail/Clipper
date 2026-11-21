import numpy as np
from PIL import Image, ImageDraw

from clipper.settings import Settings
from clipper.studio import animate, visual_qa


def background(colour, textured=False):
    data = np.full((320, 180, 3), colour, np.uint8)
    if textured:
        rng = np.random.default_rng(4)
        noise = rng.normal(0, 6, data.shape[:2] + (1,))
        data = np.clip(data.astype(float) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(data, "RGB")


def foreground(image):
    image = image.copy()
    draw = ImageDraw.Draw(image)
    draw.ellipse((55, 90, 125, 230), fill=(220, 50, 40), outline=(10, 10, 10), width=5)
    return image


def test_light_dark_and_textured_backgrounds_need_actual_foreground():
    for colour, textured in (((245, 242, 232), False), ((35, 30, 28), False), ((105, 76, 52), True)):
        blank = visual_qa.measure(background(colour, textured))
        drawn = visual_qa.measure(foreground(background(colour, textured)))
        assert blank["meaningful"] is False
        assert drawn["meaningful"] is True


def feed(observer, samples, image):
    for n in range(samples):
        observer.observe(n / visual_qa.SAMPLE_HZ, image)


def test_a_third_of_a_second_is_ignored_and_half_a_second_is_reported():
    active = [{"s": 0, "e": 2}]
    short = visual_qa.Observer(active, 2)
    feed(short, 2, background((250, 250, 250)))
    short.observe(2 / visual_qa.SAMPLE_HZ, foreground(background((250, 250, 250))))
    assert short.finish()["empty_runs"] == []
    long = visual_qa.Observer(active, 2)
    feed(long, 3, background((250, 250, 250)))
    result = long.finish()
    assert result["empty_runs"][0]["duration_s"] == 0.5 and result["warnings"]


def test_first_event_and_film_tail_are_measured_only_during_the_voice():
    observer = visual_qa.Observer([{"s": 0, "e": 0.8}], 1.0)
    observer.observe(0, background((250, 250, 250)))
    observer.observe(1 / 6, foreground(background((250, 250, 250))))
    for n in range(8, 14):                       # an empty film tail after the one-second voice
        observer.observe(n / 6, background((250, 250, 250)))
    result = observer.finish()
    assert result["first_visual_event_s"] == 0.167 and result["empty_runs"] == []


def test_caption_pixels_are_not_seen_by_the_observer():
    board = {"format": "short", "start": 0.0, "end": 1.0,
             "words": [{"w": "Caption", "s": 0.0, "e": 0.8}],
             "scenes": [{"start_word": 0, "background": "paper", "camera": "still", "elements": []}]}
    composer = animate.Composer(board, {}, Settings(captions_enabled=True), None)
    observer = visual_qa.Observer(board["words"], 1.0)
    encoded = composer.bgr(0.0, observer)
    assert encoded.shape[:2] == (1920, 1080) and observer.finish()["first_visual_event_s"] is None


def test_bad_frame_data_never_raises_from_the_observer():
    observer = visual_qa.Observer([{"s": 0, "e": 1}], 1.0)
    observer.observe(0, object())
    result = observer.finish()
    assert result["problem"].startswith("Visual QA stopped") and result["warnings"] == []
