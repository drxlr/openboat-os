# The companion

A boat accumulates knowledge that lives nowhere useful. The survey is a PDF in a mail
thread. The manual is a PDF on a manufacturer's site. The yard invoice that says what was
*actually* replaced is a photo. The long note somebody wrote the evening they finally
worked out why one side ran hot is in a chat log. All of it is true, all of it is specific
to this hull, and none of it is reachable from the pontoon at seven in the morning.

The companion is the part of OpenBoat that fixes that. It is two things that look like one:

- **`Ask` in the dashboard** — retrieval over your own documents, beside the live readings,
  with a place to record what you checked. Works offline, on the tablet, with wet hands.
- **The MCP server** — the same material, offered to an assistant that has vision and
  reasoning but has never seen your boat.

## The problem it is actually solving

This was designed against a real corpus: roughly thirty conversations between one boat
owner and a general-purpose assistant, over about a year of ownership, covering
diagnosis, procedures, part identification, quotes and paperwork. The patterns were
consistent enough to design from.

**The same facts get asked for, forever.** Engine variant. Serial. Drive type. Whether it
is raw-water or freshwater cooled. What has already been tried. What the last quote was.
These do not change, they are written down somewhere, and they were re-supplied at the
start of nearly every new thread. The sharpest moment in the corpus is an owner insisting
he had *already* said which engine he has — to an assistant with no way to know he had.

**Questions arrive as fragments, usually with a photo.** Three words and an image of a
part is a complete question. A form with required fields would have caught almost none of
the real ones.

**They are asked mid-job.** Multimeter in hand, reporting live voltage readings, being
walked through a bypass, reporting the result minutes later. Not research — work.

**The worst answers came from live web searching.** Threads where the assistant browsed
dealer sites and forums for a boat-specific fact were the longest and least useful. The
fact was usually already in the owner's own papers.

Four design rules follow, and they are why this is retrieval rather than a chatbot:

| The rule | Because |
|---|---|
| Never re-ask what the boat already knows | The single largest source of friction in the corpus |
| Answer from the boat's own papers first | Live search was the worst-performing path for anything vessel-specific |
| Quote, with the file and the line | A confident sentence with no source under it is the one dangerous thing this project can produce |
| Record what was checked, with the readings at that moment | "Impeller looks fine" is worth little in a year; the same line with hours and coolant temperature is worth a lot |

## Pointing it at your documents

```toml
[knowledge]
docs = [
  "~/boat/survey.md",
  "~/boat/engine-log.md",
  "~/boat/invoices.md",
]
logbook = "logbook.jsonl"
```

Markdown, read where they lie. Nothing is copied, indexed into a database or uploaded, and
a file you edit by hand is a file the next question reads correctly. Paths are relative to
the profile.

Ranking is BM25 over words — old, unglamorous, and entirely offline, because the moment you
most need to know which cap to unscrew is the moment you are least likely to have signal.

**It searches in two languages at once.** Boat papers are rarely written in one: the survey
in one language, the yard invoice in another, the owner's notes switching mid-sentence.
Word-frequency ranking cannot cross that on its own — measured on a real boat's files, a
German query scored 18.4 on the right passage while the identical English question scored
nothing at all. So the *query* is widened through a marine glossary before it is run
(`EQUIV` in `openboat/knowledge.py`), and the same question now finds the same passage in
either language. The glossary is a plain list; add to it freely.

## Recording what you checked

```bash
python3 -m openboat.logbook --add "impeller" "vanes intact, no cracking"
python3 -m openboat.logbook                       # read it back
```

One append-only JSONL file, one line per check, with whatever the boat was reading at that
moment captured automatically. Append-only is deliberate: a maintenance record you can
quietly revise is not evidence of anything.

Verdicts are four words — `ok`, `watch`, `act`, `noted` — and not a number, because a scale
invites an argument about whether something is a 3 or a 4, and the only decision that
follows from a check is whether the boat goes out.

## Connecting an assistant

**Locally, over a pipe** — for a model on the same machine:

```bash
claude mcp add openboat -- python3 -m openboat.mcp
```

**Over the network** — for a hosted assistant such as the ChatGPT app, whose connectors
reach out to a URL:

```bash
export OPENBOAT_MCP_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
python3 -m openboat.mcp_http                       # 127.0.0.1:8748, SSE at /sse/
```

It **refuses to start without a token**, and it binds to localhost. Reaching it from the
internet is a tunnel you set up deliberately — Tailscale Funnel, Cloudflare Tunnel, an
ngrok URL — because the moment a boat's server listens on a public interface is a decision,
not a default. Give the connector the `/sse/` URL and the bearer token.

OpenAI's connector documentation asks that a server implement two read-only tools named
`search` and `fetch`; both are provided, mapping onto the same document library.

### What the assistant can and cannot do

Eleven tools. Ten read. The eleventh, `log_check`, appends one line to your maintenance
log and can do nothing else.

**There is no route to the helm.** `openboat/control/` is not imported here, and
`tests/test_control_gate.py` parses this module's syntax tree and fails the build if an
import, a `Helm(...)`, or a tool whose name is a steering verb ever appears. A hosted model
with a connector to your boat must not be able to steer it — not because the gate would
refuse, but because the gate should never be asked.

**Think about what you are exposing.** Your papers, your berth, your position and your
engine's history are a fair description of your boat and your movements. That is exactly
what makes the companion useful, and exactly why the token is not optional.
