#!/bin/sh
# Run by MixMonitor once a recording is closed.  $1 is the path without extension.
#
# Converts the 8 kHz WAV to Opus and removes the original: roughly a tenth of the
# size, and every browser can play it. At 20 calls a day the difference is small;
# at 500 it is 45 GB a quarter versus 4.
#
# Deliberately quiet on success and loud on failure - this runs detached from the
# call, so a failure has no other way to surface.
set -u

BASE="$1"
WAV="${BASE}.wav"
OPUS="${BASE}.opus"

[ -f "$WAV" ] || exit 0

# An empty or near-empty file means the call never carried audio. Keeping it
# would show an unplayable recording in the console.
if [ "$(stat -c %s "$WAV" 2>/dev/null || echo 0)" -lt 2048 ]; then
    rm -f "$WAV"
    exit 0
fi

if ffmpeg -nostdin -loglevel error -y -i "$WAV" \
        -c:a libopus -b:a 24k -ar 16000 -ac 1 "$OPUS" 2>/tmp/rec-ffmpeg.err; then
    rm -f "$WAV"
else
    # Keep the WAV. A playable big file beats no recording at all.
    logger -t rec-postprocess "opus conversion failed for ${BASE}: $(cat /tmp/rec-ffmpeg.err)"
fi
