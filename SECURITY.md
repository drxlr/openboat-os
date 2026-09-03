# Security

## Reporting a vulnerability

Open a [private security advisory](https://github.com/drxlr/openboat-os/security/advisories/new)
on GitHub. Please do not open a public issue for a vulnerability.

There is no bounty, and this is a volunteer project — but a report about something that could
put a boat or its crew at risk will be read quickly.

## The threat model, stated plainly

**OpenBoat has no authentication of its own.** The HTTP server and the dashboard assume that
anything able to reach them is allowed to. This is a deliberate simplification, and it is
only safe under one condition: the network boundary is somewhere else.

Put it on a private overlay network — Tailscale, WireGuard, ZeroTier — and reach it by that
network's name. Do not port-forward it. Do not put it on a public IP. Marina wifi is a shared
network with strangers on it, and a boat computer answering on it is a boat computer anyone
alongside can read. See `docs/NETWORK.md`.

**What an attacker gets if they reach it anyway:** your boat's live position, its sensor
readings, its logbook and its track history. That is a burglary aid — it says where a
valuable object is, and whether anyone is aboard. Treat position data as the sensitive thing
it is.

**What they cannot get:** control of the boat. Nothing in this repository writes to a Signal
K server, so there is no command path to hijack. If you have installed a separate control
plugin, its own gating applies and it is your responsibility to understand it.

## Signal K credentials

Give OpenBoat a **read-only** Signal K user. It never needs more, and a token that cannot
write is a token that cannot be abused into writing. Signal K's own access control is
deny-by-default; keep it that way.

## Secrets

`boat.toml`, `.env*`, `security.json` and anything under `.local/` are gitignored. Keep them
that way. A private repository is not the same as a safe one: it can be forked, made public
by accident, or read by anyone you later add as a collaborator.

## Data that leaves your boat

By default, two things go out over the internet:

- **Coordinates to Open-Meteo**, to fetch a forecast. Those are the coordinates in your
  profile's `forecast_point`, which is offshore, not your berth.
- **Nothing else.** There is no telemetry, no analytics, no phone-home, and no account.

If you connect an AI assistant over the MCP server, whatever it reads goes to that model
provider under their terms. That is your choice to make, and it is worth making consciously.
