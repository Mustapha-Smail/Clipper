"""Archive photos: search filters, credits, storage, the language, the stage and the API."""

import io
import urllib.error

import numpy as np
import pytest
from PIL import Image

from clipper.db import session_scope
from clipper.db.models import Script, Storyboard, StudioPhoto
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import beats, photos, stage
from clipper.studio import storyboard as sb


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(photos, "GAP", 0)


def page(index, pageid, title, license_name, width=1600, height=1200, mime="image/jpeg", desc=""):
    return {"index": index, "pageid": pageid, "title": f"File:{title}.jpg", "imageinfo": [{
        "mime": mime, "width": width, "height": height, "url": f"https://x/{pageid}.jpg",
        "thumburl": f"https://x/thumb/{pageid}.jpg", "descriptionurl": f"https://commons/{pageid}",
        "extmetadata": {"LicenseShortName": {"value": license_name}, "Artist": {"value": "<a>Lavinia Fontana</a>"},
                        "ImageDescription": {"value": desc}, "LicenseUrl": {"value": "https://cc/by/4.0"}}}]}


ANSWER = {"query": {"pages": [
    page(1, 11, "Portrait of Pope Gregory XIII", "Public domain"),
    page(2, 12, "Pope Gregory XIII, engraving", "CC BY-SA 4.0"),                 # share-alike: left out
    page(3, 13, "Gregory XIII tiny", "Public domain", width=300, height=200),     # too small
    page(4, 14, "Some other pope", "CC BY 4.0", desc="A portrait"),                # not about Gregory XIII
    page(5, 15, "Gregory XIII in his study", "CC BY 4.0"),
]}}


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1200, 900), (120, 80, 40)).save(out, "JPEG")
    return out.getvalue()


def test_only_safe_relevant_pictures_are_kept(monkeypatch):
    monkeypatch.setattr(photos, "_get", lambda params: ANSWER)
    found = photos.search("Pope Gregory XIII portrait")
    assert [f["key"] for f in found] == ["11", "15"]
    assert found[0]["creator"] == "Lavinia Fontana"
    assert "public domain, via Wikimedia Commons" in photos.credit(found[0])
    assert "licensed under CC BY 4.0 (https://cc/by/4.0)" in photos.credit(found[1])
    for name, ok in (("Public domain", True), ("CC0", True), ("CC BY 4.0", True), ("CC BY-SA 3.0", False),
                     ("CC BY-NC 2.0", False), ("CC BY-ND 4.0", False), ("GFDL", False)):
        assert photos.allowed(name) is ok, name


def test_a_search_is_resolved_once_and_can_move_on(app_env, monkeypatch):
    monkeypatch.setattr(photos, "_get", lambda params: ANSWER)

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(photos.urllib.request, "urlopen", lambda request, timeout=0: Response(jpeg()))
    first = photos.find("Pope Gregory XIII portrait")
    assert first.key == "11" and first.rank == 0 and first.width == 1200
    again = photos.find("Pope Gregory XIII portrait", first.rank + 1)
    assert again.key == "15" and again.rank == 1
    with session_scope() as db:
        assert db.query(StudioPhoto).count() == 1                  # one row per search


def test_photos_in_the_language_and_on_the_page(tmp_path):
    line = beats.parse_line('4 photo "Pope Gregory XIII portrait" right large as pope')
    assert line.render() == '4 photo "Pope Gregory XIII portrait" right large as pope' and line.id == "pope"
    assert beats.parse_line("5 photo").__class__ is type(None)
    path = tmp_path / "p.jpg"
    Image.new("RGB", (600, 900), (90, 60, 30)).save(path)
    taped = photos.photo_image(str(path), "x", 300, 450, 1.0, 2.0, 1, False)
    assert taped.width > 300 and taped.getbbox() is not None
    full = photos.photo_image(str(path), "x", 640, 360, 1.0, 2.0, 1, True)
    assert full.size == (640, 360)
    missing = photos.photo_image(None, "Hagia Sophia", 320, 240, 1.0, 0.0, 1, False)
    assert np.asarray(missing)[..., 3].sum() > 0                    # a paper card with the search
    words = [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.4, "section": "a"} for i in range(12)]
    shots = beats.parse_plan('0 shot "x"\n0 actor narrator left\n1 photo "Pope portrait" right large as pope\n'
                             '3 circle pope\n6 shot "y" cut\n6 photo "Pope portrait" full', 12)
    board = {"format": "long", "words": words, "start": 0, "end": 6, "scenes": shots, "headings": {},
             "photos": {"Pope portrait": {"path": str(path), "width": 600, "height": 900}}}
    composer = stage.InkComposer(board, {}, Settings(), None)
    assert beats.photo_queries(shots) == ["Pope portrait"]
    assert composer.bgr(2.0).shape == (1080, 1920, 3) and composer.bgr(4.5).shape == (1080, 1920, 3)
    assert any(k == "glide" for _, k in composer.sound_events())


def test_the_draw_step_finds_photos_and_the_page_lists_their_credits(client, app_env, monkeypatch):
    with session_scope() as db:
        script = Script(title="Calendar", sections=[{"key": "a", "heading": "A", "text": "x", "short": False}])
        db.add(script)
        db.flush()
        shots = beats.parse_plan('0 shot "x"\n1 photo "Pope portrait" right\n2 photo "Nothing at all" left', 10)
        board = Storyboard(script_id=script.id, format="long", style="ink", title="Calendar",
                           words=[{"w": "w", "s": i * 0.5, "e": i * 0.5 + 0.4, "section": "a"} for i in range(10)],
                           scenes=shots, start=0, end=5)
        db.add(board)
        db.flush()
        board_id = board.id

    def fake_find(query, rank=0):
        if query != "Pope portrait":
            return None
        with session_scope() as db:
            row = StudioPhoto(query=query, rank=rank, key="11", title="Portrait", creator="Fontana",
                              license="Public domain", landing_url="https://commons/11",
                              credit="\"Portrait\" by Fontana, public domain, via Wikimedia Commons", path="")
            db.add(row)
        return row
    monkeypatch.setattr(photos, "find", fake_find)
    sb.draw_missing(board_id, ConsoleReporter())
    out = client.get(f"/api/v1/storyboards/{board_id}").json()
    assert [p["query"] for p in out["photos"]] == ["Pope portrait"]
    assert out["photo_searches_missing"] == ["Nothing at all"]
    assert out["credits"].startswith("Picture: \"Portrait\" by Fontana")


def test_a_search_that_finds_nothing_tries_its_first_two_words(app_env, monkeypatch, tmp_path):
    asked = []

    def fake_search(query, limit=15):
        asked.append(query)
        return [] if len(query.split()) > 2 else [{
            "key": "7", "title": "Bedae Historia", "creator": "unknown author", "license": "Public domain",
            "license_url": "", "landing_url": "https://commons/7", "image_url": "https://x/7.jpg",
            "width": 900, "height": 1200, "score": 1.0}]
    monkeypatch.setattr(photos, "search", fake_search)
    monkeypatch.setattr(photos, "_download", lambda url, target: (900, 1200))
    found = photos.find("Bede Historia ecclesiastica manuscript")
    assert asked == ["Bede Historia ecclesiastica manuscript", "Bede Historia"] and found.key == "7"


def test_a_name_said_twice_is_credited_once():
    from clipper.studio import photos

    assert photos._once("Unknown author Unknown author") == "Unknown author"
    assert photos._once("Jean Jean Paul") == "Jean Jean Paul"
    line = photos.credit({"title": "Nicaea icon", "creator": "Unknown author Unknown author",
                          "license": "Public domain", "license_url": "", "landing_url": "https://commons.wikimedia.org/x"})
    assert "by Unknown author, public domain" in line



def refused(retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}

    def urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
    return urlopen


def test_requests_are_spaced_and_a_429_says_how_long_to_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(photos, "GAP", 1.0)
    monkeypatch.setattr(photos, "_last", photos.time.monotonic())
    monkeypatch.setattr(photos.time, "sleep", slept.append)
    monkeypatch.setattr(photos.urllib.request, "urlopen", refused("7"))
    with pytest.raises(photos.Busy) as busy:
        photos._get({"action": "query"})
    assert busy.value.wait == 7 and len(slept) == 1 and 0 < slept[0] <= 1.0
    monkeypatch.setattr(photos.urllib.request, "urlopen", refused("900"))
    with pytest.raises(photos.Busy) as busy:
        photos._get({"action": "query"})
    assert busy.value.wait == photos.MAX_WAIT
    monkeypatch.setattr(photos.urllib.request, "urlopen", refused())
    with pytest.raises(photos.Busy) as busy:
        photos._get({"action": "query"})
    assert busy.value.wait is None


def test_a_refused_download_does_not_move_on_to_the_next_result(app_env, monkeypatch):
    monkeypatch.setattr(photos, "_get", lambda params: ANSWER)
    monkeypatch.setattr(photos.urllib.request, "urlopen", refused())
    with pytest.raises(photos.Busy):
        photos.find("Pope Gregory XIII portrait")
    with session_scope() as db:
        assert db.query(StudioPhoto).count() == 0


def board_asking_for(*queries) -> int:
    plan = "0 shot \"x\"\n" + "\n".join(f'{n} photo "{q}" right' for n, q in enumerate(queries, 1))
    with session_scope() as db:
        script = Script(title="Ordeals", sections=[{"key": "a", "heading": "A", "text": "x", "short": False}])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, format="long", style="ink", title="Ordeals",
                           words=[{"w": "w", "s": i * 0.5, "e": i * 0.5 + 0.4, "section": "a"} for i in range(10)],
                           scenes=beats.parse_plan(plan, 10), start=0, end=5)
        db.add(board)
        db.flush()
        return board.id


class Log(ConsoleReporter):
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(message)

    def warn(self, message):
        self.lines.append(message)


def test_a_limited_search_waits_and_asks_again(app_env, monkeypatch):
    board_id = board_asking_for("Hot iron ordeal", "Donkey portrait")
    asked, slept = [], []

    def find(query, rank=0):
        asked.append(query)
        if asked.count(query) == 1:
            raise photos.Busy(12 if query == "Hot iron ordeal" else None)
        with session_scope() as db:
            db.add(StudioPhoto(query=query, rank=0, key=str(len(asked)), title="Found", creator="x",
                               license="Public domain", landing_url="https://commons/1", credit="c", path=""))
        return StudioPhoto(title="Found", license="Public domain")
    monkeypatch.setattr(photos, "find", find)
    rep = Log()
    sb.find_photos(board_id, rep, sleep=slept.append)
    assert asked == ["Hot iron ordeal", "Hot iron ordeal", "Donkey portrait", "Donkey portrait"]
    assert slept == [12, photos.WAITS[0]]                          # the delay Commons named, else our own
    assert not any("not searched" in line or "No usable picture" in line for line in rep.lines)


def test_a_search_still_limited_leaves_the_rest_for_the_next_render(app_env, monkeypatch):
    board_id = board_asking_for("Hot iron ordeal", "Donkey portrait", "Rice grains")
    asked, slept = [], []

    def find(query, rank=0):
        asked.append(query)
        raise photos.Busy()
    monkeypatch.setattr(photos, "find", find)
    rep = Log()
    sb.find_photos(board_id, rep, sleep=slept.append)
    assert asked == ["Hot iron ordeal"] * (len(photos.WAITS) + 1)   # the others are not even asked
    assert slept == list(photos.WAITS)
    assert "3 pictures were not searched: Hot iron ordeal, Donkey portrait, Rice grains" in rep.lines[-1]
    assert not any("No usable picture" in line for line in rep.lines)
