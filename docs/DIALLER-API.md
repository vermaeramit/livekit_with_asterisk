# Capacity API

One endpoint, for the dialler to ask how many more calls a campaign can take
before placing them.

It is read-only. Nothing here starts, stops or changes anything.

---

## Endpoint

```
GET  http://<host>:8080/api/dialler/capacity?campaign_id=<your campaign id>
```

| Header | Value |
|---|---|
| `X-API-Key` | the key issued for your client |

`campaign_id` is **your** campaign id — the one you use in your own system, as
registered against a campaign on our side. One of our campaigns may have several
of your ids; each one answers about that campaign.

### Response — `200`

```json
{
  "activeCalls": 3,
  "capacity": 10,
  "availableSlots": 7,
  "timestamp": "2026-09-11T10:15:30.123Z"
}
```

| Field | Meaning |
|---|---|
| `activeCalls` | calls in progress on this campaign right now |
| `capacity` | the most it will run at once |
| `availableSlots` | how many more it will accept — **this is the number to act on** |
| `timestamp` | when this was true, UTC, milliseconds, `Z` |

`availableSlots` is never negative. It can be `0` while `activeCalls` is higher
than `capacity` — that happens when the limit is lowered while calls are up, and
it still means "send no more".

---

## Errors

Each one is a different thing to do, so please branch on the status code rather
than on the message text. The messages may be reworded; the codes will not.

| Code | Means | What to do |
|---|---|---|
| `401` | the key is missing, wrong, or has been revoked | Stop and raise it with us. Retrying will not help. |
| `404` | `campaign_id` is not registered | Stop for that id and raise it with us — it has not been set up, or it was removed. |
| `409` | registered, but not accepting calls — suspended client, disabled campaign, or no limit configured | Do not dial. Retry later; the message says which of the three it is. |
| `5xx` | our fault | Retry with backoff. |

A `409` is a deliberate state on our side, not a fault. It clears when we change
the configuration, so polling it occasionally is correct — but it will not clear
on its own within seconds.

**Please do not treat an error as "there is room".** If the answer cannot be
given, the safe reading is zero.

---

## Polling

Ask before a batch rather than before every single call; the number is a snapshot
and is stale the moment it is returned. Anything from a few seconds apart upwards
is fine.

If `availableSlots` comes back lower than you need, you can still send the calls
— nothing is rejected at the SIP level. Callers beyond the limit hear a hold
message and are connected as slots free, and are handed to a human if they wait
too long. That path costs nothing in speech or language processing while they
wait, but the caller is waiting, which is the reason for asking first.

---

## The key

One key per client, issued by us. It is shown once when it is created and is not
recoverable afterwards — we hold only a hash of it, so if it is lost we issue a
new one rather than look the old one up.

It begins `dlr_` so that it is recognisable if it ever turns up somewhere it
should not.

Tell us if it needs rotating. A new key replaces the old one immediately, so the
changeover is not seamless: the old key stops working the moment the new one is
issued.

---

## Example

```bash
curl -s -H "X-API-Key: dlr_xxxxxxxx" \
  "http://<host>:8080/api/dialler/capacity?campaign_id=CMP-4471"
```

```json
{"activeCalls":2,"capacity":10,"availableSlots":8,"timestamp":"2026-09-12T07:41:02.918Z"}
```
