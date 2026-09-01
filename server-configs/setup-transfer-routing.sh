#!/bin/bash
# Route a transfer to the campaign's own dialler.
#
#   bash /srv/aivoice/server-configs/setup-transfer-routing.sh
#
# Needs migration 033 and setup-asterisk-odbc.sh to have run.
# Safe to re-run; both files are backed up and neither is appended to twice.
#
#
# HOW A TRANSFER FINDS ITS DIALLER
#
#   agent      transfer_to = sip:c1@<us>          c1 = campaign 1
#   livekit    REFER to our Asterisk
#   from-livekit  _c. matches, strips the c, asks the database
#   database   transfer_routes -> "dialler-76^5000"
#   Asterisk   Dial(IAX2/dialler-76/5000)
#
# The campaign id is in the target rather than the extension because the
# extension cannot identify anything: two campaigns may both use 5000, on
# different diallers. That was the whole reason for this.
#
# The existing _X. route is left alone. A campaign with a plain sip:5000@ target
# keeps working exactly as it does today, and moves over by being given a
# dialler in the console. Nothing has to change on the same day.

set -euo pipefail

say() { printf '\n== %s\n' "$1"; }
bak() { [ -f "$1" ] && cp -a "$1" "$1.bak-$(date +%Y%m%d-%H%M%S)"; }

# ── 1. the lookup ────────────────────────────────────────────────────────────
say "func_odbc.conf"
bak /etc/asterisk/func_odbc.conf
python3 - <<'PY'
import re
p = "/etc/asterisk/func_odbc.conf"
s = open(p).read()
s = re.sub(r"\n\[TRANSFER\]\n(?:[^\[]*)", "\n", s)   # never stack two
s = s.rstrip() + """

; ODBC_TRANSFER(<campaign id>) -> "peer^extension", or empty.
;
; campaign_id is cast to text rather than the argument to bigint: a target that
; is not a number then returns nothing, instead of raising inside the driver and
; taking the transfer down with a message nobody will find.
;
; The view is the only thing this role can read - see migration 033.
[TRANSFER]
dsn=aivoice
readsql=SELECT peer || '^' || extension FROM transfer_routes WHERE campaign_id::text = '${SQL_ESC(${ARG1})}'
"""
open(p, "w").write(s)
print("[TRANSFER] written")
PY

# ── 2. the route ─────────────────────────────────────────────────────────────
say "extensions.conf"
bak /etc/asterisk/extensions.conf
python3 - <<'PY'
import re
p = "/etc/asterisk/extensions.conf"
s = open(p).read()
if "TRANSFER for campaign" in s:
    print("already present, left alone"); raise SystemExit

anchor = "exten => _X.,1,NoOp(<-- TRANSFER to ${EXTEN}"
assert s.count(anchor) == 1, f"anchor found {s.count(anchor)} times - nothing changed"

block = """; Per-campaign transfer. The target is sip:c<campaign id>@..., and the dialler
; and extension come from the database - see migration 033. Two campaigns can
; use the same extension on different diallers, which is why the campaign id is
; what travels and not the extension.
exten => _c.,1,NoOp(<-- TRANSFER for campaign ${EXTEN:1})
 same => n,Set(ROUTE=${ODBC_TRANSFER(${EXTEN:1})})
 same => n,GotoIf($["${ROUTE}" = ""]?noroute)
 same => n,Set(PEER=${CUT(ROUTE,^,1)})
 same => n,Set(DEST=${CUT(ROUTE,^,2)})
 same => n,NoOp(<-- routing to IAX2/${PEER}/${DEST})
 same => n,Dial(IAX2/${PEER}/${DEST},300,Tt)
 ; Reached only when the dial did not connect. Without this the caller who just
 ; heard "please hold" gets silence and a dropped call.
 same => n,NoOp(<-- transfer failed: ${DIALSTATUS})
 same => n(noroute),Answer()
 same => n,Wait(1)
 same => n,Playback(sorry-youre-having-problems)
 same => n,Hangup()

"""
open(p, "w").write(s.replace(anchor, block + anchor))
print("_c. added ahead of the existing routes")
PY

# ── 3. does it work ──────────────────────────────────────────────────────────
say "reloading"
asterisk -rx "module reload func_odbc.so" >/dev/null
asterisk -rx "dialplan reload" >/dev/null
sleep 1

say "the route Asterisk will take for campaign 1"
asterisk -rx "dialplan show c1@from-livekit"

say "what the database answers for campaign 1"
asterisk -rx 'dialplan eval function ODBC_TRANSFER(1)'

echo
echo "An empty answer above is expected until a dialler exists and a campaign"
echo "points at it. Add the peer to iax.conf, then the row and the campaign in"
echo "the console."
