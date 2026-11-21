"""Offline tests for the creator-approved local sound-effect workflow."""

import shutil
import wave
from pathlib import Path

import numpy as np

from clipper.db import session_scope
from clipper.db.models import Script, Storyboard, StudioSound
from clipper.studio import export, publish, sfx, sound


def _wav(path: Path, seconds: float = 0.1) -> None:
    data = (np.zeros(int(sound.RATE * seconds), dtype="<i2")).tobytes()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sound.RATE)
        out.writeframes(data)


def test_import_is_local_checksumed_and_requires_approval(app_env, monkeypatch, tmp_path):
    import clipper.config as config

    monkeypatch.setattr(config, "studio_dir", lambda: tmp_path / "studio")
    monkeypatch.setattr(sfx, "download", lambda url, target, max_bytes=sfx.MAX_DOWNLOAD_BYTES:
                        target.write_bytes(b"fake") or 4)
    monkeypatch.setattr(sound, "normalise_effect", lambda src, out: shutil.copyfile(src, out))
    monkeypatch.setattr(sfx, "_duration", lambda path: 0.1)
    candidate = {"provider": "openverse", "provider_id": "safe", "title": "Soft whoosh",
                 "media_url": "https://audio.invalid/safe", "landing_url": "https://source.invalid/safe",
                 "license": "by", "license_version": "4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                 "creator": "Artist"}
    with session_scope() as db:
        row = sfx.import_sound(db, candidate, "whoosh")
        assert row.path and Path(row.path).is_file() and row.checksum and row.approved_at is None
        sfx.approve(row)
        assert sfx.usable(row)
        sfx.reject(row, "wrong texture")
        assert not sfx.usable(row) and row.rejection_note == "wrong texture"


def test_missing_local_asset_uses_synthesized_fallback(tmp_path):
    out = tmp_path / "effects.wav"
    status = sound.write_effects([], 0.4, out,
                                 assets=[{"sound_id": 4, "at": 0.0, "rule": "pop", "path": "missing.wav"}])
    assert out.is_file() and status["missing"] == 1 and status["synthesized"] == 1
    assert status["missing_ids"] == [4]


def test_local_asset_is_mixed_without_remote_hotlink(tmp_path):
    source = tmp_path / "approved.wav"
    out = tmp_path / "effects.wav"
    _wav(source)
    status = sound.write_effects([], 0.2, out,
                                 assets=[{"sound_id": 7, "at": 0.0, "path": str(source), "gain": 1}])
    assert status["licensed"] == 1 and status["missing"] == 0 and status["synthesized"] == 0
    assert status["licensed_ids"] == [7]


def test_export_appends_sound_attribution():
    credit = "Sound: Soft whoosh by Artist (BY 4.0; https://source; trimmed/mixed by Clipper)"
    text = export.youtube_file({"title": "A", "description": "Opening", "tags": []}, sound_credits=credit)
    assert "DESCRIPTION\nOpening" in text and "trimmed/mixed by Clipper" in text


def test_publish_credits_only_effects_frozen_in_the_successful_render(app_env):
    with session_scope() as db:
        script = Script(title="S", sections=[])
        db.add(script)
        sound_row = StudioSound(kind="whoosh", name="Soft whoosh", provider="openverse", provider_id="safe",
                                creator="Artist", license="by", license_version="4.0",
                                landing_url="https://source.invalid/safe")
        db.add(sound_row)
        db.flush()
        board = Storyboard(script_id=script.id, render_meta={
            "version": 1, "sound_effects": {"licensed_ids": [sound_row.id], "missing_ids": [99]}})
        db.add(board)
        db.flush()
        credit = publish.sound_credits(db, board)
        assert "Soft whoosh by Artist" in credit and "99" not in credit
        board.render_meta = {"version": 1, "sound_effects": {"licensed_ids": [], "missing_ids": [sound_row.id]}}
        assert publish.sound_credits(db, board) == ""


def test_secrets_are_not_part_of_sound_credit():
    row = StudioSound(name="Click", creator="Artist", license="cc0", license_version="1.0",
                       landing_url="https://source.invalid/click", license_url="https://creativecommons.org/publicdomain/zero/1.0/")
    assert "secret" not in sfx.credit(row)


def test_import_route_refetches_metadata_instead_of_trusting_browser_candidate(client, monkeypatch):
    def refused(provider, provider_id):
        raise ValueError("provider metadata refused it")

    monkeypatch.setattr(sfx, "candidate", refused)
    response = client.post("/api/v1/sounds/import", json={
        "provider": "openverse", "provider_id": "unsafe", "kind": "whoosh",
        "candidate": {"license": "by", "media_url": "https://attacker.invalid/file"},
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "provider metadata refused it"


def test_sound_api_does_not_expose_local_cache_paths(client):
    with session_scope() as db:
        db.add(StudioSound(kind="pop", name="Click", path="/private/cache/secret.wav", provider="openverse",
                           provider_id="click", license="cc0"))
    item = client.get("/api/v1/sounds").json()[0]
    assert "path" not in item and item["cached"] is False


def test_cached_sound_audio_is_served_without_exposing_its_path(client, tmp_path):
    path = tmp_path / "approved.wav"
    _wav(path)
    with session_scope() as db:
        row = StudioSound(kind="pop", name="Click", path=str(path), provider="openverse",
                          provider_id="click", license="cc0", checksum="abc")
        db.add(row)
        db.flush()
        sound_id = row.id
    response = client.get(f"/api/v1/sounds/{sound_id}/audio")
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert str(path) not in response.text and response.content[:4] == b"RIFF"
    path.unlink()
    assert client.get(f"/api/v1/sounds/{sound_id}/audio").status_code == 404
