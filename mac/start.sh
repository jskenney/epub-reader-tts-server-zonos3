#!/bin/sh
# Runs the voice server on an Apple Silicon Mac, with the same ports and API as docker-compose.yml:
#
#   7860  the adapter: the reader's API                         proxy /tts/ here
#   7861  the voice studio, and the voices folder at /voices/   proxy /tts-studio/ and /tts-voices/ here
#   1919  ZONOS3-Mini's MLX server (127.0.0.1 only; the adapter talks to it)
#   7862  speech-to-text for the studio (127.0.0.1 only)
#
# Run mac/setup.sh first. Logs go to mac/logs/. Ctrl-C stops them all; if one of them stops, the others are
# stopped too (so a launchd agent, mac/install-service.sh, restarts them together).
#
# Settings, from the environment or from mac/settings.env (lines such as TTS_BIND=127.0.0.1):
#   TTS_BIND          the address the adapter and the studio listen on (default 0.0.0.0, every interface, as with
#                     Docker; 127.0.0.1 when the web server runs on this Mac)
#   CLONE_MODE        embedding (default) or reference (needs ./fetch-models.sh --reference)
#   ZONOS3_MODELS_DIR, TTS_VOICES_DIR, TTS_SOURCES_DIR, ASR_MODELS_DIR, STUDIO_ROOT_PATH, ASR_MODEL,
#   ASR_IDLE_UNLOAD, ASR_BATCH, ASR_DEVICE   as in docker-compose.yml (README, "Settings")
set -eu

cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)
if [ -f settings.env ]; then
    set -a
    . ./settings.env
    set +a
fi
if [ ! -x zonos3/.venv/bin/python ] || [ ! -x .venv/bin/python ]; then
    echo "Run mac/setup.sh first." >&2
    exit 1
fi

BIND=${TTS_BIND:-0.0.0.0}
MODELS=${ZONOS3_MODELS_DIR:-$ROOT/models/zonos3}
VOICES=${TTS_VOICES_DIR:-$ROOT/tts-voices}
SOURCES=${TTS_SOURCES_DIR:-$ROOT/tts-sources}
ASR_MODELS=${ASR_MODELS_DIR:-$ROOT/asr-models}
[ -f "$MODELS/backbone-ptq.gguf" ] || { echo "No models in $MODELS: run ./fetch-models.sh" >&2; exit 1; }
mkdir -p "$VOICES" "$SOURCES" "$ASR_MODELS" logs

pids=""
stop() {
    trap - INT TERM EXIT
    for pid in $pids; do kill "$pid" 2>/dev/null || true; done
    wait 2>/dev/null || true
}
trap 'stop; exit 0' INT TERM
trap stop EXIT

# ZONOS3 on MLX (it picks up the decoder, speaker encoder and steering files next to the backbone).
(cd zonos3 && exec .venv/bin/python -m zonos3_mlx "$MODELS/backbone-ptq.gguf" --host 127.0.0.1 --port 1919 \
    --tts-default-voices-dir "$MODELS/voices") >>logs/zonos3.log 2>&1 &
pids="$pids $!"

env ZONOS_URL=http://127.0.0.1:1919 CLONE_MODE="${CLONE_MODE:-embedding}" ADAPTER_HOST="$BIND" ADAPTER_PORT=7860 \
    PYTHONUNBUFFERED=1 .venv/bin/python "$ROOT/adapter/app.py" >>logs/adapter.log 2>&1 &
pids="$pids $!"

env ASR_HOST=127.0.0.1 ASR_PORT=7862 HF_HOME="$ASR_MODELS" \
    ASR_MODEL="${ASR_MODEL:-openai/whisper-large-v3-turbo}" ASR_IDLE_UNLOAD="${ASR_IDLE_UNLOAD:-600}" \
    ASR_BATCH="${ASR_BATCH:-8}" PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false \
    .venv/bin/python "$ROOT/asr/server.py" >>logs/asr.log 2>&1 &
pids="$pids $!"

env TTS_URL=http://127.0.0.1:7860 ASR_URL=http://127.0.0.1:7862 VOICES_DIR="$VOICES" SOURCES_DIR="$SOURCES" \
    STUDIO_ROOT_PATH="${STUDIO_ROOT_PATH-/tts-studio}" STUDIO_HOST="$BIND" STUDIO_PORT=7861 \
    PYTHONUNBUFFERED=1 GRADIO_ANALYTICS_ENABLED=False \
    .venv/bin/python "$ROOT/studio/app.py" >>logs/studio.log 2>&1 &
pids="$pids $!"

echo "Started: the adapter on $BIND:7860 (ready when http://127.0.0.1:7860/health says \"ok\"), the studio on"
echo "$BIND:7861, ZONOS3 on 127.0.0.1:1919, speech-to-text on 127.0.0.1:7862. Logs: mac/logs/. Ctrl-C stops them."
while :; do
    for pid in $pids; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "A service stopped (see mac/logs/); stopping the others." >&2
            exit 1
        fi
    done
    sleep 5
done
