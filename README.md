# EPUB reader TTS server: ZONOS3-Mini

[ZONOS3-Mini](https://github.com/Zyphra/ZONOS3-Mini) on your own GPU, for the **Server voice** in
[jskenney/epub-reader](https://github.com/jskenney/epub-reader), with a voice studio for trying text and creating
voices. It answers the same API as [epub-reader-tts-server](https://github.com/jskenney/epub-reader-tts-server)
(Breeze TTS 2), so the reader, the proxy configuration and the voices folder work with either; run one or the other.

What ZONOS3 brings over Breeze: **43 languages** (Breeze: English and Chinese), server-side text normalization
(numbers, dates, money, abbreviations), 44.1 kHz audio, built-in voices, and an open licence.

**Licence:** ZONOS3's code is MIT and its weights Apache-2.0, so unlike Breeze's non-commercial weights, the audio
can be used on a public or commercial site. You still need the rights and consent for any voice you clone.

## What runs

`docker-compose.yml` starts four services on the GPU machine:

| Service | Port | What |
|---|---|---|
| `adapter` | 7860 | The reader's API (`/health`, `/v1/audio/speech`; [API.md](API.md)), translated to ZONOS3's. Small, no GPU. |
| `zonos3` | (none) | ZONOS3-Mini's ggml server, built with CUDA from upstream at a pinned commit. Uses the GPU. Only the adapter talks to it. |
| `studio` | 7861 | The voice studio (a Gradio app) and the voices folder at `/voices/`. No GPU. |
| `asr` | (none) | Speech-to-text for the studio: English Whisper large-v3-turbo with word timings. Uses the GPU (about 2 GB) only while transcribing. Only the studio talks to it. |

These are ordinary folders next to `docker-compose.yml`, so they survive rebuilds and are easy to back up:

| Folder | What | Change with |
|---|---|---|
| `./models/zonos3` | ZONOS3's model files and built-in voices (`fetch-models.sh`) | `ZONOS3_MODELS_DIR` |
| `./tts-voices` | Voices: `voices.json` plus one reference clip per voice | `TTS_VOICES_DIR` |
| `./tts-sources` | Long recordings uploaded in the studio, with their transcripts, until you delete them | `TTS_SOURCES_DIR` |
| `./asr-models` | The speech-to-text model (about 1.6 GB, downloaded on the first transcription) | `ASR_MODELS_DIR` |

Your web server proxies three paths on the reader's site to it, exactly as for the Breeze server:

| Site path | Goes to | Used by |
|---|---|---|
| `/tts/` | `tts-host:7860/` | the reader's speech requests |
| `/tts-voices/` | `tts-host:7861/voices/` | the reader's voice list and clips |
| `/tts-studio/` | `tts-host:7861/` | you, in a browser |

The reader shows **Server voice** when `/tts/health` answers, for books in the languages it lists, and reads its
voices from `/tts-voices/voices.json`.

## Requirements

- Linux with an NVIDIA GPU (4 GB of memory is plenty for ZONOS3; Whisper adds about 2 GB while transcribing), a
  recent driver, and the NVIDIA Container Toolkit for Docker. (For a Mac, see [On a Mac](#on-a-mac-apple-silicon).)
- `docker-compose.yml` builds for Blackwell workstation and consumer GPUs (RTX PRO 4000/6000 Blackwell, RTX 50xx:
  `ZONOS3_CUDA_ARCHS=120`). For other cards set `ZONOS3_CUDA_ARCHS` (90 H100, 89 RTX 40xx, 86 RTX 30xx, 80 A100).

## Set up

On the GPU machine, in a clone of this repository:

```bash
./fetch-models.sh                   # about 1.2 GB, checked against the Hub's SHA-256s
docker compose up -d --build        # the first build compiles ZONOS3 with CUDA: allow 10-30 minutes
docker compose logs -f zonos3       # wait for "listening on http://0.0.0.0:1919"
curl http://localhost:7860/health
curl http://localhost:7861/health
```

Then add the three proxy paths on your web server: see `nginx.conf.example` or `Caddyfile.example`. Response
buffering must be off for `/tts/` and `/tts-studio/` so audio and progress stream back. Put the site behind a
password so nobody else can use your GPU.

If you open the studio directly at `http://<host>:7861/` instead of through the proxy, set `STUDIO_ROOT_PATH=`
(empty) before `docker compose up -d`.

**Switching from the Breeze server:** stop it (`docker compose down` in its folder), copy or point
`TTS_VOICES_DIR` at its `tts-voices` folder, and start this one. Voices with a clip work as they are (their
transcripts aren't needed here); voices made only from a description (`instruction`) can't be spoken by ZONOS3, and
the reader leaves them out. The reader's saved audio is kept per server address and voice, so audio made by Breeze
is reused if the address and voice are the same; clear it in the reader's settings to hear ZONOS3 instead.

## On a Mac (Apple Silicon)

Docker on a Mac can't use the GPU, so the `mac/` folder runs the same services natively, with the same ports and
API. ZONOS3 runs on its own MLX server (upstream's `servers/mlx`), from the same model files.

**Not yet run on a Mac.** The adapter, studio and ZONOS3's ggml server were tested together on Linux (on the CPU);
the Mac scripts follow the Breeze repository's, which were checked with stand-in tools, and upstream's MLX server.

```bash
brew install uv ffmpeg git
mac/setup.sh                 # fetches ZONOS3, creates the Python environments, downloads the models (~1.2 GB)
mac/start.sh                 # the adapter on 7860, the studio on 7861 (ZONOS3 and speech-to-text on 127.0.0.1)
curl http://localhost:7860/health
```

`mac/start.sh` runs in the foreground (Ctrl-C stops it) and logs to `mac/logs/`. `mac/install-service.sh` starts it
at login and restarts it if it stops (`--uninstall` removes that). Settings go in `mac/settings.env`, one
`NAME=value` per line: those below, plus `TTS_BIND` (default `0.0.0.0`; `127.0.0.1` if the web server runs on the
same Mac).

## The voice studio

Open `https://<your site>/tts-studio/`.

- **Try text:** paste any text, choose a saved or built-in voice, give the language (`en`, `en-GB`, `fr`, `de`,
  `zh`, `ja`, …), and generate. With "Read it like the reader" on, it sends one request per paragraph as the
  reader does, and it reports seconds of audio, generation time and the real-time factor, so you can tell whether
  your GPU keeps up.
- **Pick a built-in voice:** choose one of ZONOS3's 27 built-in voices (English, French, German, Spanish,
  Italian, Portuguese, Chinese, Korean, Hindi, Telugu), hear its own sample, generate a sample of your text in
  it, try other seeds, and **Save as voice**. The sample becomes the voice's reference clip. (ZONOS3 makes voices
  from clips, not from descriptions, so this takes the place of Breeze's "Design a voice".)
- **Record or upload:** record with the microphone or upload a file, trim it, test it, and save it. Use 5-15 seconds
  of clean speech (ZONOS3 uses up to 60 s). Recording needs HTTPS (or localhost); uploading works anywhere.
- **From a long recording:** upload an audiobook chapter or a podcast, transcribe it (English), click sentences to
  choose a clip, test and save it.
- **Voices:** preview, rename, reorder (the order the reader lists them) and delete voices, and **Hear it speak**
  any text in a voice.

`make-voice.sh` does the same from the command line: `./make-voice.sh list`, `./make-voice.sh builtin <id> <name>
<built-in id>`, and `./make-voice.sh clip <id> <name> <recording> <transcript>` (set `TTS_URL`).

## Settings

Everything has a working default. Set the `docker-compose.yml` values in a `.env` file next to it.

| Setting | Default | What |
|---|---|---|
| `ZONOS3_MODELS_DIR` | `./models/zonos3` | ZONOS3's model files (`fetch-models.sh` downloads there; it reads the same variable). |
| `ZONOS3_CUDA_ARCHS` | `120` | The GPU architecture ZONOS3 is compiled for. Rebuild after changing it. |
| `CLONE_MODE` | `embedding` | How a voice's clip is used: `embedding` (its speaker embedding; fast, small) or `reference` (the clip itself as the prompt: often closer to the recording, slower, and needs `./fetch-models.sh --reference`, about 5.6 GB more). |
| `TTS_VOICES_DIR` | `./tts-voices` | The voices: `voices.json` and the reference clips. |
| `TTS_SOURCES_DIR` | `./tts-sources` | Long recordings uploaded in the studio, with their transcripts. |
| `ASR_MODELS_DIR` | `./asr-models` | The downloaded speech-to-text model. |
| `STUDIO_ROOT_PATH` | `/tts-studio` | The path your web server serves the studio at (`STUDIO_ROOT_PATH=` to open it on port 7861). |
| `ASR_MODEL`, `ASR_IDLE_UNLOAD`, `ASR_BATCH` | `openai/whisper-large-v3-turbo`, `600`, `8` | Speech-to-text, as in the Breeze repository. |

## How fast

On 4 CPU cores (no GPU), ZONOS3 here speaks about as fast as it plays: 9.1 s of speech in 9.5 s. A GPU is far faster;
the studio's **Try text** shows the real-time factor on yours. The first request in a voice also registers its
clip (a few seconds; once per voice while the server runs).

## Updating ZONOS3

ZONOS3 is pinned in two places: the commit (`ZONOS3_COMMIT` in `docker-compose.yml` and `mac/setup.sh`) and the
model revision (`REVISION` in `fetch-models.sh`). Change them together, to a commit and a revision from the same
day, then `./fetch-models.sh`, `docker compose up -d --build`, and check `adapter/test_app.py` still passes
(`pytest adapter/test_app.py`, with `fastapi`, `httpx`, `numpy` and `python-multipart`). The revision can stay as it
is when the Hub's `gguf/` folder hasn't changed since (compare its files at the two revisions): the commit is then
only the server's code, and `./fetch-models.sh` has nothing to do.

## Files

| File | What |
|---|---|
| `API.md` | Every HTTP endpoint: the fields each request takes, the responses, headers and errors, and the differences from the Breeze server |
| `docker-compose.yml` | The four services |
| `fetch-models.sh` | Downloads and checks ZONOS3's model files and built-in voices |
| `zonos3/` | ZONOS3's ggml server built with CUDA: `Dockerfile`, `start.sh` |
| `adapter/` | The reader's API in front of ZONOS3: `app.py`, its tests (`test_app.py`), `Dockerfile`, `requirements.txt` |
| `studio/` | The voice studio: `app.py` (Gradio 6.30), `Dockerfile`, `requirements.txt` |
| `asr/` | Speech-to-text for the studio: `server.py` (Whisper through transformers), `Dockerfile`, `requirements.txt` |
| `mac/` | Running natively on an Apple Silicon Mac: `setup.sh`, `start.sh`, `install-service.sh` |
| `nginx.conf.example`, `Caddyfile.example` | Proxy `/tts/`, `/tts-voices/` and `/tts-studio/` on the reader's site |
| `make-voice.sh` | Creates voices from the command line |
| `models/`, `tts-voices/`, `tts-sources/`, `asr-models/` | Created on first use; git-ignored |

The studio and speech-to-text service come from epub-reader-tts-server; the studio's Breeze-only parts (designing
voices from descriptions) are replaced by built-in voices and a language field.
