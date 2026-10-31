"""Uploading a browser-picked file to the engine (ui/src/renderer/src/lib/api.ts).

The TypeScript runs under Node here, as tests/test_ui_steps.py runs its. What
is held to the contract: the `data` part uploadVideo builds is the same body
the path-based import sends minus the path it cannot have, the session token
rides the same header request() uses, progress reaches the caller as `loaded`
and a total that is `null` when the browser cannot compute one, and a 401
drops the session and fires the same `auth-expired` event the rest of the app
listens to. No network: XMLHttpRequest is a recording fake whose answers are
what the test says they are — that is exactly what these tests ask. The
engine side of the route has its own suite, tests/test_videos_upload.py; when
the two must stay equal, both files say so.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "ui" / "src" / "renderer" / "src" / "lib"

# Browser-adjacent globals a page build supplies, small enough to see whole.
# The fake XHR records what it is handed and answers with the plan, which is
# all these tests can possibly ask of it.
STUBS = r"""
const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k)
}
globalThis.window = new EventTarget()

var sent = []
var xhrPlan = {
  status: 200,
  response: '{"job_id": 7, "video_id": "local_abcdefghij"}',
  lengthComputable: true,
  loaded: 12,
  total: 40
}
class FakeXHR {
  constructor() {
    this.upload = {}
    this.headers = {}
  }
  open(method, url) {
    this.method = method
    this.url = url
  }
  setRequestHeader(name, value) {
    this.headers[name] = value
  }
  send(body) {
    sent.push(this)
    this.body = body
    this.upload.onprogress &&
      this.upload.onprogress({
        loaded: xhrPlan.loaded,
        total: xhrPlan.total,
        lengthComputable: xhrPlan.lengthComputable
      })
    this.status = xhrPlan.status
    this.responseText = xhrPlan.response
    this.onload && this.onload()
  }
}
globalThis.XMLHttpRequest = FakeXHR
// Node ships File, FormData, Event itself; the module only borrows shapes.
"""

# auth.ts reads the session token from localStorage at import time, so the
# token is placed before the module is ever imported.
TOKEN_SETUP = "store.set('auth-token', 'tok-123')"


def _clip_file() -> str:
    """A picked File, the way a browser hands one to uploadVideo."""
    return "new File([new Uint8Array(8)], 'friday.mp4', { type: 'video/mp4' })"


def _fail_wrap(body: str) -> str:
    """Run a body that is expected to throw, and keep the error text without
    short-circuiting the test's own next line."""
    return (
        "let failure = null\n"
        "try {\n"
        "  const attempt = await (async () => {\n" + body + "\n  })()\n"
        "  failure = 'no error, answered: ' + JSON.stringify(attempt)\n"
        "} catch (e) { failure = String(e) }"
    )


BODY_UPLOAD = f"""
const progress = []
const res = await m.api.uploadVideo(
  {_clip_file()},
  {{ title: 'Friday stream', vertical_live: true, force: true }},
  (loaded, total) => progress.push([loaded, total])
)
return {{
  method: sent[0].method,
  url: sent[0].url,
  auth: sent[0].headers.Authorization ?? null,
  fileName: sent[0].body.get('file') ? sent[0].body.get('file').name : null,
  data: JSON.parse(sent[0].body.get('data')),
  res,
  progress
}}
"""


def _run(tmp_path: Path, setup: str, body: str):
    """Run `body` (JavaScript; `m` is lib/api.ts) under Node and return what
    it prints as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    for name in ("api.ts", "auth.ts"):
        text = (LIB / name).read_text(encoding="utf-8")
        # The copy a page build gets differs from a test copy in exactly two
        # starter answers: Vite's compile-time env, and a loader that resolves
        # extensionless imports. The source itself is not touched.
        text = text.replace("import.meta.env.VITE_API_BASE", "globalThis.__VITE_API_BASE__")
        text = text.replace("from './auth'", "from './auth.ts'")
        (tmp_path / name).write_text(text, encoding="utf-8")
    script = (
        f"{STUBS}\n{setup}\n"
        f"const m = await import({json.dumps((tmp_path / 'api.ts').as_uri())});\n"
        "const out = await (async () => {\n" + body + "\n})();\n"
        "console.log(JSON.stringify(out));\n"
    )
    r = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


# ---- what the request looks like, and that it answers ----------------------


def test_the_upload_carries_the_file_the_data_and_the_token(tmp_path):
    r = _run(tmp_path, TOKEN_SETUP, BODY_UPLOAD)
    assert r["method"] == "POST"
    assert r["url"].endswith("/videos/upload")
    assert r["auth"] == "Bearer tok-123"
    assert r["res"] == {"job_id": 7, "video_id": "local_abcdefghij"}
    # The picked file goes up as the file part, under its own name.
    assert r["fileName"] == "friday.mp4"
    # …and the data part is the body the path import sends, minus its path.
    assert r["data"] == {
        "title": "Friday stream",
        "channel": "",
        "platform": "youtube",
        "captions": None,
        "caption_style": None,
        "long_clips": None,
        "podcast": None,
        "vertical_live": True,
        "gaming": None,
        "gaming_layout": None,
        "gaming_remember": None,
        "source_url": "",
        "longform": None,
        "watermark_profile_id": None,
        "filter": None,
        "min_score": None,
        "max_clips": None,
        "force": True,
    }
    assert "path" not in r["data"]
    # Progress was reported exactly as the browser gave it.
    assert r["progress"] == [[12, 40]]


def test_without_a_session_no_token_header_leaves(tmp_path):
    body = BODY_UPLOAD.replace("return {", "return { authed: sent[0].headers.Authorization ?? null, ")
    r = _run(tmp_path, "", body)
    assert r["authed"] is None


def test_progress_without_a_total_is_indeterminate(tmp_path):
    setup = "\n".join([TOKEN_SETUP, "xhrPlan.lengthComputable = false", "xhrPlan.total = 0"]) + "\n"
    r = _run(tmp_path, setup, BODY_UPLOAD)
    assert r["progress"] == [[12, None]]


# ---- 401: the session dies the same death request() gives it ---------------


def test_a_401_drops_the_session_and_fires_the_expired_event(tmp_path):
    setup = "\n".join(
        [
            TOKEN_SETUP,
            "var expired = 0",
            "window.addEventListener('auth-expired', () => { expired += 1 })",
            "xhrPlan.status = 401",
            "xhrPlan.response = '{\"detail\": \"invalid session token\"}'",
        ]
    ) + "\n"
    r = _run(
        tmp_path,
        setup,
        _fail_wrap(BODY_UPLOAD)
        + "\nreturn { failure, expired, tokenAfter: store.get('auth-token') ?? null }",
    )
    # The same words request() throws, so errorText() reads them; the extra
    # "Error: " comes from String(e) for a thrown Error and is the only
    # difference from what the screen would quote.
    assert r["failure"] == (
        "Error: 401 /videos/upload: " + '{"detail": "invalid session token"}'
    )
    # One event, the token dropped from storage it would be read back from.
    assert r["expired"] == 1
    assert r["tokenAfter"] is None


# ---- the engine's words reach the caller in request()'s shape ---------------


def test_refusals_read_the_same_way_the_rest_of_the_app_quotes_them(tmp_path):
    setup = (
        TOKEN_SETUP
        + "\nxhrPlan.status = 409\n"
        + "xhrPlan.response = '{\"detail\": \"the queue is full (5 videos) — let one finish or remove one first\"}'\n"
    )
    r = _run(tmp_path, setup, _fail_wrap(BODY_UPLOAD) + "\nreturn { failure }")
    # request() throws "Error: 409 /videos/upload: {json…}" (String(e) adds
    # the prefix); errorText() slices after the first ": ", so this shape IS
    # the contract.
    assert r["failure"] == (
        "Error: 409 /videos/upload: "
        '{"detail": "the queue is full (5 videos) — let one finish or remove one first"}'
    )
