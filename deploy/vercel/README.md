# The relay

A fixed public address that forwards to wherever the boat's tunnel currently is. It holds
no data and makes no decisions — see [docs/GATE.md](../../docs/GATE.md) for the whole
chain and why it is shaped this way.

```
browser → this function → cloudflared tunnel → openboat.gate → the boat's own services
```

## Deploy it

```bash
vercel --cwd deploy/vercel                # a preview, to check it
vercel --cwd deploy/vercel --prod         # the real one
```

`vercel.json` rewrites every path to `api/relay.py`, so there is one function and no
routing table to keep in step with the gate's.

## What to set on the project

| | |
|---|---|
| `OPENBOAT_GATE_SECRET` | The shared secret. **Must be byte-identical** to the one the gate runs with, or every request is a 401 with an empty body |
| `EDGE_CONFIG` | The Edge Config connection string, `https://edge-config.vercel.com/<id>?token=<t>`. Vercel sets this for you when you attach an Edge Config to the project |
| `OPENBOAT_ORIGIN` | Optional fallback: a fixed tunnel URL, used when there is no Edge Config or no `origin` item in it |

Create the Edge Config first — it is what makes the address survive a tunnel restart.
Attach it to the project, and note its id (`ecfg_…`) for the Mac side.

## The Mac side

`scripts/gate_tunnel.py` opens the tunnel and writes its URL into that Edge Config item on
every start. It needs:

| | |
|---|---|
| `OPENBOAT_EDGE_CONFIG_ID` | The `ecfg_…` id |
| `VERCEL_TEAM_ID` | When the project belongs to a team |
| `VERCEL_TOKEN` | An API token. Falls back to the token `vercel login` already left on the machine |

Run it under launchd with `KeepAlive`, so a dead tunnel is replaced and the new hostname
republished without anybody noticing. The example plists are in
[docs/GATE.md](../../docs/GATE.md).

## Checking it

```bash
curl -s https://<your-project>.vercel.app/_relay/health
{"ok": true, "origin_known": true}
```

`origin_known: false` means nothing has told this address where the boat is — the tunnel
is down, or `gate_tunnel.py` is not running, or the Edge Config is not attached. The health
route deliberately does not say *what* the origin is: the tunnel hostname is the one
address the shared secret exists to protect.

Everything else you might want to know is on the gate's own side. This function logs
nothing but what Vercel logs for any request, and it is the wrong place to debug a login.
