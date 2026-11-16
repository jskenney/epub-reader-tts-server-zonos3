#!/bin/sh
# Sets up the voice server on an Apple Silicon Mac, without Docker (README, "On a Mac").
#
#   1. Fetches ZONOS3-Mini at the commit below into mac/zonos3 (its MLX server runs the model on the GPU).
#   2. Creates the Python environments: ZONOS3's own (mac/zonos3/.venv, with uv) and mac/.venv (the adapter, the
#      studio and speech-to-text).
#   3. Downloads the models (../fetch-models.sh: about 1.2 GB, once) into models/zonos3.
#
# Needs: macOS 14 or later on Apple Silicon with 16 GB or more, and uv, ffmpeg and git (brew install uv ffmpeg git).
# Safe to run again: it only redoes what is missing or out of date.
set -eu

ZONOS3_URL="https://github.com/Zyphra/ZONOS3-Mini.git"
ZONOS3_COMMIT="96d9e0ec1e39083ed336ac78f24e3dbc93721c0f"   # as in docker-compose.yml

cd "$(dirname "$0")"
ROOT=$(cd .. && pwd)

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "mac/setup.sh needs an Apple Silicon Mac (this machine: $(uname -s) $(uname -m))." >&2
    exit 1
fi
for tool in uv ffmpeg git curl; do
    command -v "$tool" >/dev/null || { echo "$tool is missing: brew install uv ffmpeg git" >&2; exit 1; }
done

echo "== ZONOS3-Mini at $(printf %.7s "$ZONOS3_COMMIT")"
[ -d zonos3/.git ] || git init -q zonos3
if [ "$(git -C zonos3 rev-parse -q --verify HEAD 2>/dev/null || true)" != "$ZONOS3_COMMIT" ]; then
    git -C zonos3 fetch -q --depth 1 "$ZONOS3_URL" "$ZONOS3_COMMIT"
    git -C zonos3 checkout -q -f "$ZONOS3_COMMIT"
fi
# (The workspace refers to the forced aligner's package by path, so uv needs the submodule even though this
# setup doesn't use speech editing.)
git -C zonos3 submodule update -q --init --depth 1 third_party/fusealign-multilingual

echo "== ZONOS3's Python environment (mac/zonos3/.venv)"
(cd zonos3 && uv sync -q --locked --package zonos3-mlx)

echo "== Python environment for the adapter, the studio and speech-to-text (mac/.venv)"
[ -x .venv/bin/python ] || uv venv -q --python 3.12
uv pip install -q --python .venv/bin/python -r "$ROOT/adapter/requirements.txt" -r "$ROOT/studio/requirements.txt" \
    -r "$ROOT/asr/requirements.txt" torch==2.9.1 numpy

echo "== Models"
sh "$ROOT/fetch-models.sh"

mkdir -p "$ROOT/tts-voices" "$ROOT/tts-sources" "$ROOT/asr-models" logs
echo
echo "Done. Start the server with: mac/start.sh   (or at every login: mac/install-service.sh)"
