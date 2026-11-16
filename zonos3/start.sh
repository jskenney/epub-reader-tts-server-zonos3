#!/bin/sh
# Starts zonos3-server on the models fetch-models.sh downloaded (mounted at /models). The server picks up the
# companions it finds next to the backbone (spk-encoder.gguf, steer.gguf, and zac-encoder.gguf with
# ssl-teacher.gguf for CLONE_MODE=reference).
set -eu
M=/models
for f in backbone-ptq.gguf zac-keep2.gguf; do
    [ -f "$M/$f" ] || { echo "$M/$f is missing: run ./fetch-models.sh on the host first" >&2; exit 1; }
done
exec /opt/zonos3/zonos3-server "$M/backbone-ptq.gguf" --zac "$M/zac-keep2.gguf" \
    --tts-default-voices-dir "$M/voices" --host 0.0.0.0 --port 1919 "--${ZONOS3_DEVICE:-gpu}"
