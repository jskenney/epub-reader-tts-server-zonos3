"""The adapter against a fake ZONOS3 (no model): pytest adapter/test_app.py

The fake answers like zonos3-server: /v2/health, /v2/capabilities, /v2/speakers (cached per session),
/v2/generate (streamed float32 PCM), and records what it was sent.
"""

import base64
import struct

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

import app as adapter

LANGS = ["en_us", "en_gb", "fr_fr", "de", "pt_br", "cmn", "ja", "no"]


def make_fake(state: dict) -> FastAPI:
    fake = FastAPI()

    @fake.get("/v2/health")
    def health():
        if state.get("down"):
            return JSONResponse({"detail": "starting"}, 503)
        return {"status": "ok"}

    @fake.get("/v2/capabilities")
    def caps():
        return {"text_norm_languages": LANGS}

    @fake.get("/v2/speakers")
    def speakers():
        return {"speakers": [
            {"id": "default_aaa", "label": "Cillian_en-Irish_M_1", "original_name": "Cillian_en-Irish_M_1.wav",
             "is_default": True, "has_preview": True},
            {"id": "default_bbb", "label": "Gaspard_fr_M_1", "original_name": "Gaspard_fr_M_1.mp3",
             "is_default": True, "has_preview": True},
            {"id": "default_ccc", "label": "Willow en asrm F 1", "original_name": "Willow_en-asrm_F_1.wav",
             "is_default": True, "has_preview": True},
            {"id": "sess_1", "label": "x", "original_name": None, "is_default": False, "has_preview": False}]}

    @fake.get("/v2/speakers/{sid}/preview")
    def preview(sid: str):
        if sid != "default_aaa":
            return JSONResponse({"detail": "unknown voice"}, 404)
        return StreamingResponse(iter([b"RIFFfake"]), media_type="audio/wav")

    @fake.post("/v2/speakers")
    async def add_speaker(request: Request):
        body = await request.json()
        state["registered"].append((request.headers.get("x-tts-session-id"), base64.b64decode(body["speaker_audio_base64"])))
        sid = f"sess_{len(state['registered'])}"
        state["known"].add(sid)
        return {"id": sid}

    @fake.post("/v2/generate")
    async def generate(request: Request):
        body = await request.json()
        state["generated"].append(body)
        sid = body.get("speaker_embedding_id")
        if sid and sid.startswith("sess_") and sid not in state["known"]:
            return JSONResponse({"detail": f"Unknown cached speaker id '{sid}'."}, 400)   # (zonos3-server's words)
        if body["text"] == "BAD":
            return JSONResponse({"detail": "Pause marker outside the buckets"}, 400)
        samples = np.array([0.0, 0.5, -0.5, 1.5, -2.0, float("nan")] * 50, dtype="<f4").tobytes()

        def chunks():  # odd sizes: samples split across chunks
            for i in range(0, len(samples), 7):
                yield samples[i:i + 7]
        return StreamingResponse(chunks(), media_type="application/octet-stream",
                                 headers={"X-Audio-Sample-Rate": "44100", "X-Audio-Format": "float32"})

    return fake


@pytest.fixture
def env():
    state = {"registered": [], "known": set(), "generated": []}
    adapter.app.state.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=make_fake(state)),
                                                 base_url="http://zonos3")
    with TestClient(adapter.app) as client:
        yield client, state
    adapter.app.state.client = None


def speak(client, **fields):
    files = {k: (None, str(v)) for k, v in fields.items() if k != "ref_audio"}
    if "ref_audio" in fields:
        files["ref_audio"] = ("narrator.wav", fields["ref_audio"], "audio/wav")
    return client.post("/v1/audio/speech", files=files)


def test_health_lists_the_languages_as_the_reader_names_them(env):
    client, state = env
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "sample_rate": 44100, "languages": ["de", "en", "fr", "ja", "no", "pt", "zh"],
                        "instructions": False, "engine": "zonos3"}
    state["down"] = True
    assert client.get("/health").json() == {"status": "loading"}
    assert client.get("/health").status_code == 503


def test_speech_streams_16_bit_pcm_with_the_clip_registered_once(env):
    client, state = env
    for text in ("First paragraph.", "Second paragraph."):
        r = speak(client, text=text, ref_audio=b"WAVDATA", ref_text="The words.", cfg_scale="1", seed="7",
                  language="en-GB")
        assert r.status_code == 200
        assert r.headers["x-sample-rate"] == "44100" and r.headers["x-sample-format"] == "s16le"
        assert r.headers["content-type"].startswith("audio/pcm")
        pcm = struct.unpack(f"<{len(r.content) // 2}h", r.content)
        assert len(pcm) == 300
        assert pcm[:6] == (0, 16384, -16384, 32767, -32767, 0)   # clipped, NaN as silence
    assert state["registered"] == [("epub-reader", b"WAVDATA")]     # once for both paragraphs
    assert [g["text"] for g in state["generated"]] == ["First paragraph.", "Second paragraph."]
    g = state["generated"][0]
    assert g["speaker_embedding_id"] == "sess_1" and g["speaker_mode"] == "embedding"
    assert g["language"] == "en_gb" and g["seed"] == 7 and g["speaker_cfg_scale"] == 1.0 and g["stream"] is True


@pytest.mark.parametrize(("given", "expected"), [
    (None, "en_us"), ("en", "en_us"), ("en-US", "en_us"), ("en-AU", "en_gb"), ("fr-CA", "fr_fr"), ("zh-Hans", "cmn"),
    ("pt", "pt_br"), ("nb", "no"), ("de-DE", "de"), ("ja", "ja"), ("cmn", "cmn")])
def test_book_languages_map_to_zonos3s(env, given, expected):
    client, state = env
    fields = {"text": "Hi."} | ({"language": given} if given else {})
    assert speak(client, **fields).status_code == 200
    assert state["generated"][-1]["language"] == expected


def test_a_language_zonos3_doesnt_speak_is_refused(env):
    client, _ = env
    r = speak(client, text="Hi.", language="xx")
    assert r.status_code == 400 and "isn't one ZONOS3 speaks" in r.json()["detail"]


def test_instruction_without_a_clip_is_refused_and_ignored_with_one(env):
    client, state = env
    r = speak(client, text="Hi.", instruction="A deep voice.")
    assert r.status_code == 400 and "instruction" in r.json()["detail"]
    assert state["generated"] == []
    assert speak(client, text="Hi.", instruction="Whisper.", ref_audio=b"CLIP", ref_text="x").status_code == 200
    assert "instruction" not in state["generated"][0]


@pytest.mark.parametrize(("fields", "status", "detail"), [
    ({"text": "Hi.", "cfg_scale": "0"}, 400, "cfg_scale must be greater than 0."),
    ({"text": "Hi.", "cfg_scale": "nan"}, 400, "cfg_scale must be greater than 0."),
    ({"text": "Hi.", "ref_audio": b"", "ref_text": "x"}, 400, "Reference audio is empty."),
    ({"text": "BAD"}, 400, "Pause marker outside the buckets"),
])
def test_errors_answer_as_breeze_does(env, fields, status, detail):
    client, _ = env
    r = speak(client, **fields)
    assert r.status_code == status and r.json()["detail"] == detail


def test_missing_text_is_a_422_with_a_list(env):
    client, _ = env
    r = client.post("/v1/audio/speech", files={"seed": (None, "1")})
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)


def test_a_forgotten_voice_is_registered_again(env):
    client, state = env
    assert speak(client, text="One.", ref_audio=b"CLIP", ref_text="x").status_code == 200
    state["known"].clear()   # ZONOS3 restarted
    assert speak(client, text="Two.", ref_audio=b"CLIP", ref_text="x").status_code == 200
    assert len(state["registered"]) == 2 and state["generated"][-1]["speaker_embedding_id"] == "sess_2"


def test_built_in_voices_and_speaking_in_one(env):
    client, state = env
    r = client.get("/v1/builtin-voices")
    assert r.json() == {"voices": [
        {"id": "default_aaa", "name": "Cillian", "language": "en", "style": "Irish", "gender": "male", "preview": True},
        {"id": "default_ccc", "name": "Willow", "language": "en", "style": "ASMR", "gender": "female", "preview": True},
        {"id": "default_bbb", "name": "Gaspard", "language": "fr", "style": "", "gender": "male", "preview": True}]}
    assert client.get("/v1/builtin-voices/default_aaa/preview").content == b"RIFFfake"
    assert client.get("/v1/builtin-voices/nope/preview").status_code == 404
    assert client.get("/v1/builtin-voices/..%2fx/preview").status_code == 404
    assert speak(client, text="Bonjour.", speaker_id="default_bbb", language="fr").status_code == 200
    assert state["generated"][-1]["speaker_embedding_id"] == "default_bbb"


def test_zonos3_down_is_a_503(env):
    client, _ = env
    adapter.app.state.client = httpx.AsyncClient(base_url="http://127.0.0.1:9")   # nothing listens there
    adapter.app.state.languages = None
    r = speak(client, text="Hi.")
    assert r.status_code == 503
    assert client.get("/health").status_code == 503
