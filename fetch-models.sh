#!/bin/sh
# Downloads ZONOS3-Mini's model files (the GGUF build, Apache-2.0) into ./models/zonos3 next to this script,
# at the Hugging Face revision below, and checks each against the SHA-256 the Hub lists for it.
#
#   ./fetch-models.sh               the backbone, decoder and speaker encoder, steering and the built-in voices
#                                   (about 1.2 GB): cloning from a clip's speaker embedding (CLONE_MODE=embedding)
#   ./fetch-models.sh --reference   also the reference encoders (about 5.6 GB more), for CLONE_MODE=reference
#                                   (the clip itself as the prompt: closer to the clip, slower)
#
# Safe to run again: files already there and correct are kept. Needs curl, python3 and sha256sum (or shasum).
set -eu

REPO=Zyphra/ZONOS3-Mini
REVISION=d2d142a86b1e6aeb4252679673c86ecd86177bf8   # 2026-11-14 (README: "Updating ZONOS3")
DEST="${ZONOS3_MODELS_DIR:-$(cd "$(dirname "$0")" && pwd)/models/zonos3}"

FILES="backbone-ptq.gguf zac-keep2.gguf spk-encoder.gguf steer.gguf"
[ "${1:-}" = "--reference" ] && FILES="$FILES zac-encoder.gguf ssl-teacher.gguf"

for tool in curl python3; do
    command -v "$tool" >/dev/null || { echo "$tool is needed" >&2; exit 1; }
done
if command -v sha256sum >/dev/null; then sha() { sha256sum "$1" | cut -d' ' -f1; }
else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi

mkdir -p "$DEST"
LISTING=$(mktemp)
trap 'rm -f "$LISTING"' EXIT
# Every file of gguf/ at the revision, with its size and (for large files) its SHA-256: "path<TAB>sha256<TAB>size".
curl -fsS "https://huggingface.co/api/models/$REPO/tree/$REVISION/gguf?recursive=true" | python3 -c '
import json, sys
for f in json.load(sys.stdin):
    if f["type"] == "file":
        print(f["path"], (f.get("lfs") or {}).get("oid", ""), f.get("size", 0), sep="\t")
' >"$LISTING"

fetch() {  # $1 = path under gguf/, $2 = destination
    line=$(grep -F "$(printf '%s\t' "gguf/$1")" "$LISTING" | head -1)
    [ -n "$line" ] || { echo "gguf/$1 isn't in $REPO at $REVISION" >&2; exit 1; }
    want=$(printf '%s' "$line" | cut -f2)
    size=$(printf '%s' "$line" | cut -f3)
    if [ -f "$2" ] && [ "$(wc -c <"$2" | tr -d ' ')" = "$size" ] && { [ -z "$want" ] || [ "$(sha "$2")" = "$want" ]; }; then
        return
    fi
    echo "  $1 ($((size / 1000000)) MB)"
    mkdir -p "$(dirname "$2")"
    url="https://huggingface.co/$REPO/resolve/$REVISION/gguf/$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1")"
    curl -fsSL --retry 3 -o "$2.part" "$url"
    if [ -n "$want" ] && [ "$(sha "$2.part")" != "$want" ]; then
        rm -f "$2.part"
        echo "$1 doesn't match its SHA-256; run this again" >&2
        exit 1
    fi
    mv "$2.part" "$2"
}

echo "ZONOS3-Mini at ${REVISION%"${REVISION#????????}"} into $DEST"
for f in $FILES; do fetch "$f" "$DEST/$f"; done
echo "  the built-in voices"
grep -F "$(printf 'gguf/voices/')" "$LISTING" | cut -f1 | while IFS= read -r p; do
    fetch "${p#gguf/}" "$DEST/${p#gguf/}"
done
echo "Done."
