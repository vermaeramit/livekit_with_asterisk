#!/bin/bash
# Usage: ./loadtest.sh <concurrent> [stagger_seconds]
N=${1:-5}
STAGGER=${2:-0.7}
OUT=/tmp/loadtest-$(date +%H%M%S)-n${N}.log

echo "=== load test: $N concurrent, ${STAGGER}s stagger ===" | tee "$OUT"
echo "start $(date +%T)" | tee -a "$OUT"

# sampler
(
  printf "%-8s %6s %6s %7s %7s %7s %6s %6s\n" \
         TIME LOAD1 CHANS ASTERISK LIVEKIT SIP AGENT ROOMS
  while :; do
    read -r l1 _ < /proc/loadavg
    chans=$(docker exec asterisk asterisk -rx "core show channels count" 2>/dev/null \
            | grep -oP '^\d+(?= active channel)' || echo 0)
    stats=$(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' 2>/dev/null)
    ast=$(echo "$stats"  | awk '$1=="asterisk"{print $2}')
    lk=$(echo "$stats"   | awk '$1=="livekit"{print $2}')
    sip=$(echo "$stats"  | awk '$1=="sip"{print $2}')
    agent=$(ps -C python -o %cpu= 2>/dev/null | awk '{s+=$1} END {printf "%.0f%%", s}')
    rooms=$(docker exec redis redis-cli --scan --pattern 'room*' 2>/dev/null | wc -l)
    printf "%-8s %6s %6s %7s %7s %7s %6s %6s\n" \
           "$(date +%T)" "$l1" "$chans" "$ast" "$lk" "$sip" "$agent" "$rooms"
    sleep 2
  done
) >> "$OUT" &
SAMPLER=$!

for i in $(seq 1 "$N"); do
  docker exec asterisk asterisk -rx \
    "channel originate Local/s@loadtest extension 700@from-internal" > /dev/null
  sleep "$STAGGER"
done
echo "all $N originated $(date +%T)" | tee -a "$OUT"

sleep 90
kill $SAMPLER 2>/dev/null
echo "end $(date +%T)" | tee -a "$OUT"
echo
echo "=== capacity warnings during test ==="
journalctl -u aivoice-agent --since "-3min" --no-pager \
  | grep -E "full capacity|below capacity|ERROR|Traceback" | tail -20
echo
echo "log: $OUT"
tail -50 "$OUT"
