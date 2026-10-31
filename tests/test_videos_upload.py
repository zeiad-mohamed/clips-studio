"""Importing video bytes over HTTP: POST /videos/upload.

The route a browser-served interface gets instead of a picked path. What is
pinned here: the bytes land streamed (digest and file in one pass), identity
is the CONTENT of the file not the name the browser claims, refusals read the
same messages the path route raises and leave nothing behind, the queue's
rules are the ones every other door answers to, and the login wall covers the
route like every other.

The path-based import has its own suite: tests/test_local_upload.py. When the
two must stay equal, both files must say so.
"""

import hashlib
import json

import pytest

from core.state import StateDB


@pytest.fixture
def api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("multipart")
    pytest.importorskip("numpy")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    # No `with` block: startup never runs, so no worker thread picks the job up.
    return TestClient(app, base_url="http://127.0.0.1"), data_dir


def _video_bytes(tmp_path, size: str, seconds: int = 3, bitrate: str | None = None) -> bytes:
    """A real small H.264 MP4, the way a browser would hand one over.

    `bitrate` forces the encoder's rate, for when the test needs the file to
    reach a size the content alone would never get it to (testsrc2 crushes to
    very little at the default CRF)."""
    from subprocess import run

    from core.binaries import ffmpeg

    binary = ffmpeg()
    try:
        run([binary, "-version"], capture_output=True, check=True)
    except (OSError, Exception):
        pytest.skip("FFmpeg isn't available")
    path = tmp_path / f"gen_{size.replace(':', 'x')}.mp4"
    args = [binary, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
            "-f", "lavfi", "-i", "sine", "-t", str(seconds), "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    if bitrate:
        args += ["-b:v", bitrate]
    args.append(str(path))
    run(args, check=True, capture_output=True)
    return path.read_bytes()


def _upload(client, blob: bytes, name: str = "clip.mp4", mime: str = "video/mp4", data: dict | None = None):
    return client.post(
        "/videos/upload",
        data={"data": json.dumps(data)} if data is not None else {"data": "{}"},
        files={"file": (name, blob, mime)},
    )


def _jobs(data_dir) -> list:
    db = StateDB(data_dir / "state.db")
    try:
        return [dict(r) for r in db.list_jobs()]
    finally:
        db.conn.close()


def _video_rows(data_dir) -> dict:
    db = StateDB(data_dir / "state.db")
    try:
        return {r["video_id"]: r["title"] for r in
                db.conn.execute("SELECT video_id, title FROM videos").fetchall()}
    finally:
        db.conn.close()


# ---- the route exists and registers an import like the path route ------------


def test_an_uploaded_video_is_imported_and_queued(api, tmp_path):
    client, data_dir = api
    blob = _video_bytes(tmp_path, "320x240")
    res = _upload(client, blob, name="friday.mp4")
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body["job_id"], int) and body["video_id"].startswith("local_")
    assert "already_processed" not in body

    copy = data_dir / "downloads" / f"{body['video_id']}.mp4"
    assert copy.is_file()
    # Queued exactly like the path route queues one: a local-import job of
    # that video, with the options the request carried.
    job = _jobs(data_dir)
    assert len(job) == 1 and job[0]["video_id"] == body["video_id"]
    assert json.loads(job[0]["payload"])["url"] == f"local:{body['video_id']}"
    # The client name becomes the title and nothing else: no file anywhere is
    # called friday.mp4, and the temp copy did not survive.
    assert _video_rows(data_dir) == {body["video_id"]: "friday"}
    assert [p.name for p in copy.parent.iterdir()] == [copy.name]
    assert not list((data_dir / "incoming").iterdir())


def test_the_title_defaults_to_the_browser_name_and_the_callers_win(api, tmp_path):
    client, data_dir = api
    plain = _upload(client, _video_bytes(tmp_path, "320x240"), name="my mend.mp4")
    assert plain.status_code == 200, plain.text
    given = _upload(
        client, _video_bytes(tmp_path, "480x360"),
        name="ignored.mp4", data={"title": "Islands, weekend one"},
    )
    assert given.status_code == 200, given.text
    titles = set(_video_rows(data_dir).values())
    assert titles == {"my mend", "Islands, weekend one"}


def test_the_same_bytes_uploaded_under_another_name_are_the_same_video(api, tmp_path):
    """Identity is the content, never the name the browser declares."""
    client, data_dir = api
    blob = _video_bytes(tmp_path, "320x240")
    first = _upload(client, blob, name="one.mp4").json()
    second = _upload(client, blob, name="completely different.mp4").json()
    assert second["video_id"] == first["video_id"]
    # One downloads entry, two queue jobs — the second is the parity case the
    # path route has with a re-added file, not a new import.
    downloads = data_dir / "downloads"
    assert [p.name for p in downloads.iterdir()] == [f"{first['video_id']}.mp4"]
    assert len(_jobs(data_dir)) == 2


def test_sending_the_same_bytes_again_does_not_convert_again(api, tmp_path):
    client, data_dir = api
    blob = _video_bytes(tmp_path, "320x240")
    first = _upload(client, blob).json()
    copy = data_dir / "downloads" / f"{first['video_id']}.mp4"
    before = copy.stat().st_mtime_ns

    _upload(client, blob)

    # import_local_source reuses a dest it can already read, so the second
    # request did not rewrite it (a copy-in touch would move this timestamp).
    assert copy.stat().st_mtime_ns == before


# ---- already processed (#98): the "Make clips again" offer --------------------


def test_a_file_already_clipped_is_answered_and_forced(api, tmp_path, monkeypatch):
    client, data_dir = api
    blob = _video_bytes(tmp_path, "320x240")
    import core.state

    # The pipeline is never started here, so shorts_made is claimed for the
    # video the way a finished run leaves it.
    monkeypatch.setattr(core.state.StateDB, "shorts_made", lambda self, vid: True)

    refused = _upload(client, blob)
    assert refused.status_code == 200, refused.text
    assert refused.json()["job_id"] is None
    assert refused.json()["already_processed"] is True
    assert len(_jobs(data_dir)) == 0

    forced = _upload(client, blob, data={"force": True})
    assert forced.status_code == 200, forced.text
    jobs = _jobs(data_dir)
    assert len(jobs) == 1 and json.loads(jobs[0]["payload"])["force"] is True


# ---- refusals: the messages the path route raises, and nothing left behind ---


def test_a_request_without_a_file_part_is_refused(api):
    client, data_dir = api
    res = client.post("/videos/upload", data={"data": "{}"})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("not a video file this app can open")
    assert _jobs(data_dir) == []
    assert not (data_dir / "downloads").exists() or not list((data_dir / "downloads").iterdir())


def test_a_part_the_engine_does_not_import_is_refused(api, tmp_path):
    client, data_dir = api
    res = _upload(client, b"keystrokes, not video", name="notes.txt", mime="text/plain")
    assert res.status_code == 400
    assert res.json()["detail"].endswith("notes.txt")
    # A media-looking name with media-looking type still faces the probe:
    still_refused = _upload(client, b"not really a video", name="clip.mp4")
    assert still_refused.status_code == 400
    assert still_refused.json()["detail"] == "that file doesn't look like a video"
    assert _jobs(data_dir) == []
    assert not list((data_dir / "incoming").iterdir())


def test_a_bad_data_part_is_named_not_crashed(api, tmp_path):
    client, _ = api
    blob = _video_bytes(tmp_path, "320x240")
    broken = client.post("/videos/upload", data={"data": "{oops"},
                        files={"file": ("clip.mp4", blob, "video/mp4")})
    assert broken.status_code == 400 and "JSON" in broken.json()["detail"]
    not_object = client.post("/videos/upload", data={"data": "17"},
                             files={"file": ("clip.mp4", blob, "video/mp4")})
    assert not_object.status_code == 400
    bad_option = client.post("/videos/upload",
                             data={"data": json.dumps({"max_clips": "all"})},
                             files={"file": ("clip.mp4", blob, "video/mp4")})
    assert bad_option.status_code == 400 and "invalid options" in bad_option.json()["detail"]


# ---- the queue's rules arrive here with every other door ---------------------


def test_the_queue_has_room_or_the_upload_is_refused(api, tmp_path, monkeypatch):
    client, data_dir = api
    import core.queue

    monkeypatch.setattr(core.queue, "active_count", lambda db: core.queue.MAX_ACTIVE)
    res = _upload(client, _video_bytes(tmp_path, "320x240"))
    assert res.status_code == 409
    assert res.json()["detail"] == (
        f"the queue is full ({core.queue.MAX_ACTIVE} videos) — let one finish or remove one first"
    )
    # Nothing was committed under the refusal: no job, no import, no temp copy.
    assert _jobs(data_dir) == []
    assert (data_dir / "incoming").is_dir()
    assert not list((data_dir / "incoming").iterdir())
    assert not list((data_dir / "downloads").iterdir())


# ---- nothing holds the whole body in memory ---------------------------------


def test_the_part_is_read_and_hashed_in_fixed_chunks():
    """The streaming pass, at the unit level: bounded reads, no whole read()."""
    from server.api import _save_upload_part, _UPLOAD_CHUNK

    blob = b"x" * (_UPLOAD_CHUNK + 12345)
    seen: list[int] = []

    class _Watching:
        def __init__(self, payload):
            self.payload = payload
            self.cursor = 0

        def read(self, size):
            piece = self.payload[self.cursor:self.cursor + size]
            self.cursor += len(piece)
            seen.append(size)
            return piece

    import tempfile
    from pathlib import Path

    fd, name = tempfile.mkstemp(suffix=".part")
    stream = _Watching(blob)
    digest = _save_upload_part(stream, Path(name))
    assert digest == hashlib.md5(blob, usedforsecurity=False).hexdigest()
    # Bounded reads: nothing ever asked for more than one chunk, and the blob
    # is bigger than a chunk, so one read() could never have gotten it all.
    assert max(seen) <= _UPLOAD_CHUNK
    assert len(blob) > _UPLOAD_CHUNK


def test_a_multi_megabyte_upload_succeeds(api, tmp_path):
    """Crosses Starlette's spooled-file threshold on the way in."""
    client, data_dir = api
    # The bitrate makes the blob's size certain: three seconds at 6 Mbit is
    # about two megabytes, past the ~1 MiB where the request body's spool
    # stops staying in memory — the posture a real recording arrives in.
    blob = _video_bytes(tmp_path, "640x480", seconds=3, bitrate="6M")
    assert len(blob) > 1024 * 1024, "the generator undershot; raise the bitrate"
    res = _upload(client, blob)
    assert res.status_code == 200, res.text
    copy = data_dir / "downloads" / f"{res.json()['video_id']}.mp4"
    assert copy.is_file() and copy.stat().st_size >= len(blob) // 2


# ---- the login wall reaches this route like every other ---------------------


USER = "admin"
PASSWORD = "hunter2"


@pytest.fixture
def auth_api(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPS_STUDIO_AUTH_USER", USER)
    monkeypatch.setenv("CLIPS_STUDIO_AUTH_PASSWORD", PASSWORD)
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    app = create_app(config, tmp_path / "settings.yaml")
    return TestClient(app, base_url="http://127.0.0.1"), tmp_path


def test_auth_guards_the_upload_route(auth_api, tmp_path):
    client, _ = auth_api
    refused = client.post("/videos/upload", data={"data": "{}"})
    assert refused.status_code == 401

    token = client.post("/login", json={"username": USER, "password": PASSWORD}).json()["token"]
    assert token
    allowed = client.post(
        "/videos/upload",
        data={"data": "{}"},
        files={"file": ("clip.mp4", _video_bytes(tmp_path, "320x240"), "video/mp4")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["video_id"].startswith("local_")
