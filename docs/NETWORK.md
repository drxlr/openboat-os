# Reaching the boat from somewhere else

This is the part that makes it more than a laptop program. The requirement is narrow: the
boat is in a marina, you are not, and one has to reach the other without opening anything to
the internet.

## Use a private overlay network, and nothing else

[Tailscale](https://tailscale.com), WireGuard or ZeroTier. Adding the boat is one command:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname boat
```

Then, from anywhere on that network:

```bash
SIGNALK_URL=http://boat:3000 python3 -m openboat.server
```

Why this and not the alternatives:

- **No port forwarding.** Marina wifi and mobile networks sit behind carrier-grade NAT, so
  there is usually no port to forward even if it were wise.
- **Nothing is exposed.** OpenBoat and Signal K have no authentication worth the name. On a
  private network that is fine, because there is nothing to reach them from. On the public
  internet it would be careless.
- **It survives the network changing.** Marina wifi today, a phone hotspot tomorrow, a
  4G router next season. The overlay name does not change.

Set an ACL so the boat can be *read* from home but the boat cannot reach back into your
house. It is a small computer in a locker on a public pontoon; treat it as one.

## Connectivity at the berth

| | Cost | Reliability | Good for |
|---|---|---|---|
| Marina wifi | usually included | poor to fair | alongside only, fine to start |
| A phone hotspot when aboard | nothing extra | good | tracks and logs, no remote access when away |
| A 4G/5G router with its own SIM | monthly | good, and **always on** | the actual case: checking on the boat when nobody is there |

Only the third gives the thing that is really wanted — asking *is the boat alright* in
February and getting an answer.

## Under way

Coastal mobile coverage is good close in and gone offshore. Design for the gap: the boat
computer logs locally and the rest reads what it can when the link returns. Nothing in this
project assumes a live connection.
