"""Licensed sound-effect provider and provenance contracts."""

import io
import json
import urllib.error
import urllib.parse

from alembic import command
from sqlalchemy import inspect

from clipper import db as db_mod
from clipper.studio import openverse, sfx


def test_sound_migration_round_trip(app_env):
    assert "studio_sounds" in inspect(db_mod.get_engine()).get_table_names()
    command.downgrade(db_mod._alembic_config(), "9d4a7c6e5b10")
    assert "studio_sounds" not in inspect(db_mod.get_engine()).get_table_names()
    command.upgrade(db_mod._alembic_config(), "head")
    columns = {column["name"] for column in inspect(db_mod.get_engine()).get_columns("studio_sounds")}
    assert {"kind", "provider", "provider_id", "landing_url", "license", "checksum", "approved_at"} <= columns


def test_only_exact_cc0_and_by_are_safe():
    safe = {"CC0": "cc0", "cc0 1.0": "cc0", "BY": "by", "CC BY 4.0": "by"}
    for value, canonical in safe.items():
        assert sfx.allowed_license(value) == canonical
    for value in ("", "PDM", "CC BY-SA 4.0", "CC BY-NC", "CC BY-ND", "Sampling+", "unknown"):
        assert sfx.allowed_license(value) is None


def test_openverse_sfx_search_sends_narrow_filters_and_normalises(monkeypatch):
    asked = {}

    def fake(url):
        asked.update(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
        return {"result_count": 2, "page": 1, "page_count": 1, "results": [
            {"id": "safe", "title": "Soft whoosh", "creator": "Artist", "source": "freesound",
             "url": "https://audio/preview.mp3", "foreign_landing_url": "https://source/safe",
             "license": "by", "license_version": "4.0", "license_url": "https://license",
             "attribution": "Soft whoosh by Artist", "duration": 300, "filetype": "mp3"},
            {"id": "unsafe", "source": "freesound", "url": "https://audio/no.mp3",
             "foreign_landing_url": "https://source/no", "license": "by-nc"},
        ]}

    monkeypatch.setattr(openverse, "get_json", fake)
    found = sfx.search_openverse("paper swish")
    assert asked["source"] == ["freesound"] and asked["license"] == ["cc0,by"]
    assert "category" not in asked
    assert [item["provider_id"] for item in found["results"]] == ["safe"]
    assert found["results"][0]["license"] == "by" and found["results"][0]["duration"] == 0.3


def test_commons_audio_sanitises_metadata_and_rejects_sharealike(monkeypatch):
    monkeypatch.setattr(sfx.photos, "_get", lambda params: {"query": {"pages": [
        {"pageid": 1, "title": "File:Door.wav", "index": 1, "imageinfo": [{
            "mime": "audio/wav", "url": "https://audio/door.wav", "descriptionurl": "https://page/door",
            "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"},
                            "LicenseUrl": {"value": "https://license"},
                            "Artist": {"value": "<b>Alice</b> &amp; Bob"},
                            "Credit": {"value": "<i>Recorded outside</i>"}}}]},
        {"pageid": 2, "title": "File:No.ogg", "index": 2, "imageinfo": [{
            "mime": "audio/ogg", "url": "https://audio/no.ogg", "descriptionurl": "https://page/no",
            "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}}}]},
    ]}})
    found = sfx.search_commons("wood door")
    assert len(found["results"]) == 1
    item = found["results"][0]
    assert item["creator"] == "Alice & Bob" and item["attribution"] == "Recorded outside"
    assert found["rate_limit"]["max_concurrency"] == 3


class Response(io.BytesIO):
    def __init__(self, value: dict, headers=None):
        super().__init__(json.dumps(value).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_openverse_uses_server_token_without_exposing_secret(monkeypatch):
    calls = []
    monkeypatch.setattr(openverse.config, "openverse_client", lambda: ("client", "very-secret"))

    def fake(request, timeout):
        calls.append(request)
        if request.full_url == openverse.TOKEN_API:
            assert b"very-secret" in request.data
            return Response({"access_token": "token", "expires_in": 3600})
        assert request.headers.get("Authorization") == "Bearer token"
        return Response({"results": []}, {"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "99"})

    monkeypatch.setattr(openverse.urllib.request, "urlopen", fake)
    openverse.reset_for_tests()
    assert openverse.get_json(openverse.API + "audio/") == {"results": []}
    assert openverse.rate_limit() == {"limit": 100, "remaining": 99, "reset": None, "authenticated": True}
    assert all("very-secret" not in repr(call.headers) for call in calls)


def test_failed_token_request_falls_back_anonymously(monkeypatch):
    monkeypatch.setattr(openverse.config, "openverse_client", lambda: ("client", "secret"))
    calls = 0

    def fake(request, timeout):
        nonlocal calls
        calls += 1
        if request.full_url == openverse.TOKEN_API:
            raise urllib.error.URLError("offline")
        assert "Authorization" not in request.headers
        return Response({"ok": True}, {"X-RateLimit-Limit": "20"})

    monkeypatch.setattr(openverse.urllib.request, "urlopen", fake)
    openverse.reset_for_tests()
    assert openverse.get_json(openverse.API + "audio/") == {"ok": True}
    assert calls == 2 and openverse.rate_limit()["authenticated"] is False
