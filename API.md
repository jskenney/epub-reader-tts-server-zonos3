# API

Every HTTP endpoint the services in `docker-compose.yml` offer: what each request takes, what comes back, and the
errors. The reader-facing API is the same as [epub-reader-tts-server](https://github.com/jskenney/epub-reader-tts-server)'s
(Breeze TTS 2), so the reader, the proxy configuration and the voices folder work with either; the differences are
listed under [Differences from the Breeze server](#differences-from-the-breeze-server).

| Service | Port | Site path (through the proxy) | Endpoints | Used by |
|---|---|---|---|---|
| `adapter` | 7860 | `/tts/` | [`GET /health`](#get-health), [`POST /v1/audio/speech`](#post-v1audiospeech), [`GET /v1/builtin-voices`](#get-v1builtin-voices) | the reader, the studio, `make-voice.sh` |
| `studio` | 7861 | `/tts-voices/` → `/voices/`, `/tts-studio/` → `/` | [`GET /voices/voices.json`](#get-voicesvoicesjson), [`GET /voices/<clip>`](#get-voicesclip), [`GET /health`](#get-health-1), the studio UI | the reader (voices), you (UI) |
| `zonos3` | 1919 (not published) | none | ZONOS3's own API (`/v2/…`, [upstream docs](https://github.com/Zyphra/ZONOS3-Mini/blob/main/docs/api.md)) | the adapter only |
| `asr` | 7862 (not published) | none | [`GET /health`](#get-health-2), [`POST /transcribe`](#post-transcribe) | the studio only |

Paths below are as the service sees them. Through the proxy, prefix the site path: the reader asks for
`https://<site>/tts/v1/audio/speech`, which the proxy sends to `http://tts-host:7860/v1/audio/speech`. The services
themselves have no authentication; the proxy's password (see `nginx.conf.example`, `Caddyfile.example`) is what keeps
others off your GPU. The adapter is a FastAPI app, so it also serves generated docs at `/docs` and `/openapi.json`.

Times are in seconds. Audio is 44.1 kHz, 16-bit mono (88,200 bytes per second).

---

## Voice server API (`adapter`, port 7860)

The adapter (`adapter/app.py`) answers the reader's API and calls ZONOS3-Mini's server (`zonos3`, its ggml build) for
the speech. Requests may run while others are generating: ZONOS3 queues them and runs several at once, so there is
no 409 here (a Breeze server answers 409 while busy).

### `GET /health`

| Status | Body | When |
|---|---|---|
| 200 | `{"status": "ok", "sample_rate": 44100, "languages": ["ar", "bg", …, "en", …, "zh"], "instructions": false, "engine": "zonos3"}` | ZONOS3 is up. |
| 503 | `{"status": "loading"}` | ZONOS3 is still starting, or isn't answering. |

| Field | Meaning |
|---|---|
| `status` | `"ok"`: the reader shows **Server voice** only on a 200 with this. |
| `sample_rate` | The rate of the PCM speech returns. |
| `languages` | The languages ZONOS3 speaks, as language subtags (`en`, `fr`, `zh`, `pt`, …; 43 of them). The reader offers Server voice for a book whose language is one of these, and sends the book's language with each request. A server without this field (Breeze) is taken to speak English and Chinese. |
| `instructions` | `false`: voices that have only an `instruction` (Breeze's voice design) can't be spoken here; the reader leaves them out. |
| `engine` | `"zonos3"`, for information. |

### `POST /v1/audio/speech`

Speak one text; the audio streams back while it is generated.

**Request:** `multipart/form-data` (or `application/x-www-form-urlencoded` without a clip). Fields:

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `text` | string | yes | | What to say. ZONOS3 normalizes numbers, dates, money and abbreviations itself ("1984", "Dr.", "$5.50"). |
| `ref_audio` | file | no | none | A clip of the voice to speak in (any format ffmpeg reads; 5–15 s of clean speech is best, at most 60 s are used). |
| `ref_text` | string | no | `""` | The clip's transcript. Accepted for compatibility; ZONOS3 doesn't need it. |
| `language` | string | no | `en_us` | The book's language, as a BCP 47 tag (`en`, `en-GB`, `fr-CA`, `zh-Hans`, `pt`) or a ZONOS3 code (`en_gb`, `cmn`). `en` is read as US English, and `en-GB`, `en-AU`, `en-IE`, `en-NZ`, `en-ZA` and `en-IN` as British English. A language ZONOS3 doesn't speak is a 400. |
| `speaker_id` | string | no | none | One of ZONOS3's [built-in voices](#get-v1builtin-voices), instead of a clip. (An extension: the studio and `make-voice.sh` use it to make voices from them.) |
| `cfg_scale` | number | no | `1.0` | Speaker guidance (ZONOS3's `speaker_cfg_scale`): 1 is off; higher keeps closer to the voice, at twice the work per step. Must be finite and greater than 0. |
| `seed` | integer | no | `42` | The random seed. The same text, voice, language and seed give the same audio on the same build. |
| `instruction` | string | no | none | Breeze's voice description. With a clip (or `speaker_id`) it is ignored; alone, it is refused (400): ZONOS3 makes voices from clips. |

With neither a clip nor `speaker_id`, the speech is in no particular voice.

A clip is registered with ZONOS3 the first time it is seen (its speaker embedding is worked out, which takes a few
seconds on the first request in a voice) and spoken with by its id afterwards; the adapter remembers clips by their
SHA-256, so a changed clip is registered again. If ZONOS3 restarts and forgets them, the adapter registers the clip
again and retries, once.

**Response: 200**, the audio, streamed:

| Header | Value |
|---|---|
| `Content-Type` | `audio/pcm` |
| `X-Sample-Rate` | `44100` |
| `X-Sample-Format` | `s16le` |
| `Cache-Control` | `no-store` |

The body is raw PCM, with no WAV header: mono, signed 16-bit little-endian samples at `X-Sample-Rate`, sent as
ZONOS3 generates it (in chunks; a chunk can end in the middle of a sample). To save it as WAV:

```bash
ffmpeg -f s16le -ar 44100 -ac 1 -i speech.pcm speech.wav
```

Closing the connection stops the generation (ZONOS3 is told, and the next request starts at once).

**Errors:** JSON, FastAPI's form, as the Breeze server's:

| Status | Body | Cause |
|---|---|---|
| 400 | `{"detail": "cfg_scale must be greater than 0."}` | `cfg_scale` is 0, negative, infinite or NaN. |
| 400 | `{"detail": "Reference audio is empty."}` | `ref_audio` has no bytes. |
| 400 | `{"detail": "This voice server (ZONOS3-Mini) can't make a voice from an instruction: …"}` | `instruction` without a clip or `speaker_id`. |
| 400 | `{"detail": "Language 'xx' isn't one ZONOS3 speaks (it speaks …)."}` | An unknown `language`. |
| 400 | `{"detail": "ZONOS3 couldn't use the reference clip: …"}` | The clip couldn't be decoded or embedded. |
| 400 | `{"detail": "…"}` | Other refusals from ZONOS3, passed on (an unknown `speaker_id`, for example). |
| 422 | `{"detail": [{"type": "missing", "loc": ["body", "text"], …}]}` | A required field is missing, or a field isn't the right type. |
| 502 | `{"detail": "…"}` | ZONOS3 failed (a 5xx from it). |
| 503 | `{"detail": "ZONOS3 isn't answering…"}` | ZONOS3 is down or still starting. The reader then treats the server as gone until `/health` answers again. |

A failure after the audio has started (ZONOS3 stopping partway) ends the stream early; the reader treats a stream
much shorter than its text as failed and asks again one sentence at a time.

#### Example

```bash
curl -sS -X POST http://localhost:7860/v1/audio/speech \
  -F "text=It was a bright cold day in April." \
  -F "ref_audio=@tts-voices/narrator.wav" -F "ref_text=The exact words spoken in narrator.wav." \
  -F "language=en-GB" -F "seed=42" -o speech.pcm
```

### `GET /v1/builtin-voices`

ZONOS3's built-in voices (an extension, for the studio's **Pick a built-in voice** and `make-voice.sh list`):

```json
{"voices": [
  {"id": "default_2c5123a959875341", "name": "Cillian", "language": "en", "style": "Irish", "gender": "male", "preview": true},
  {"id": "default_517319c805b927e9", "name": "Gaspard", "language": "fr", "style": "", "gender": "male", "preview": true}
]}
```

English first, then by language and name. `id` goes in [`speaker_id`](#post-v1audiospeech).
`GET /v1/builtin-voices/<id>/preview` returns the voice's own sample clip (WAV, MP3 or OGG, as shipped). 503 while
ZONOS3 isn't answering.

The reader doesn't use built-in voices directly: the studio saves a sample in one as an ordinary clip voice in
`voices.json`.

---

## Voices (`studio`, port 7861)

The studio serves the voices folder (`./tts-voices` on the host, `VOICES_DIR` in the container) read-only at
`/voices/`; the site proxies `/tts-voices/` to it. The studio, `make-voice.sh` and you (by hand) write it. Responses
from `/voices/` carry `Cache-Control: no-cache`, so a new or changed voice shows up as soon as the reader asks again.

### `GET /voices/voices.json`

The voices the reader offers, in the order it lists them.

```json
{
  "voices": [
    {
      "id": "narrator",
      "name": "Warm narrator",
      "ref_audio": "narrator.wav",
      "ref_text": "The old house stood at the end of the lane, its windows catching the last of the evening light.",
      "seed": 42,
      "cfg_scale": 1.0,
      "description": "A warm, calm middle-aged woman with a clear, steady storytelling voice."
    },
    {
      "id": "me",
      "name": "My voice",
      "ref_audio": "me.wav",
      "ref_text": "The exact words spoken in the clip.",
      "seed": 42,
      "cfg_scale": 1.0,
      "source": "chapter-03.m4b 12:04.5–12:16.0"
    }
  ]
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `voices` | array | yes | The voices, in the order the reader lists them (the studio's **Voices** tab reorders them). |
| `id` | string | yes | Unique; `a-z`, `0-9`, `-`, `_` (the studio and `make-voice.sh` insist). The reader remembers the chosen voice by it. |
| `name` | string | no | Shown in the reader's voice list; `id` if missing. |
| `ref_audio` | string | with `ref_text` | The reference clip's file name, relative to the voices folder. Sent as [`ref_audio`](#post-v1audiospeech) with every request. |
| `ref_text` | string | with `ref_audio` | The clip's exact transcript, sent as `ref_text`. ZONOS3 doesn't use it, but the reader offers a clip voice only with one (the voices folder is the same format as the Breeze server's). |
| `instruction` | string | no | Breeze's voice description. ZONOS3 can't make a voice from one: with a clip it is ignored, and a voice with only an instruction fails (400) and is hidden by readers that know this server can't use instructions ([`/health`](#get-health)). |
| `seed` | integer | no | Sent as `seed`; 42 if missing. |
| `cfg_scale` | number | no | Sent as `cfg_scale` (speaker guidance; 1, off, if missing). |
| `description` | string | no | What the voice was made from, such as the built-in voice (kept for you; not sent). |
| `source` | string | no | The recording and times a clip was cut from (kept for you; not sent). |

Other fields are kept and ignored. How the reader reads the list:

- A voice is offered if it has `ref_audio` with `ref_text` (or an `instruction`, on a server that can use one); others
  are left out.
- `ref_audio` must stay inside the voices folder: a name that resolves elsewhere (`../x.wav`, another site, an
  encoded slash) isn't fetched. The voice then falls back to its `instruction` if it has one, and is left out
  otherwise.
- The reader caches the speech it makes per voice, keyed by the server address, `ref_audio` and its version (below),
  `ref_text`, `instruction`, `cfg_scale`, `seed` and the book's language. Changing any of those makes new speech;
  changing `name`, the
  order, or other fields doesn't.

| Status | When |
|---|---|
| 200 | `application/json`, as above. |
| 404 | No voices yet (no `voices.json`). The reader says so and points to the studio. |

### `GET /voices/<clip>`

A reference clip named in `voices.json`, for example `GET /voices/narrator.wav`. The clips the studio and
`make-voice.sh` write are WAV, mono, 44.1 kHz, 16-bit (clips from the Breeze server's studio, at 24 kHz, work as
well). Served as static files: `HEAD` works, and responses carry `ETag`
and `Last-Modified`. The reader fetches each clip once per session and sends it with every request in that voice;
it asks with `HEAD` for the clip's version (its `ETag`, or date and size), so replacing a clip under the same name
makes its cached speech be made again. 404 if the file isn't there.

### `GET /health`

`{"status": "ok", "voices": 3}`: the studio is up, and `voices` is how many entries `voices.json` has (0 without
one). Used by the container's health check. 500 if `voices.json` exists but isn't valid JSON.

### The studio UI (`/`)

The Gradio app (served at `/tts-studio/` through the proxy). Gradio's own endpoints under it (`/gradio_api/…`, the
upload and queue routes) belong to the UI and change with Gradio's version: they aren't an API to build on. Use
[`/v1/audio/speech`](#post-v1audiospeech) and the voices folder instead.

---

## Speech-to-text (`asr`, port 7862)

English Whisper (`ASR_MODEL`, default `openai/whisper-large-v3-turbo`) with word timings. Only the studio calls it,
over the compose network at `http://asr:7862`; the port isn't published on the host. From the host:
`docker compose exec asr curl -s localhost:7862/health`.

The model is loaded on the first transcription (downloaded the first time, about 1.6 GB) and unloaded after
`ASR_IDLE_UNLOAD` seconds without one (600; 0 keeps it). One transcription runs at a time; others wait.

### `GET /health`

```json
{"status": "ok", "model": "openai/whisper-large-v3-turbo", "device": "cuda", "loaded": false}
```

| Field | Meaning |
|---|---|
| `status` | Always `"ok"` while the service runs. |
| `model` | The Whisper model it uses. |
| `device` | `"cuda"`, or `"cpu"` without a GPU (much slower). |
| `loaded` | Whether the model is in memory now. |

### `POST /transcribe`

Transcribe a recording; progress streams back as it goes.

**Request:** `multipart/form-data` with one field:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `file` | file | yes | Any audio ffmpeg can read (MP3, M4A, M4B, WAV, FLAC, OGG, …), any length (up to 30 minutes of ffmpeg time to decode it). The file name's extension is used as a hint. Video is ignored. |

**Response: 200**, `application/x-ndjson`: one JSON object per line, streamed. Lines come in this order:

1. Zero or more **progress** lines while it works:

   ```json
   {"progress": 0.0, "status": "Reading the audio…"}
   {"progress": 0.0, "status": "Waiting for another transcription to finish…"}
   {"progress": 0.0, "status": "Loading openai/whisper-large-v3-turbo (the first time downloads it)…"}
   {"progress": 0.4213, "status": "Transcribed 12.6 of 30.0 minutes, about 2 min left"}
   ```

   `progress` goes from 0 to 1 (how far through the audio, by time); `status` is a sentence to show.

2. Then exactly one of:

   - a **result** line, the last line:

     ```json
     {"result": {
       "duration": 1800.04,
       "model": "openai/whisper-large-v3-turbo",
       "words": [
         {"start": 0.52, "end": 0.81, "text": "It"},
         {"start": 0.81, "end": 0.98, "text": "was"},
         {"start": 0.98, "end": 1.44, "text": "bright."}
       ],
       "segments": [
         {"start": 0.52, "end": 1.44, "first": 0, "last": 2, "text": "It was bright."}
       ]
     }}
     ```

   - or an **error** line, the last line: `{"error": "The recording is empty."}`, or
     `{"error": "<ExceptionType>: <message>"}` (ffmpeg couldn't read the file, the GPU ran out of memory, …).

Errors come as an `error` line in a 200 response, since the status has been sent before the work starts; check every
line for `error`. Only a request without `file` gets a non-200 answer (422, FastAPI's validation error).

| Result field | Type | Meaning |
|---|---|---|
| `duration` | number | The recording's length, in seconds. |
| `model` | string | The model that transcribed it. |
| `words` | array | Every word, in order. |
| `words[].start`, `words[].end` | number | When the word begins and ends, in seconds from the start (3 decimals). `end` ≥ `start`; a missing end is filled in (the next word's start, at most 0.6 s on). |
| `words[].text` | string | The word with its punctuation attached (`"bright."`), no surrounding spaces. |
| `segments` | array | The words grouped into sentences, in order. |
| `segments[].start`, `segments[].end` | number | The sentence's first word's start and last word's end. |
| `segments[].first`, `segments[].last` | integer | Indexes into `words` of the sentence's first and last word (inclusive). |
| `segments[].text` | string | The sentence's words joined by spaces. |

How it gets there, which explains the shape:

- The audio is decoded to 16 kHz mono and cut into pieces of 18–28 s at the quietest 50 ms near each cut, so a piece
  never splits a word; pieces that are nearly silent are skipped (Whisper invents words for silence), so stretches of
  silence or music have no words.
- Pieces are transcribed `ASR_BATCH` at a time, always as English. A piece Whisper wrote without punctuation is
  transcribed again with a punctuated prompt, and its punctuation and capitals are copied onto the words where the two
  agree.
- If the model can't give word timings, it falls back to sentence timings and spreads each sentence's words evenly
  over it.
- Sentences end at `.`, `!`, `?` or `…` (not after `Mr.`, `Dr.`, `St.` and the like), or failing that at a pause:
  over 1 s, over 0.5 s once a sentence is 8 s long, or the longest gap among its last words once it is 15 s long.

```bash
docker compose exec asr sh -c 'curl -sS -N -F "file=@/tmp/chapter.m4b" localhost:7862/transcribe'
```

---


---

## How the reader uses the API

The reader ([jskenney/epub-reader](https://github.com/jskenney/epub-reader)) takes the two addresses from its
`TTS_SERVER` setting (default `{"api": "/tts/", "voices": "/tts-voices/"}` on the site root). In order:

1. **Is there a server, and what does it speak?** `GET /tts/health`, with a 5 s time limit, when the read-aloud bar
   opens and before playing, at most every 15 s. Only a 200 JSON answer with `"status": "ok"` counts. With a
   `languages` list (this server), Server voice is offered for a book in one of those languages; without one (a
   Breeze server), for English and Chinese books.
2. **Which voices?** `GET /tts-voices/voices.json`, then each voice's clip from `/tts-voices/<ref_audio>` (once per
   session, plus a `HEAD` for its version). With `"instructions": false`, voices that have only an `instruction` are
   left out.
3. **Speech.** One `POST /tts/v1/audio/speech` per paragraph, as multipart: `text` (parentheses and brackets turned
   into dashes), the voice's `ref_audio` and `ref_text`, or its `instruction`, `cfg_scale` and `seed` (defaults 1
   and 42), and, to a server that listed its languages, `language` (the book's, as its metadata gives it, such as
   `en-GB`). A paragraph longer than 600 characters is split at sentence ends. Requests go one at a time, with the
   next two paragraphs asked for ahead.
4. **Reading the answer.** The stream is read as it arrives, at `X-Sample-Rate`. The reader gives up on a request that
   hasn't started answering after 60 s, or whose stream stops for 180 s before any audio or 60 s after some; an
   answer with less than 0.12 s of audio per word counts as cut off.
5. **Failures.** 502, 503, 504, an HTML answer, or no connection: the server counts as gone until `/health` answers
   again. Another error status: that paragraph fails with the `detail` shown, and a paragraph that keeps failing is
   asked for one sentence at a time.

The reader caches the speech per voice, keyed by the server address, the voice's clip and settings, and the
language it sent.

## Differences from the Breeze server

| | Breeze (epub-reader-tts-server) | ZONOS3 (this repository) |
|---|---|---|
| Languages | English, Chinese | 43 (`languages` in `/health`); `language` per request |
| Voices | a clip with its transcript, or a description (`instruction`) | a clip (the transcript isn't used), or a built-in voice; no descriptions |
| `cfg_scale` | guidance on the instruction or reference | speaker guidance |
| Audio | 24 kHz | 44.1 kHz |
| Busy | 409, the caller retries | queued; several at once |
| Text | read as written; `(sigh)` performed | numbers, dates and money normalized; no vocal events |
| Long prompts | fail partway past `fast-reader.json`'s lengths | no fixed limit (the model's context, 2,000 frames of audio) |
| Extra routes | `/v1/model`, `/v1/audio/speech/batch` | `/v1/builtin-voices` |
| Licence | weights and audio non-commercial | code MIT, weights Apache-2.0 |

## Proxy requirements

What the proxy must let through for the API to work (both examples do):

| Path | Needs |
|---|---|
| `/tts/` | No response buffering (audio streams back as it is generated); request bodies of at least 20 MB (the reference clip comes with every request); a read timeout of a few minutes. |
| `/tts-voices/` | Nothing special: small static files. |
| `/tts-studio/` | WebSocket upgrade, no buffering (progress streams back), request bodies up to the largest recording you upload (1 GB in `nginx.conf.example`; Caddy has no limit), and HTTPS for microphone recording. |
