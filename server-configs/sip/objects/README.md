# LiveKit SIP objects

Deployed at `/opt/aivoice/sip/objects/`. These are applied with the `lk` CLI, not
by a config reload — they live in Redis, which is why enabling Redis persistence
mattered: a reboot once wiped both and every call rang and died with SIP 486,
before any dispatch rule lookup and therefore with nothing in any log naming the
cause.

The JSON files carry **no comments**. `lk` parses them as protobuf JSON, which
rejects unknown fields — a `_comment` key makes the whole file unusable. Hence
this README.

```bash
set -a; . /opt/aivoice/.env; set +a
export LIVEKIT_URL=http://localhost:7880    # lk wants http://, the agent wants ws://

lk sip inbound list
lk sip dispatch list
```

## `inbound-trunk.json`

`numbers` is deliberately absent, so the trunk matches any called number.
Security comes from `allowed_addresses`: only our own Asterisk can use it.

### `headers_to_attributes`

Carries the dialler's per-call context through to the agent. The chain is:

| Hop | What happens |
|---|---|
| Dialler → Asterisk | IAX2 variables (`IAXVAR(cus_name)`, …) |
| Asterisk → livekit-sip | `PJSIP_HEADER(add,X-Cus-Name)` in `[recsetup]` |
| livekit-sip → room | this mapping, into participant attributes |
| Agent | reads `dialer.*`, prompts with the conversational half |

Attribute names are prefixed `dialer.` on purpose. LiveKit owns the `sip.`
namespace — `sip.callIDFull`, `sip.trunkPhoneNumber` — and colliding with it
would be a bug that only appears the day LiveKit adds a field.

> ⚠️ LiveKit documents these attributes as populated **asynchronously**, so they
> may be absent the instant the participant joins. The agent reads them late and
> logs whether they arrived; if that log starts showing nothing on calls that
> should carry context, the fix is the `lk.sip.GetRemoteHeaders` RPC, not a
> longer sleep.

## Changing a trunk

There is no in-place edit in the CLI worth relying on. Recreating gives a **new
trunk ID**, and the dispatch rule references it — so both must be recreated
together, in this order:

```bash
lk sip inbound  delete  <OLD_TRUNK_ID>
lk sip inbound  create  /opt/aivoice/sip/objects/inbound-trunk.json   # note the new ST_...
lk sip dispatch delete  <OLD_RULE_ID>
lk sip dispatch create --name lab-dispatch --trunks <NEW_TRUNK_ID> --individual call
```

Doing the trunk without the rule leaves calls arriving and matching nothing —
the same symptom as the reboot, and just as quiet.
