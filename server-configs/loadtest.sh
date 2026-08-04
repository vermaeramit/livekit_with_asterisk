#!/bin/bash
# Usage: ./loadtest.sh <concurrent> [stagger_seconds] [extension]
#
# Originates N calls and reports what happened. Each run is self-contained: it
# prints its own p50/p95 from Postgres, so two runs compare directly.
N=${1:-5}
STAGGER=${2:-0.7}
# 709 is the load-test campaign - same pipeline, transfer disabled. On 700 the
# agent hands every synthetic call to a human inside the first turn, so the
# calls end in seconds and the run measures near-simultaneous single-turn calls
# rather than sustained concurrency.
EXTEN=${3:-709}
OUT=/tmp/loadtest-$(date +%H%M%S)-n${N}.log

# Scopes the result query to this run. Taken before anything is originated.
START_TS=$(docker exec postgres psql -U aivoice -d aivoice -tAc "SELECT now()")
START_EPOCH=$(date +%s)

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
# Interactive bash prints a "Terminated" job notice when the sampler is killed,
# which buries the results under a wall of the subshell's own source.
disown $SAMPLER 2>/dev/null

# The originate result used to go to /dev/null. Half a run can fail to start and
# the script would still report a clean, quiet test on whatever did.
FAILED=0
for i in $(seq 1 "$N"); do
  resp=$(docker exec asterisk asterisk -rx \
    "channel originate Local/s@loadtest extension ${EXTEN}@from-internal" 2>&1)
  case "$resp" in
    *Failed*|*Unable*|*"No such"*|*Error*)
      FAILED=$((FAILED + 1))
      echo "originate $i FAILED: $resp" | tee -a "$OUT" ;;
  esac
  sleep "$STAGGER"
done
echo "originated $((N - FAILED))/$N into ${EXTEN} at $(date +%T)" | tee -a "$OUT"
if [ "$FAILED" -gt 0 ]; then
  echo "!! $FAILED originate(s) failed - the numbers below cover only what started" \
    | tee -a "$OUT"
fi

# Wait for the calls to finish rather than guessing. The old fixed 90s sleep was
# too short once concurrency rose, and silently truncated the results.
echo "waiting for channels to drain..." | tee -a "$OUT"
for _ in $(seq 1 120); do
  sleep 5
  live=$(docker exec asterisk asterisk -rx "core show channels count" 2>/dev/null \
         | grep -oP '^\d+(?= active channel)' || echo 0)
  [ "${live:-0}" -eq 0 ] && break
done
sleep 5   # let the last end_call write land
kill $SAMPLER 2>/dev/null; wait $SAMPLER 2>/dev/null
echo "end $(date +%T)" | tee -a "$OUT"

echo | tee -a "$OUT"
echo "=== worker errors during the run ===" | tee -a "$OUT"
# The INSTANCE units, not `aivoice-agent`. That plain unit is disabled, so the
# original filter reported a clean run no matter what the workers actually did.
# `fallback` is in here on purpose. Disabling the chains for a benchmark would
# measure something we do not ship; the answer is to make a fallback firing
# visible instead, because a run where the primary degraded under load and the
# secondary quietly took over looks identical to a healthy one otherwise.
# Matching a bare "ERROR" also matched `error=None` inside every healthy call's
# usage summary - the HEALTHY case - so this section filled with four screens of
# noise and a real error would have been scrolled off the top by it. Match the
# log's own level field instead.
journalctl -u 'aivoice-agent@*' --since "-10min" --no-pager 2>/dev/null \
  | grep -E '"level": "ERROR"|Traceback|full capacity|below capacity|DECLINED|[Ff]allback' \
  | tail -20 | tee -a "$OUT"
echo "(nothing listed above means no errors and no fallbacks fired)" | tee -a "$OUT"

echo | tee -a "$OUT"
echo "=== where the calls went ===" | tee -a "$OUT"
# Asterisk logs in UTC while journalctl and `date` here show IST, so the window
# is computed rather than typed. Getting that wrong silently returns zero
# matches, which reads as "nothing happened" - it cost a full round of guessing.
UTC_FROM=$(date -u -d "@$((START_EPOCH - 5))" '+%Y-%m-%d %H:%M')
UTC_TO=$(date -u '+%Y-%m-%d %H:%M')
dialled=$(docker exec asterisk sed -n "/${UTC_FROM}/,/${UTC_TO}/p" \
          /var/log/asterisk/debug.log 2>/dev/null | grep -c "Routing to LiveKit AI")
recorded=$(docker exec asterisk sed -n "/${UTC_FROM}/,/${UTC_TO}/p" \
          /var/log/asterisk/debug.log 2>/dev/null | grep -c "recording id:")
fellback=$(docker exec asterisk sed -n "/${UTC_FROM}/,/${UTC_TO}/p" \
          /var/log/asterisk/debug.log 2>/dev/null | grep -c "AI UNAVAILABLE")
printf "requested %s | reached the dialplan %s | recordings started %s | no agent, sent to human %s\n" \
       "$N" "$dialled" "$recorded" "$fellback" | tee -a "$OUT"
echo "(dialplan < requested means originate is failing; dialplan > db rows means the agent did not pick them up)" \
     | tee -a "$OUT"

echo | tee -a "$OUT"
echo "=== result ===" | tee -a "$OUT"
docker exec postgres psql -U aivoice -d aivoice -X -P pager=off -c "
WITH scoped AS (
    SELECT id, duration_ms, end_reason, limit_hit
      FROM calls WHERE started_at > '${START_TS}'::timestamptz
)
SELECT count(*)                                              AS calls,
       count(*) FILTER (WHERE end_reason = 'error')          AS errors,
       count(*) FILTER (WHERE limit_hit IS NOT NULL)         AS limit_hits,
       round(avg(duration_ms) / 1000.0, 1)                   AS avg_sec,
       (SELECT count(*) FROM turns t JOIN scoped s ON s.id = t.call_id
         WHERE t.total_ms IS NOT NULL)                       AS timed_turns,
       (SELECT round(percentile_cont(0.50) WITHIN GROUP (ORDER BY t.total_ms))
          FROM turns t JOIN scoped s ON s.id = t.call_id)    AS p50_ms,
       (SELECT round(percentile_cont(0.95) WITHIN GROUP (ORDER BY t.total_ms))
          FROM turns t JOIN scoped s ON s.id = t.call_id)    AS p95_ms,
       (SELECT max(t.total_ms) FROM turns t JOIN scoped s ON s.id = t.call_id)
                                                             AS worst_ms
  FROM scoped;" | tee -a "$OUT"

# Where the time went. A rising eou is our machine - VAD and turn detection run
# locally; rising llm/tts is the provider. This is what to read when a run is
# slower than the one before it.
docker exec postgres psql -U aivoice -d aivoice -X -P pager=off -c "
SELECT round(percentile_cont(0.50) WITHIN GROUP (ORDER BY t.eou_ms))      AS eou_ms,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY t.llm_ttft_ms)) AS llm_ms,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY t.tts_ttfb_ms)) AS tts_ms
  FROM turns t JOIN calls c ON c.id = t.call_id
 WHERE c.started_at > '${START_TS}'::timestamptz AND t.total_ms IS NOT NULL;" \
 | tee -a "$OUT"

echo "log: $OUT"
