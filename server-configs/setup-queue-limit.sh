#!/bin/bash
# Hold calls over a campaign's limit, and let them in as slots free.
#
#   bash /srv/aivoice/server-configs/setup-queue-limit.sh
#
# Needs migrations 046 and 047, and setup-asterisk-odbc.sh to have run.
# Safe to re-run; both files are backed up and neither is appended to twice.
#
#
# WHY THE WAIT IS HERE AND NOT IN THE AGENT
#
# Everything this adds happens BEFORE the Dial to LiveKit. A call waiting at
# that point has no room, no agent job, no speech recogniser and no language
# model behind it - it costs a channel and a file being read off disk. Move the
# wait to the other side of the Dial and every held caller is paying for STT,
# TTS and an LLM to sit in silence, at the exact moment the system is already
# at its limit.
#
#
# WHAT IT READS
#
#   dialled 700 -> ODBC_QUEUECFG(700) -> "1^5^/opt/.../83d5..^8^90^human^"
#                                         campaign, limit, audio, gap,
#                                         max wait, action, goodbye audio
#
# An empty limit means unlimited. An empty ANSWER means the same: if the
# database is unreachable the campaign runs exactly as it did before this
# feature existed. A lookup that cannot answer must never become a fuse that
# stops every call.
#
#
# WHAT IT DOES NOT DO
#
# This is not a FIFO. Whoever tests the count in the second a slot frees takes
# it, so somebody who has waited two minutes can be passed by somebody who has
# waited five seconds. Real ordering, and "you are third in line", are what
# app_queue exists for; that was considered and deliberately left for later.
#
# The message cannot be interrupted, so its length is the real granularity: a
# slot freeing one second into a seven-second message is taken six seconds
# later. Shorter message, shorter tail.

set -euo pipefail

say() { printf '\n== %s\n' "$1"; }
bak() { [ -f "$1" ] && cp -a "$1" "$1.bak-$(date +%Y%m%d-%H%M%S)"; }

# ── 1. the lookup ────────────────────────────────────────────────────────────
say "func_odbc.conf"
bak /etc/asterisk/func_odbc.conf
python3 - <<'PY'
import re
p = "/etc/asterisk/func_odbc.conf"
s = open(p, encoding="utf-8").read()
s = re.sub(r"\n\[QUEUECFG\]\n(?:[^\[]*)", "\n", s)   # never stack two
s = s.rstrip() + """

; ODBC_QUEUECFG(<dialled extension>) -> the campaign's call limit, or empty.
;
; One column, already joined. The field list lives in the view - see migration
; 047 - so adding a field later is a migration rather than an edit to this file
; on a box that is carrying calls.
[QUEUECFG]
dsn=aivoice
readsql=SELECT cfg FROM queue_routes WHERE did = '${SQL_ESC(${ARG1})}'
"""
open(p, "w", encoding="utf-8").write(s)
print("[QUEUECFG] written")
PY

# ── 2. the wait ──────────────────────────────────────────────────────────────
say "extensions.conf"
bak /etc/asterisk/extensions.conf
python3 - <<'PY'
p = "/etc/asterisk/extensions.conf"
s = open(p, encoding="utf-8").read()
if "the call limit" in s:
    print("already present, left alone"); raise SystemExit

anchor = " same => n,Set(CALLERID(num)=${CALLERID(num)})\n same => n,Dial(PJSIP/${EXTEN}@livekit,25,b(recsetup^s^1))"
assert s.count(anchor) == 1, f"anchor found {s.count(anchor)} times - nothing changed"

block = """ ; ── the call limit ────────────────────────────────────────────────────────
 ; All of this is BEFORE the Dial on purpose. A call waiting here has not
 ; reached LiveKit, so it is costing a channel and a file read - no room, no
 ; agent job, no STT stream, no LLM request.
 same => n,Set(QCFG=${ODBC_QUEUECFG(${EXTEN})})
 ; No row, no limit. If the database is unreachable this behaves exactly as it
 ; did before the feature existed, which is the only acceptable failure: a
 ; limit is a cost control, not a fuse that stops every call.
 same => n,GotoIf($["${QCFG}" = ""]?ai)
 same => n,Set(QCAMP=${CUT(QCFG,^,1)})
 same => n,Set(QMAX=${CUT(QCFG,^,2)})
 ; Empty is unlimited. Zero is NOT - zero is a campaign somebody has closed on
 ; purpose, and it falls through to the wait and then to the action below.
 same => n,GotoIf($["${QMAX}" = ""]?ai)
 same => n,Set(QAUDIO=${CUT(QCFG,^,3)})
 same => n,Set(QGAP=${CUT(QCFG,^,4)})
 same => n,Set(QWAIT=${CUT(QCFG,^,5)})
 same => n,Set(QACT=${CUT(QCFG,^,6)})
 same => n,Set(QBYE=${CUT(QCFG,^,7)})
 same => n,Set(QUNTIL=$[${EPOCH} + ${QWAIT}])
 same => n,NoOp(--> campaign ${QCAMP}: ${GROUP_COUNT(c${QCAMP}@aiq)} of ${QMAX} in use)

 same => n(qcheck),GotoIf($[${GROUP_COUNT(c${QCAMP}@aiq)} < ${QMAX}]?qtake)
 same => n,GotoIf($[${EPOCH} > ${QUNTIL}]?qover)
 ; Nothing was rendered, so there is nothing to hold them with. Silence on a
 ; loop reads as a broken line; hand them on instead.
 same => n,GotoIf($["${QAUDIO}" = ""]?qover)
 same => n,ExecIf($["${QANS}" != "1"]?Answer())
 same => n,Set(QANS=1)
 same => n,Playback(${QAUDIO})
 ; A second at a time through the gap, so a slot is taken within a second of
 ; freeing instead of at the end of it. The message itself cannot be
 ; interrupted, which is why its length is the real granularity here.
 same => n,Set(QI=0)
 same => n(qgap),GotoIf($[${GROUP_COUNT(c${QCAMP}@aiq)} < ${QMAX}]?qtake)
 same => n,GotoIf($[${EPOCH} > ${QUNTIL}]?qover)
 same => n,Wait(1)
 same => n,Set(QI=$[${QI} + 1])
 same => n,GotoIf($[${QI} < ${QGAP}]?qgap)
 same => n,Goto(qcheck)

 ; Two calls arriving in the same instant can both pass the test above before
 ; either joins, so a limit of 5 can briefly hold 6. That is accepted: the
 ; alternative is join-then-verify-then-leave, and if leaving a group does not
 ; behave the way it reads, every waiting call counts itself and nothing ever
 ; gets in. A cost control may overshoot by one. It may not deadlock.
 same => n(qtake),Set(GROUP(aiq)=c${QCAMP})
 same => n,NoOp(--> slot taken: ${GROUP_COUNT(c${QCAMP}@aiq)} of ${QMAX} on campaign ${QCAMP})
 same => n,Goto(ai)

 same => n(qover),NoOp(--> waited out ${QWAIT}s on campaign ${QCAMP}, action=${QACT})
 same => n,GotoIf($["${QACT}" = "hangup"]?qbye)
 same => n,Goto(from-livekit,800,1)
 same => n(qbye),ExecIf($["${QANS}" != "1"]?Answer())
 same => n,ExecIf($["${QBYE}" != ""]?Playback(${QBYE}))
 same => n,Hangup()

 same => n(ai),NoOp(--> to the agent)
"""
open(p, "w", encoding="utf-8").write(s.replace(anchor, block + anchor))
print("limit check added ahead of the Dial")

# The slot belongs to the AI leg. Once the Dial has failed and the call is on
# its way to a human, holding one is holding a slot nobody is using - and on a
# campaign limited to five, that is a fifth of it.
s = open(p, encoding="utf-8").read()
old = " same => n,NoOp(--> AI UNAVAILABLE - falling back to human  DIALSTATUS=${DIALSTATUS})"
assert s.count(old) == 1, "fallback line not found - the slot will not be released"
open(p, "w", encoding="utf-8").write(s.replace(
    old, old + "\n same => n,Set(GROUP(aiq)=)   ; the AI leg is over; give the slot back"))
print("slot released on the human fallback")
PY

# ── 3. does it work ──────────────────────────────────────────────────────────
say "reloading"
asterisk -rx "module reload func_odbc.so" >/dev/null
asterisk -rx "dialplan reload" >/dev/null
sleep 1

say "what the database answers for extension 700"
asterisk -rx 'dialplan eval function ODBC_QUEUECFG(700)'

say "the route a call will now take"
asterisk -rx "dialplan show 700@from-internal" | head -40

say "who is holding a slot right now"
asterisk -rx "group show channels"

echo
echo "An empty answer for 700 means no campaign_route maps it, or no limit is"
echo "set - either way calls run exactly as they did before. Set a limit in the"
echo "console under Configure -> Limits and run this line again to see it."
