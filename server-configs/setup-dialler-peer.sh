#!/bin/bash
# Turn the inline IAX2 dial string in from-external into a named peer.
#
#   bash /srv/aivoice/server-configs/setup-dialler-peer.sh
#
# Safe to re-run. iax.conf is backed up and the peer is rewritten, never
# stacked - two sections with the same name would authenticate with whichever
# Asterisk read first, which is not a thing to debug at 2am.
#
#
# WHY
#
# The transfer that works today dials
#
#     Dial(IAX2/<user>:<secret>@<host>:4569/${EXTEN},300,Tt)
#
# which is a working call and three problems. The password is in
# extensions.conf, a file that is edited by several people and was going to be
# synced to the repo. There is no name to refer to it by, so the console cannot
# offer it. And it describes one destination, when the point of all this was
# more than one.
#
# This makes it a peer, so it can be dialled as IAX2/<name>/<extension>.
#
#
# THE CREDENTIALS ARE READ OUT OF extensions.conf, NOT TYPED HERE
#
# They are already on the server; asking for them again would put them in
# somebody's shell history and in this file. Nothing below prints the secret.
#
# The old line is LEFT ALONE. Campaigns still on the plain SIP target keep
# using it, and it stays as the way back if the peer turns out not to be
# equivalent. Replace it once a transfer has gone through the peer - not before.

set -euo pipefail

EXT=/etc/asterisk/extensions.conf
IAX=/etc/asterisk/iax.conf

say() { printf '\n== %s\n' "$1"; }

say "reading the working dial string"
# Written to a file rather than a variable that gets echoed by accident, and
# removed on the way out however this exits.
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
python3 - "$EXT" > "$TMP" <<'PY'
import re, sys

src = open(sys.argv[1]).read()

m = re.search(r"Dial\(IAX2/([^:/]+):([^@]+)@([^:/)]+)(?::(\d+))?/", src)
if not m:
    sys.exit("no inline IAX2 dial string found - nothing to convert")
user, secret, host, port = m.group(1), m.group(2), m.group(3), m.group(4) or "4569"

# The host is usually a channel variable set a line above it.
v = re.fullmatch(r"\$\{(\w+)\}", host)
if v:
    s = re.search(r"Set\(_?%s=([^)]+)\)" % re.escape(v.group(1)), src)
    if not s:
        sys.exit(f"host is ${{{v.group(1)}}} and nothing sets it - stopping")
    host = s.group(1).strip()

if not re.fullmatch(r"[\w.-]+", host):
    sys.exit(f"host {host!r} is not a plain address - stopping")

print(user); print(secret); print(host); print(port)
PY
USER=$(sed -n 1p "$TMP"); SECRET=$(sed -n 2p "$TMP")
HOST=$(sed -n 3p "$TMP"); PORT=$(sed -n 4p "$TMP")
echo "peer   : $USER"
echo "host   : $HOST:$PORT"
echo "secret : ${#SECRET} characters, not shown"

# The one thing worth saying out loud about it.
if [ "$SECRET" = "$USER" ]; then
    echo
    echo "!! The password is the same as the username. That is the dialler"
    echo "!! team's to change, not ours, but it should be raised with them."
fi

say "writing the peer into iax.conf"
cp -a "$IAX" "$IAX.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$IAX" "$USER" "$SECRET" "$HOST" "$PORT" <<'PY'
import re, sys
path, user, secret, host, port = sys.argv[1:6]
s = open(path).read()

# Rewritten, not appended.
s = re.sub(r"\n\[%s\]\n(?:[^\[]*)" % re.escape(user), "\n", s)

# Deliberately minimal. Codecs, calltokens and timeouts come from [general],
# which is exactly where the inline dial got them - anything added here would
# be a difference between the peer and the thing it is replacing, and the
# whole point is that there are none.
s = s.rstrip() + f"""

; Outbound trunk to the dialler. type=peer, so this is only somewhere we CALL -
; it cannot be used to authenticate anything coming in. Written by
; setup-dialler-peer.sh from the dial string in extensions.conf.
[{user}]
type=peer
host={host}
port={port}
secret={secret}
; The username sent is the section name, which is why the section is named
; after the account rather than after the dialler.
qualify=yes
"""
open(path, "w").write(s)
print(f"[{user}] written")
PY

chown root:asterisk "$IAX" 2>/dev/null || true
chmod 0640 "$IAX"

say "reloading"
asterisk -rx "iax2 reload" >/dev/null
sleep 2
asterisk -rx "iax2 show peers"

cat <<EOF

Add a dialler on the console's Diallers page with the peer name printed above,
then pick it on the campaign and set the extension.

On the status column: OK means their side answered a qualify poke. UNREACHABLE
does NOT prove calls fail - some diallers ignore pokes from an unregistered
peer - so treat it as a hint and let a real transfer be the answer.
EOF
