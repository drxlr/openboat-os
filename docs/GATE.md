# The gate

```bash
python3 -m openboat.gate                 # → http://localhost:8749
```

Everywhere else this project says: put it on a private overlay network and nothing else.
[docs/NETWORK.md](NETWORK.md) still says it, and for one person with one laptop it is the
right answer. It stops being the right answer the moment somebody else needs to see the
boat — the yard for a fortnight, a delivery skipper for a weekend, a partner who wants the
snag list on their phone and is not going to install Tailscale to get it.

The gate is the one component that has a password on it. Everything behind it keeps having
none, and keeps binding loopback.

## The chain

```
browser
  → deploy/vercel/api/relay.py     a fixed public address; holds nothing, decides nothing
  → cloudflared                    a tunnel whose hostname changes on every restart
  → openboat.gate                  the login, the sessions, and who may see which boat
  → openboat.server (per boat)     read-only, unauthenticated, on localhost
  → openboat.snag                  the write surface, unauthenticated, on localhost
```

Every link is checked, and each one is checking a different thing:

- **The relay proves it is the relay.** A `trycloudflare.com` hostname is a public address.
  With `OPENBOAT_GATE_SECRET` set, the gate answers nothing that does not carry
  `X-OpenBoat-Gate` with that exact value — no page, no error, no body. The tunnel is
  reachable by anybody and useful to nobody but the relay.
- **The browser proves it is a person.** A signed cookie, thirty days, `HttpOnly`,
  `SameSite=Lax`, `Secure` when the public URL is https.
- **The boats prove nothing at all.** That is why they must never be on the tunnel
  themselves. The gate is the only thing it reaches.

**The rule that keeps this honest: the tunnel points at the gate and at nothing else.** A
tunnel to `openboat.server` is a boat's papers on the open internet, because that server
says in its own docstring that it has no authentication and never will.

## Configuration

| | |
|---|---|
| `OPENBOAT_GATE_BOATS` | `key=origin` pairs, comma-separated: `alpha=http://127.0.0.1:8750,beta=http://127.0.0.1:8747`. The keys are the same folder keys `openboat.snag` uses |
| `OPENBOAT_SNAG_ORIGIN` | The snag service, default `http://127.0.0.1:8752` |
| `OPENBOAT_USERS` | **Required.** Path to the users file; created by the first `invite` |
| `OPENBOAT_SESSION_SECRET` | **Required**, 32+ characters. Signs cookies and invite links. Change it and everybody is signed out |
| `OPENBOAT_GATE_SECRET` | The shared secret with the relay. Optional, and not optional in practice: without it the tunnel is an open door to a login form |
| `OPENBOAT_PUBLIC_URL` | e.g. `https://openboat.example.vercel.app`. Used to print invite links, and to set `Secure` on cookies when it is https |
| `OPENBOAT_BOATS` | The directory of boat folders, as the snag service reads it. Needed only for share links, which read and append to the boat's own file in this process |

Boat keys matter more than they look. The gate, the dashboards and the snag service must
all call a boat the same thing, or the console loads and every photograph on it is a 404
for a picture that exists.

## Users

```bash
python3 -m openboat.gate secret                  # a session secret to paste into the env
python3 -m openboat.gate invite you@example.org --name "A Name" --boat alpha --role owner
python3 -m openboat.gate users                   # who exists, and who has not set a password
python3 -m openboat.gate boats you@example.org --add beta --remove alpha
python3 -m openboat.gate revoke you@example.org
```

`invite` adds the user with **no password**, and prints a link:

```
https://openboat.example.vercel.app/invite/eyJlbWFpb…
```

They open it, choose a password of ten characters or more, and are signed in. The link is
good for seven days and re-issuable — run `invite` again for a fresh one, which does not
disturb the password of somebody who already set theirs.

**No password is ever typed on this command line, printed, or recovered.** One typed as an
argument is in a shell history and in the process list of everybody on the machine while it
runs; one that can be mailed is one that lives in a mailbox. Passwords are stored as
`pbkdf2_sha256` at 600,000 iterations with a per-user salt, and verified in constant time.

The users file is JSON, written atomically, and **re-read on every request**. So `revoke`
takes effect on that person's next click rather than at the next restart, which is the only
useful definition of the word.

## Roles, and what crew can do

| Role | Sees |
|---|---|
| `admin` | Every boat in `OPENBOAT_GATE_BOATS` |
| `owner` | The boats in their own record |
| `crew` | The boats in their own record |

`owner` and `crew` are the same reach today; they are separate words because the difference
is about to matter and a role added later cannot be added retroactively to records already
written.

What anybody signed in can do is what the console can do: read the boat, and file or update
a snag. Two things are **forced** on the way through rather than trusted, and they are the
reason this is a relay and not a port forward:

- `boat=<key>` is rewritten from the path onto every snag request — in the query *and* in
  the JSON body — replacing whatever was asked for. Crew on one boat cannot file against
  another by editing one field.
- `who=<their name>` is rewritten from the session onto every POST, in the query *and* as
  the `by` field of the body, because the routes behind this read it from one place or the
  other. Attribution is what makes a fault followable-up six weeks later, and a name the
  sender chooses is not attribution.

Run the snag service behind the gate with **no `OPENBOAT_SNAG_PEOPLE`**. With people
configured it wants its own `?k=` key on every route and takes the sender's name from that,
and the gate holds no such key. Authentication happens once, at the gate; the service
behind it stays on localhost with none.

A boat somebody may not see returns **404, not 403**. Which boats a gate serves is not a
stranger's business, and a status code that distinguishes "no such boat" from "not your
boat" hands out that list to anybody with a keyboard.

## Your account

The console has an **Account** page, at `#account`, reached from the name in the sidebar.
Everything on it is about the person rather than the boat — the boat key in the address is
only where the console lives — and every route behind it is `/b/<key>/account/…`, so it
takes the same two checks as every other path there: signed in, and a boat you may see.

Anybody signed in can:

- **Change their display name.** One to sixty characters. It is the name that goes on every
  fault they file, because the gate writes it onto every snag from the session.
- **Change their password.** The current one is verified first, ten wrong ones in fifteen
  minutes and that account's password route stops answering — the same count in the same
  window as the login form, because this is the other route that verifies a password. The
  answer sets a fresh cookie, so the browser that did it stays signed in.
- **Sign out everywhere else.** Every user record carries a `session_gen`, an integer that
  goes into the session cookie when it is issued and is checked against the record on every
  request. Bumping it ends every other session at once: another browser, an old laptop, a
  phone in a locker that is switched off. There is no table of sessions to keep and nothing
  to expire — the cookie is checked on the next click, whenever that is. A record with no
  `session_gen` and a cookie with no `gen` are both nought, so nothing issued before this
  existed stops working.

**Changing a password does not sign the other devices out.** They are two buttons because
they are two intentions, and somebody tidying up a password should not be surprised by a
phone that has forgotten them. If a password is being changed *because* it leaked, press
both.

An `admin` also gets **People** on the same page, and `owner` and `crew` get a **404** from
every route behind it — not a 403, for the reason every other refusal here is a 404.

| | |
|---|---|
| The list | Everybody with a login: name, email, role, boats, whether a password is set or the invite is still outstanding, and when the record was made. No hash leaves the process, not even to an admin |
| Invite | Email, name, role and boats. It creates the record with **no password** and hands back the link — the same link `python3 -m openboat.gate invite` prints, from the same function — with Copy and a WhatsApp button beside it |
| Re-issue | A fresh link for somebody who has not set a password yet. Refused for somebody who has: that would be a password reset one person can perform on another's account from a browser. The command line can still do it, standing at the machine |
| Boats | Which boats one person may see. Only keys this gate is configured with; an unknown one is refused rather than quietly dropped |
| Revoke | Removes the login. An admin cannot revoke themselves — the last door locked with the key on the inside is a shell on the machine to fix |

Every change re-reads the users file, alters one record and writes the whole thing back with
the same atomic writer the command line uses, stamping `updated_by` and `updated_at`. The
file is shared with a terminal, so a stale list saved back over it would undo a `revoke`
somebody typed thirty seconds ago.

**Every write here is a JSON POST and has to say so**, or it is a **415**. `SameSite=Lax`
already keeps the session cookie off a cross-site POST; the content type is the second lock.
A cross-site form can send `application/x-www-form-urlencoded`, `multipart/form-data` or
`text/plain` and nothing else, so a route that accepts only `application/json` cannot be
driven by one — which still holds on a browser that is loose about Lax. Bodies are capped at
64 kB and parsed strictly; anything that is not a JSON object is a 400.

Served plainly off a boat's own machine, with no gate in front, the Account page says so and
stops. There is nobody signed in there and nothing to change.

## Sharing one fault with somebody who has no login

`owner` and `admin` can hand out a link to a **single fault**: the yard, a friend's
engineer, whoever is actually holding the spanner. They need no account, and creating one
for them would be worse than useless — an account is a thing somebody has to remember to
revoke.

```
POST /b/<key>/snag/api/share    {"when": "…", "label": "Jo at the yard", "days": 30}
  → {"url": "https://…/s/<token>", "id": "3f8a91c2", "until": "2026-10-07T18:00:00+03:00"}
```

`crew` get a 404 from that route, the same 404 they get for a boat that is not theirs.

The token is a signed statement — boat, fault, expiry, label, id — with an HMAC over the
gate's own `$OPENBOAT_SESSION_SECRET`, so it is checkable with no table behind it. On its
own that would be a link nobody could take back, so the grant is also **written into the
boat's own file** as a follow-up on the fault (`**Share:** <id> <expiry> <label>`), and
revoking is one more line (`**Unshare:** <id>`). A token whose id the file does not list,
or lists as taken back, opens nothing. Who was given access is therefore readable by a
person, in the file the fault is in.

What the link reaches:

| | |
|---|---|
| `GET /s/<token>` | That one fault: the note, its photographs, its follow-ups, who it is with, and a form |
| `GET /s/<token>/photo?name=` | One photograph, relayed to the snag service with the boat forced from the token, and only a photograph filed against *this* fault |
| `POST /s/<token>/update` | One follow-up — `review` or `fixed`, with a note and a name. Thirty a day at most |

Nothing else. The boat never comes from the request, no other fault is reachable, and every
answer is filed as `Jo (via share link 3f8a91c2)` — what somebody said their name was, and
which link they said it through.

This is `openboat/share.py`, hung off `MOUNTS` below rather than wired into the router. It
reads and appends to the boat's own files **in the gate's process**, so the gate must be
started with `$OPENBOAT_BOATS` (or `$OPENBOAT_PROFILE`) pointing at them, exactly as the
snag service is. Without it every share route is an honest 404 rather than a half-drawn
page. The full description is in [docs/SNAGS.md](SNAGS.md).

## Routes

| | |
|---|---|
| `GET /login`, `POST /login` | The form. Ten failures for one email in fifteen minutes and that email gets a 429 |
| `GET /logout` | Clears the cookie |
| `GET/POST /invite/<token>` | Choose a password. Expired or tampered → 404 |
| `GET /` | To the first boat you may see, or to the login |
| `GET /me`, `GET /b/<key>/me` | Who you are and which boats you have, as JSON |
| `GET /b/<key>/` | The console, for that boat |
| `GET /b/<key>/index.html`, `/jobs.html`, `/windy.html` | The pages the console holds in a frame, served from this package |
| `GET /b/<key>/account/users` | Everybody with a login, for an `admin`. Anybody else: 404 |
| `POST /b/<key>/account/name`, `/password`, `/logout-all` | Your own record. JSON only, or 415 |
| `POST /b/<key>/account/invite`, `/reissue`, `/revoke`, `/boats` | The people page, for an `admin`. Anybody else: 404 |
| `GET /b/<key>/api/…`, `/paper`, `/reports/…` | Relayed to that boat's dashboard |
| `POST /b/<key>/api/logbook` | The only write a boat's own server accepts, and the only one relayed. The allow-list is that one route spelled out |
| `GET/POST/OPTIONS /b/<key>/snag/…` | Relayed to the snag service, with the boat and the name forced |
| `POST /mcp/…` | The MCP server, at a permanent address. No session — see below |
| `POST /b/<key>/snag/api/share` | Mint a link to one fault. `owner` and `admin`; `crew` gets a 404 |
| `GET /s/<token>`, `/s/<token>/photo`, `POST /s/<token>/update` | One shared fault, with no login. See above |
| `GET /vendor/…` | The vendored Bootstrap, unauthenticated: the login page needs its stylesheet |

Every answer carries `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options:
nosniff` and `Referrer-Policy: same-origin`. Log lines are a method, a route and a status —
never a cookie, never a token, never a query string.

## The MCP address

```
OPENBOAT_MCP_ORIGIN=http://127.0.0.1:8748     # the default
```

`POST /mcp/<rest>` goes to `openboat.mcp_http` with the prefix stripped and nothing else
touched, so `/mcp/<token>/mcp` arrives as `/<token>/mcp`. That is the point of it: an
assistant configured with a connector URL needs one address that keeps working, and a
`trycloudflare.com` hostname is not one.

**It carries no session, deliberately.** An assistant is not a browser and holds no cookie;
it holds a token, in a Bearer header or as the first segment of the path, and
`openboat.mcp_http` checks that token itself. The gate is a wire between the two rather than
a second opinion about it. `OPENBOAT_GATE_SECRET` still applies, as it does to every route.

Because the path can be the credential, **these requests are never logged past the prefix**
— the log line says `POST /mcp/… 200` and nothing more.

`GET` and `DELETE` on that prefix are a 405 saying to use streamable HTTP with plain JSON
answers. The server-sent-event stream a client may also ask for cannot work through this
chain: the relay in front of the gate is a serverless function that ends when it answers,
and it cannot hold a connection open for minutes.

## The console behind it

`console.html` works out where it is mounted from its own address: `""` when the dashboard
serves it at `/console.html`, `/b/<key>` behind the gate. Every fetch is built from that,
and so is the snag service's address — which behind the gate is not a second port on
another origin but `/b/<key>/snag`, relayed. Nothing on the page is configured for one case
or the other, and nothing about it changes when it is served plainly.

The Dashboards view works the same way. Its frames are `ROOT + "/jobs.html"` and the
rest, the framed pages each derive their own `ROOT` from `location.pathname` exactly as the
console does, and their `/api/…` calls come back through the gate as `/b/<key>/api/…`. So a
page inside a frame inside the console is inside the same session, with no second login and
no second copy of the file. The generated reports need none of this — they are static HTML
with no fetches, and only have to arrive.

One route is answered by the gate rather than relayed: `/b/<key>/snag/api/boats` returns the
single boat whose page it is. The snag service knows every boat on the machine and would
name them all, and the phone page inside the frame asks it for a list.

Behind the gate the navbar also grows a boat switcher, the reader's name and a sign-out
link, from `/me`, and the name in the sidebar becomes the way in to the Account page above.
Served plainly there is no account to show and none appears.

The console makes writes through exactly two functions in its shell, and a test pins that
there is no third: `snagPost()`, which reaches the snag service, and `gatePost()`, which
reaches these account routes. No view opens a fetch of its own, and nothing on the page ever
posts to a boat's own `/api/`.

## Extending it

`openboat.gate.MOUNTS` is a list of `(prefix, handler)` pairs, consulted after the gate
secret and the session cookie but before any built-in route:

```python
def share(handler, user, rest):        # rest is the path after the prefix
    ...
    return True                        # truthy: handled. falsy: fall through.

openboat.gate.MOUNTS.append(("/s/", share))
```

That is the seam a share-link module hangs off — public `/s/<token>` pages, `POST
/b/<key>/share` — without editing the router.

## Running it

Three processes on the always-on machine: the dashboards, the snag service, the gate, plus
the tunnel. All of them bind loopback except the snag service, which binds the LAN so a
phone on the boat's wifi can still reach it directly.

```xml
<!-- ~/Library/LaunchAgents/com.openboat.gate.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.openboat.gate</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-m</string><string>openboat.gate</string>
    <string>8749</string>
  </array>
  <key>WorkingDirectory</key><string>/opt/openboat/openboat-os</string>
  <key>EnvironmentVariables</key><dict>
    <key>PYTHONPATH</key><string>/opt/openboat/openboat-os</string>
    <key>OPENBOAT_USERS</key><string>/opt/openboat/config/users.json</string>
    <key>OPENBOAT_SESSION_SECRET</key><string>…</string>
    <key>OPENBOAT_GATE_SECRET</key><string>…</string>
    <key>OPENBOAT_GATE_BOATS</key>
      <string>alpha=http://127.0.0.1:8750,beta=http://127.0.0.1:8747</string>
    <key>OPENBOAT_SNAG_ORIGIN</key><string>http://127.0.0.1:8752</string>
    <key>OPENBOAT_PUBLIC_URL</key><string>https://openboat.example.vercel.app</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/opt/openboat/log/gate.log</string>
</dict></plist>
```

```xml
<!-- ~/Library/LaunchAgents/com.openboat.tunnel.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.openboat.tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/opt/openboat/openboat-os/scripts/gate_tunnel.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>OPENBOAT_EDGE_CONFIG_ID</key><string>ecfg_…</string>
    <key>VERCEL_TEAM_ID</key><string>team_…</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/opt/openboat/log/tunnel.log</string>
</dict></plist>
```

`KeepAlive` on the tunnel is the whole restart story: `gate_tunnel.py` exits non-zero when
cloudflared dies, launchd starts a fresh one, and the new hostname is published to Edge
Config before anybody notices the old one stopped working. The current URL is also written
to `~/.config/openboat/gate-url` for whoever is looking.

The relay side — deploying it, and the three environment variables it needs — is in
[deploy/vercel/README.md](../deploy/vercel/README.md).

## What this does not make safe

It is a password on a door, not a security review. `openboat.server` and `openboat.snag`
are still unauthenticated services that trust whatever reaches them, and they are one
misconfigured tunnel away from the internet at all times. Check what the tunnel points at
before you start it, and check it again after you change a port.

Nothing here touches `openboat/control/`. The gate has no route into it, cannot arm a helm
and cannot send a command; control stays exactly where [README.md](../README.md) says it
is, behind three deliberate acts by somebody standing on the boat.

## Connecting an assistant, per boat

`/mcp/…` relays to one MCP process, and one process reads whatever it was pointed at — so
one token there opens every boat the process knows. To hand a boat's owner an assistant
address that reaches **their boat only**, run a second `openboat.mcp_http` on that boat's
profile alone (no `OPENBOAT_BOATS`, its own `OPENBOAT_MCP_TOKEN`) and tell the gate:

    OPENBOAT_MCP_ORIGINS=beta=http://127.0.0.1:8746
    OPENBOAT_MCP_CONNECT=beta=https://your.public.host/mcp/beta/<that token>/mcp

`/mcp/<key>/<token>/mcp` then reaches that process with the key stripped, and the console's
**MCP connect** page (`/b/<key>/mcp-connect`) shows the configured address to an `owner` or
`admin` of the boat — never to `crew`, who get the same 404 as for a boat that is not
theirs. The address is the credential: it is not logged, and it is not in `/me`.
