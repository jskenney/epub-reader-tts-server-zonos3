"""The EPUB reader's voice-server API (API.md) in front of ZONOS3-Mini.

The reader and the voice studio speak the same API as epub-reader-tts-server's Breeze server: a multipart
POST /v1/audio/speech that streams 16-bit PCM back, and GET /health. ZONOS3's own server (zonos3-server, its
ggml build, on ZONOS_URL) speaks another (JSON, /v2/generate, 32-bit float PCM at 44.1 kHz), so this
translates:

  GET  /health               {"status": "ok", "sample_rate": 44100, "languages": [...], "instructions": false}
                             once ZONOS3 answers; 503 {"status": "loading"} until then
  POST /v1/audio/speech      text, ref_audio + ref_text (the clip; ZONOS3 doesn't need the transcript),
                             cfg_scale (speaker guidance), seed, language (the book's, e.g. "en-GB"),
                             speaker_id (a built-in voice, instead of a clip) -> streamed s16le PCM
  GET  /v1/builtin-voices    ZONOS3's built-in voices (for the studio)
  GET  /v1/builtin-voices/{id}/preview   a built-in voice's sample clip

A clip is registered with ZONOS3 once (POST /v2/speakers, keyed by the clip's SHA-256) and spoken with by its
id afterwards, so it isn't encoded again for every paragraph. `instruction` (Breeze's voice design) has no
ZONOS3 equivalent: with a clip it is ignored, without one it is refused (400).

Environment:
  ZONOS_URL          ZONOS3's server (default http://zonos3:1919)
  CLONE_MODE         embedding (default: the clip's speaker embedding) or reference (the clip itself as a
                     prompt prefix; needs zac-encoder.gguf and ssl-teacher.gguf in ZONOS3's models)
  DEFAULT_LANGUAGE   when a request names none (default en_us)
  ADAPTER_HOST, ADAPTER_PORT   default 0.0.0.0, 7860
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import os
import re
from contextlib import asynccontextmanager

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

ZONOS = os.environ.get("ZONOS_URL", "http://zonos3:1919").rstrip("/")
CLONE_MODE = os.environ.get("CLONE_MODE", "embedding")
DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "en_us")
SESSION = "epub-reader"  # ZONOS3 keeps cached voices per session (X-TTS-Session-ID)

# A book's language (BCP 47, as EPUB metadata gives it) -> ZONOS3's language code, where they differ.
LANGUAGE_ALIASES = {"en": "en_us", "fr": "fr_fr", "pt": "pt_br", "zh": "cmn", "nb": "no", "nn": "no",
                    "fil": "tl", "iw": "he", "in": "id"}
BRITISH = {"gb", "uk", "ie", "au", "nz", "za", "in"}  # English regions read with ZONOS3's British English
# ZONOS3's codes -> the language subtag the reader compares a book's language with.
READER_CODE = {"en_us": "en", "en_gb": "en", "fr_fr": "fr", "pt_br": "pt", "cmn": "zh"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = getattr(app.state, "client", None) or httpx.AsyncClient(
        base_url=ZONOS, timeout=httpx.Timeout(connect=5, read=300, write=60, pool=30))
    app.state.voices = {}       # clip SHA-256 -> ZONOS3 speaker id
    app.state.registering = {}  # clip SHA-256 -> asyncio.Lock
    app.state.languages = None  # ZONOS3's language codes, once it has answered
    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)


def zonos_language(given: str | None, supported: list[str] | None) -> str:
    """The ZONOS3 code for a book language such as "en-GB", "fr" or "zh-Hans" (or a ZONOS3 code itself)."""
    if not given or not given.strip():
        return DEFAULT_LANGUAGE
    tag = given.strip().lower().replace("-", "_")
    if supported and tag in supported:
        return tag
    primary, _, rest = tag.partition("_")
    if primary == "en":
        code = "en_gb" if rest.split("_")[0] in BRITISH else "en_us"
    else:
        code = LANGUAGE_ALIASES.get(primary, primary)
    if supported and code not in supported:
        raise HTTPException(400, f"Language '{given}' isn't one ZONOS3 speaks (it speaks {', '.join(reader_languages(supported))}).")
    return code


def reader_languages(supported: list[str]) -> list[str]:
    return sorted({READER_CODE.get(c, c) for c in supported})


async def languages(request: Request) -> list[str] | None:
    """ZONOS3's language codes (asked once; None while it isn't answering)."""
    if request.app.state.languages is None:
        try:
            r = await request.app.state.client.get("/v2/capabilities", timeout=5)
            if r.status_code == 200:
                request.app.state.languages = r.json().get("text_norm_languages") or None
        except httpx.HTTPError:
            return None
    return request.app.state.languages


def detail_of(r: httpx.Response) -> object:
    try:
        return r.json().get("detail", r.text[:300])
    except ValueError:
        return r.text[:300] or f"ZONOS3 answered {r.status_code}"


@app.get("/health")
async def health(request: Request):
    try:
        r = await request.app.state.client.get("/v2/health", timeout=5)
        ok = r.status_code == 200 and r.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        ok = False
    langs = await languages(request) if ok else None
    if not ok or not langs:
        return JSONResponse({"status": "loading"}, status_code=503)
    return {"status": "ok", "sample_rate": 44100, "languages": reader_languages(langs), "instructions": False,
            "engine": "zonos3"}


async def speaker_for_clip(request: Request, clip: bytes, name: str) -> str:
    """The ZONOS3 speaker id of a clip, registering it the first time it is seen."""
    st = request.app.state
    key = hashlib.sha256(clip).hexdigest()
    if key in st.voices:
        return st.voices[key]
    lock = st.registering.setdefault(key, asyncio.Lock())
    async with lock:
        if key not in st.voices:
            r = await st.client.post("/v2/speakers", headers={"X-TTS-Session-ID": SESSION}, json={
                "label": f"epub-reader {key[:12]}", "speaker_audio_base64": base64.b64encode(clip).decode(),
                "speaker_audio_name": name or "clip.wav"})
            if r.status_code != 200:
                raise HTTPException(400 if r.status_code < 500 else 502, f"ZONOS3 couldn't use the reference clip: {detail_of(r)}")
            st.voices[key] = r.json()["id"]
    return st.voices[key]


def to_s16(data: bytes, carry: bytearray) -> bytes:
    """32-bit float samples (as many whole ones as arrived) -> 16-bit; the remainder waits in carry."""
    carry += data
    n = len(carry) // 4 * 4
    if not n:
        return b""
    f = np.frombuffer(bytes(carry[:n]), dtype="<f4")
    del carry[:n]
    f = np.nan_to_num(f, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.rint(np.clip(f, -1.0, 1.0) * 32767).astype("<i2").tobytes()


@app.post("/v1/audio/speech")
async def speech(request: Request, text: str = Form(...), instruction: str | None = Form(None),
                 cfg_scale: float = Form(1.0), ref_audio: UploadFile | None = File(None),
                 ref_text: str = Form(""), seed: int = Form(42), language: str | None = Form(None),
                 speaker_id: str | None = Form(None)):
    if not math.isfinite(cfg_scale) or cfg_scale <= 0:
        raise HTTPException(400, "cfg_scale must be greater than 0.")
    if not text.strip():
        raise HTTPException(400, "text is empty.")
    clip = await ref_audio.read() if ref_audio is not None and ref_audio.filename else None
    if clip is not None and not clip:
        raise HTTPException(400, "Reference audio is empty.")
    if clip is None and not speaker_id and instruction and instruction.strip():
        raise HTTPException(400, "This voice server (ZONOS3-Mini) can't make a voice from an instruction: "
                                 "give the voice a reference clip.")
    supported = await languages(request)
    if supported is None:
        raise HTTPException(503, "ZONOS3 isn't answering.")
    body = {"text": text, "language": zonos_language(language, supported), "stream": True,
            "seed": seed % 2**32, "speaker_cfg_scale": cfg_scale}
    st = request.app.state

    async def open_stream(retry: bool = True) -> httpx.Response:
        if clip is not None:
            body["speaker_embedding_id"] = await speaker_for_clip(request, clip, ref_audio.filename)
            body["speaker_mode"] = CLONE_MODE
        elif speaker_id:
            body["speaker_embedding_id"] = speaker_id
        req = st.client.build_request("POST", "/v2/generate", json=body,
                                      headers={"X-TTS-Session-ID": SESSION})
        try:
            r = await st.client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"ZONOS3 isn't answering: {exc}") from None
        if r.status_code == 200:
            return r
        await r.aread()
        await r.aclose()
        detail = detail_of(r)
        # ZONOS3 restarted and forgot the session's voices: register the clip again, once.
        if clip is not None and retry and r.status_code == 400 and re.search(r"unknown (cached )?(voice|speaker)", str(detail), re.I):
            st.voices.pop(hashlib.sha256(clip).hexdigest(), None)
            return await open_stream(False)
        raise HTTPException(r.status_code if r.status_code < 500 else 502, detail)

    upstream = await open_stream()
    rate = upstream.headers.get("X-Audio-Sample-Rate", "44100")

    async def pcm():
        carry = bytearray()
        try:
            async for chunk in upstream.aiter_raw():
                out = to_s16(chunk, carry)
                if out:
                    yield out
        finally:
            await upstream.aclose()  # (also when the reader goes away: ZONOS3 then stops generating)

    return StreamingResponse(pcm(), media_type="audio/pcm", headers={
        "X-Sample-Rate": rate, "X-Sample-Format": "s16le", "Cache-Control": "no-store"})


STYLE_NAMES = {"announcervoice": "announcer", "asrm": "ASMR", "asmr": "ASMR"}  # (as upstream's files spell them)
VOICE_LABEL = re.compile(r"^(?P<name>[^_]+)_(?P<lang>[a-z]{2,3})(?:-(?P<style>[^_]+))?_(?P<gender>[MF])_\d+$")


@app.get("/v1/builtin-voices")
async def builtin_voices(request: Request):
    """ZONOS3's built-in voices: [{"id", "name", "language", "style", "gender"}], by language then name."""
    try:
        r = await request.app.state.client.get("/v2/speakers", timeout=10)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"ZONOS3 isn't answering: {exc}") from None
    if r.status_code != 200:
        raise HTTPException(502, detail_of(r))
    out = []
    for s in r.json().get("speakers", []):
        if not s.get("is_default"):
            continue
        label = s.get("original_name") or s.get("label") or s["id"]
        stem = re.sub(r"\.[a-z0-9]+$", "", label.rsplit("/", 1)[-1])
        m = VOICE_LABEL.match(stem)
        out.append({"id": s["id"], "name": m["name"] if m else stem, "language": m["lang"] if m else "",
                    "style": STYLE_NAMES.get(m["style"] or "", m["style"] or "") if m else "", "gender": {"M": "male", "F": "female"}.get(m["gender"], "") if m else "",
                    "preview": bool(s.get("has_preview"))})
    return {"voices": sorted(out, key=lambda v: (v["language"] != "en", v["language"], v["name"]))}


@app.get("/v1/builtin-voices/{voice_id}/preview")
async def builtin_preview(request: Request, voice_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
        raise HTTPException(404, "Not Found")
    try:
        r = await request.app.state.client.get(f"/v2/speakers/{voice_id}/preview", timeout=30)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"ZONOS3 isn't answering: {exc}") from None
    if r.status_code != 200:
        raise HTTPException(404 if r.status_code == 404 else 502, detail_of(r))
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("ADAPTER_HOST", "0.0.0.0"), port=int(os.environ.get("ADAPTER_PORT", "7860")))
