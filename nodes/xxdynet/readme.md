# Philips Dynet Lighting Gateway (fixed)

Local version of the official `Philips Dynet Lighting Gateway` recipe, fixing the
crash:

```
TypeError: chr(): 1st arg can't be coerced to int
  File ".../script.py", line 148, in sendDynaliteMessage
    raw_packet = '%s%s%s%s%s%s%s' % ('\x1c',    # SYNC
```

## Cause

`sendDynaliteMessage` read `area`, `data0` and `preset` straight out of the packet
dict and passed them to `chr()` with no coercion or presence check. A Nodel
`'type': 'integer'` schema drives the web UI only — it does not validate or coerce —
so the handler receives whatever the caller actually sent. Three different inputs
raise this same message:

| Input | How it happens |
|---|---|
| `"6"` — a string | dashboard binding or REST caller sending quoted JSON numbers |
| `None` | a blank field in a **Custom messages** parameter entry |
| `127.5` — a float | no operator error at all, see below |

The float case needs nothing to be misconfigured. The auto-generated channel
slider handler computed `dynArg = 255 - (arg*255/100)`; a slider value of `50.0`
rather than `50` makes that `127.5`, and Jython's `chr()` rejects floats even when
they are whole numbers.

Also note `console.info('%s' % v)` renders `6` and `u'6'` identically, so the log
line printed just before the traceback looks correct while the value is not.

## Changes from the official recipe

1. Added `asByte(name, value, default=None)` — coerces via `int(round(float(v)))`,
   substitutes the default for `None`/empty, and raises an error naming the
   offending field if it is missing, non-numeric, or outside 0–255.
2. `sendDynaliteMessage` now takes all six fields through `asByte`. `area`,
   `data0` and `preset` are mandatory; `data1`/`data2` default to 0 and `join` to
   255, as before.
3. Added a check that `preset >= 1`, since it goes on the wire as `preset-1`.
4. The channel slider handler coerces its argument:
   `dynArg = 255 - (asByte('level', arg, 0)*255/100)`.

Everything else — discovery, labelling, custom messages, the keepalive, the
checksum, the Nodel transport forwarding — is untouched.

## Verified

Tested against a Nodel 2.2.1 host (Jython 2.5.4) with a TCP listener capturing the
gateway side:

- int, string and float inputs now all produce identical wire bytes
  (`1c 06 64 00 00 00 ff 7b` for area 6 / data0 100 / preset 1)
- a float slider value of `50.0` sends `1c 01 80 13 03 05 ff 49` instead of crashing
- missing and out-of-range fields now fail with a message naming the field

The checksum implementation was checked against captured output and is correct as
originally written.

## Installing

Copy this whole folder into the production host's recipes directory, then create a
node from it, or use **Recipes → apply** on the existing node to replace its script.

Keep it out of `recipes/nodel-official-recipes/` — that tree is a git clone kept in
sync by the Recipes Sync node and local edits there will be overwritten.

Parameters to set after creating the node:

- **TCP address** — the gateway's `host:port`, e.g. `192.168.1.24:2001`. Nothing
  starts until this is set.

## Gotcha worth knowing

The Status event only reaches level 0 on *received* traffic (it reports level 2 if
nothing arrives for roughly 5 minutes). A gateway that never sends unsolicited
messages will therefore report as offline even when the link is healthy — check the
`Last Dynalite message` debug event before chasing it.
