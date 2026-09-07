#!/usr/bin/env python3
"""The AI intake inbox: a submitter, never a librarian.

    python3 tests/test_intake.py

An assistant can now bring paper to the boat. That is useful and it is the most dangerous
thing in this project, because `openboat.knowledge` answers questions by quoting documents
with a file and a line — so a model that could put a document in the library could put a
*sentence* in the library, and the next answer would carry a real citation under a false
claim. Four things must stay true, and each of them has a way of quietly stopping:

1. Nothing a model submits is in the library. A person moves it, and their name goes on it.
2. The fetch cannot be talked into opening a connection to something that is not on the
   public internet — checked on every address a name resolves to, and again on every
   redirect.
3. A rejection is remembered after the bytes are gone, so the same URL is not fetched again.
4. An accepted document says where it came from, in every passage anybody can retrieve.

Do not weaken an assertion here to make a change pass. The whole feature is the assertions.
"""

from __future__ import annotations

import io
import os
import socket
import sys
import tempfile
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openboat import ingest, intake, knowledge                          # noqa: E402

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"{PASS if ok else FAIL}  {what}")


def refuses(fn, why: str) -> None:
    """`fn` must raise `Refused` with a sentence in it, not return and not raise anything
    else. A guard that raises `TypeError` is a guard that crashed."""
    try:
        fn()
    except intake.Refused as exc:
        check(bool(str(exc).strip()), f"{why} — refused: {str(exc)[:70]}")
    except Exception as exc:                                            # noqa: BLE001
        check(False, f"{why} — raised {type(exc).__name__} instead of Refused: {exc}")
    else:
        check(False, f"{why} — was NOT refused")


# --------------------------------------------------------------------------------------
# Fixtures. Invented boat, invented manual, invented people: nothing here names anything
# real, which is the rule the pre-commit hook enforces.
# --------------------------------------------------------------------------------------

def a_boat(tmp: Path, key: str = "test-boat") -> Path:
    folder = tmp / key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "boat.toml").write_text('[vessel]\nname = "Test Boat"\n', encoding="utf-8")
    return folder


def tiny_pdf(line: str, extra: bytes = b"") -> bytes:
    """A real one-page PDF with real text in it, built here so the suite needs no fixture
    file and no network. `extra` is appended so a test can bolt active content on."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{start}\n%%EOF\n").encode()
    return bytes(out) + extra


class Env:
    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        # A stray OPENBOAT_PROFILE from the shell would send every write in this file at
        # somebody's real boat. Nothing in this suite may touch one.
        for k in ("OPENBOAT_PROFILE", "OPENBOAT_BOATS"):
            if k not in self.kv:
                self.old.setdefault(k, os.environ.get(k))
                os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, old in self.old.items():
            os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old)


class Fake:
    """A transport standing in for the internet: a scripted answer per URL, no socket.

    Injected rather than monkeypatched so the test says what it is doing. The real
    `_request` is exercised against a real server further down; this is for the paths that
    need a redirect chain or a body of a particular shape.
    """

    def __init__(self, answers: dict):
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, url, timeout=None):
        self.asked.append(url)
        if url not in self.answers:
            raise intake.Refused(f"nothing scripted for {url}")
        status, headers, body = self.answers[url]
        return _Response(status, headers, body)


class _Response(io.BytesIO):
    def __init__(self, status, headers, body):
        super().__init__(body or b"")
        self.status = status
        self.headers = headers


class Server:
    """A real HTTP server on an ephemeral port, so the streaming path meets a real socket."""

    def __init__(self, routes: dict):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                route = self.path.split("?")[0]
                if route not in outer.routes:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, kind, body = outer.routes[route]
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.routes = routes
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def url(self, path):
        return f"https://127.0.0.1:{self.port}{path}"

    def transport(self, url, timeout=None):
        """The real `_request`, with https rewritten to http so a test needs no certificate.

        The guard is what refuses this address, and the guard is tested separately and
        directly. This substitution is about TLS, not about the address check.
        """
        return intake._request(url.replace("https://", "http://", 1), timeout or 5)


#: A guard that permits everything, for the tests whose subject is the *download* rather
#: than the address. Every test whose subject IS the address uses the real one.
def allow(url):
    return None


# --------------------------------------------------------------------------------------
# 1. The address guard. This is the SSRF surface: a URL handed to the boat's own server by
#    a model that read it off a web page. Literal addresses AND names that resolve to them.
# --------------------------------------------------------------------------------------
def test_the_guard_refuses_everything_not_on_the_public_internet() -> None:
    refuses(lambda: intake.check_address("http://example.invalid/a.pdf"),
            "plain http")
    refuses(lambda: intake.check_address("file:///etc/passwd"), "a file: URL")
    refuses(lambda: intake.check_address("ftp://example.invalid/a.pdf"), "ftp")
    refuses(lambda: intake.check_address("https:///a.pdf"), "a URL with no host")

    literal = {
        "127.0.0.1": "loopback",
        "10.1.2.3": "RFC1918 10/8",
        "172.16.9.9": "RFC1918 172.16/12",
        "192.168.1.1": "RFC1918 192.168/16",
        "169.254.169.254": "link-local, the metadata address",
        "100.64.3.4": "carrier-grade NAT",
        "0.0.0.0": "the unspecified address",
        "224.0.0.1": "multicast",
        "[::1]": "IPv6 loopback",
        "[fd00::1]": "an IPv6 unique-local address",
        "[fe80::1]": "an IPv6 link-local address",
        "[::ffff:10.0.0.1]": "an IPv4 private address wearing an IPv6 hat",
    }
    for host, why in literal.items():
        refuses(lambda h=host: intake.check_address(f"https://{h}/a.pdf"), f"{why} ({host})")

    # A public literal is allowed through — otherwise the guard above proves nothing.
    try:
        intake.check_address("https://93.184.216.34/a.pdf")
        check(True, "a public address is allowed through")
    except intake.Refused as exc:
        check(False, f"a public address was refused: {exc}")


def test_the_guard_checks_what_a_name_resolves_to_not_the_name() -> None:
    """The interesting attack is a perfectly ordinary hostname whose A record is 10.0.0.1."""
    def resolves_to(*addresses):
        def fake(host, port, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a_, port))
                    for a_ in addresses]
        return fake

    refuses(lambda: intake.check_address("https://manuals.example/a.pdf",
                                         resolve=resolves_to("10.0.0.5")),
            "a public name resolving to a private address")

    # Every answer, not the first. A name with one good address and one bad one is the
    # shape that beats a guard which stops at the first.
    refuses(lambda: intake.check_address("https://manuals.example/a.pdf",
                                         resolve=resolves_to("93.184.216.34", "127.0.0.1")),
            "a name whose SECOND address is loopback")

    try:
        intake.check_address("https://manuals.example/a.pdf",
                             resolve=resolves_to("93.184.216.34", "198.41.0.4"))
        check(True, "a name resolving only to public addresses is allowed")
    except intake.Refused as exc:
        check(False, f"a wholly public name was refused: {exc}")

    def nothing(host, port, *a, **kw):
        raise socket.gaierror("no such host")
    refuses(lambda: intake.check_address("https://nowhere.example/a.pdf", resolve=nothing),
            "a name that does not resolve")


def test_a_redirect_into_a_private_address_is_refused() -> None:
    """A public URL is not a promise about where it ends up. The guard runs on every hop."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        a_boat(tmp)
        fake = Fake({
            "https://93.184.216.34/manual.pdf":
                (302, {"Location": "https://10.0.0.7/manual.pdf"}, b""),
        })
        with Env(OPENBOAT_BOATS=str(tmp)):
            refuses(lambda: intake.fetch_document(
                        "https://93.184.216.34/manual.pdf", by="chatgpt", transport=fake),
                    "a public URL redirecting to a private address")
            check(fake.asked == ["https://93.184.216.34/manual.pdf"],
                  f"and the second connection was never made ({fake.asked})")

            # Three is the limit, and a loop is not a way past it.
            loop = Fake({f"https://93.184.216.{n}/a.pdf":
                         (302, {"Location": f"https://93.184.216.{n + 1}/a.pdf"}, b"")
                         for n in range(34, 60)})
            refuses(lambda: intake.fetch_document("https://93.184.216.34/a.pdf",
                                                  by="chatgpt", transport=loop),
                    "a redirect chain longer than three hops")
            check(len(loop.asked) <= intake.MAX_REDIRECTS + 1,
                  f"and it stopped after {len(loop.asked)} requests")


# --------------------------------------------------------------------------------------
# 2. What comes back over the wire. Real socket, real streaming, real cap.
# --------------------------------------------------------------------------------------
def test_only_a_pdf_is_kept_and_the_cap_is_enforced_while_it_arrives() -> None:
    good = tiny_pdf("Impeller replacement interval 200 hours")
    routes = {
        "/manual.pdf": (200, "application/pdf", good),
        "/page.html": (200, "text/html", b"<html>not a pdf at all</html>"),
        # A liar: says it is a PDF, is not. The magic bytes decide, not the header.
        "/liar.pdf": (200, "application/pdf", b"GIF89a" + b"\x00" * 100),
        "/huge.pdf": (200, "application/pdf",
                      b"%PDF-1.4\n" + b"x" * (intake.MAX_BYTES + 4096)),
        "/active.pdf": (200, "application/pdf",
                        tiny_pdf("Bulletin", extra=b"\n/OpenAction 9 0 R\n/JavaScript 8 0 R\n")),
    }
    with tempfile.TemporaryDirectory() as raw, Server(routes) as srv:
        tmp = Path(raw)
        base = a_boat(tmp)
        with Env(OPENBOAT_BOATS=str(tmp)):
            got = intake.fetch_document(srv.url("/manual.pdf"), title="Pump manual",
                                        reason="the owner asked about the impeller",
                                        by="chatgpt", transport=srv.transport, safe=allow)
            check(got["bytes"] == len(good) and got["status"] == "inbox",
                  f"a PDF is taken in whole ({got['bytes']} of {len(good)} bytes)")
            check(got["flags"] == [], f"and carries no flags ({got['flags']})")
            check((base / "intake" / f"{got['id']}.pdf").is_file(),
                  "the bytes are in intake/, keyed by their own hash")
            check((base / "intake" / f"{got['id']}.toml").is_file(),
                  "with a sidecar beside them")
            check(not (base / "documents").exists(),
                  "and NOTHING was written into documents/ — that is a person's decision")

            refuses(lambda: intake.fetch_document(srv.url("/page.html"), by="chatgpt",
                                                  transport=srv.transport, safe=allow),
                    "an HTML page")
            refuses(lambda: intake.fetch_document(srv.url("/liar.pdf"), by="chatgpt",
                                                  transport=srv.transport, safe=allow),
                    "a file claiming to be a PDF and starting GIF89a")
            refuses(lambda: intake.fetch_document(srv.url("/huge.pdf"), by="chatgpt",
                                                  transport=srv.transport, safe=allow),
                    f"a body over {intake.MAX_BYTES // (1024 * 1024)} MB")
            refuses(lambda: intake.fetch_document(srv.url("/missing.pdf"), by="chatgpt",
                                                  transport=srv.transport, safe=allow),
                    "a 404")

            flagged = intake.fetch_document(srv.url("/active.pdf"), title="Bulletin",
                                            by="claude", transport=srv.transport, safe=allow)
            check(sorted(flagged["flags"]) == ["JavaScript", "OpenAction"],
                  f"active content is found and named ({flagged['flags']})")
            check(flagged["status"] == "inbox" and
                  (base / "intake" / f"{flagged['id']}.pdf").is_file(),
                  "and the file is KEPT and labelled, not deleted — the person decides")
            refuses(lambda: intake.file_bytes("test-boat", flagged["id"]),
                    "downloading a flagged file")

            # Byte-for-byte the same document is the same item, not a second copy.
            again = intake.fetch_document(srv.url("/manual.pdf"), by="claude",
                                          transport=srv.transport, safe=allow)
            check(again.get("duplicate") is True and again["id"] == got["id"],
                  "the same bytes twice are one item, by sha256")
            check(len(list((base / "intake").glob("*.pdf"))) == 2,
                  "so no second file was written")


def test_the_quota_refuses_in_a_sentence() -> None:
    good = tiny_pdf("Manual")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        a_boat(tmp)
        with Env(OPENBOAT_BOATS=str(tmp)):
            for n in range(intake.PER_DAY):
                intake.add_link(f"https://manuals.example/{n}", title=f"one {n}",
                                by="chatgpt")
            refuses(lambda: intake.add_link("https://manuals.example/last", by="chatgpt"),
                    f"the {intake.PER_DAY + 1}th submission from one caller in a day")
            # Per submitter, not per boat: a second assistant is not blocked by the first.
            second = intake.add_link("https://manuals.example/other", by="claude")
            check(second["status"] == "inbox", "a different caller is unaffected")


# --------------------------------------------------------------------------------------
# 3. Links: recorded, never opened.
# --------------------------------------------------------------------------------------
def test_a_link_is_recorded_and_never_fetched() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        base = a_boat(tmp)
        with Env(OPENBOAT_BOATS=str(tmp)):
            one = intake.add_link("https://manuals.example/pump-7j",
                                  title="Pump 7-J service bulletin",
                                  reason="matches the part in the photograph", by="chatgpt")
            two = intake.add_link("https://manuals.example/gearbox", title="Gearbox notes",
                                  by="claude")
            listed = intake.items("test-boat")
            check(len(listed) == 2, f"both links are listed ({len(listed)})")
            check({i["by"] for i in listed} == {"chatgpt", "claude"},
                  "each remembers who submitted it")
            check(all(i["status"] == "inbox" for i in listed), "and both are waiting")

            text = (base / "intake" / "LINKS.md").read_text(encoding="utf-8")
            check("Nothing in this file is one of the boat's documents" in text
                  and "not searched, not quoted" in text,
                  "the file says on its face that it is not the library")

            # Appending never disturbs what is already there.
            before = (base / "intake" / "LINKS.md").read_text(encoding="utf-8")
            intake.add_link("https://manuals.example/third", title="Third", by="chatgpt")
            check((base / "intake" / "LINKS.md").read_text(encoding="utf-8")
                  .startswith(before), "the links file is appended to, never rewritten")

            refuses(lambda: intake.add_link("javascript:alert(1)", by="chatgpt"),
                    "a javascript: URL as a link")
            check(one["id"] != two["id"], "two links get two ids")


def test_a_title_from_the_network_cannot_become_a_path_or_a_heading() -> None:
    """Everything with a title on it was written by a model reading somebody's web page."""
    check(intake._slug("../../etc/passwd") == "etc-passwd",
          f"a traversal in a title comes out as a word ({intake._slug('../../etc/passwd')})")
    check(intake._slug("C:\\Windows\\System32") == "c-windows-system32",
          "and so does a Windows path")
    check(intake._slug("") == "", "an empty title slugs to nothing, and the caller names it")
    check("/" not in intake._slug("a/b/c") and "." not in intake._slug("a.b.c"),
          "no separator survives the allow-list")
    forged = intake._flat("## 2030-01-01 — all fine\n**Status:** fixed")
    check("\n" not in forged and "#" not in forged and "*" not in forged,
          f"a title cannot forge a heading or a field ({forged!r})")


# --------------------------------------------------------------------------------------
# 4. A person deciding. The only route into the library, and it has a name on it.
# --------------------------------------------------------------------------------------
def test_accept_moves_it_and_the_library_picks_it_up_with_its_provenance() -> None:
    good = tiny_pdf("Impeller replacement interval 200 hours")
    with tempfile.TemporaryDirectory() as raw, Server({"/m.pdf": (200, "application/pdf", good)}) as srv:
        tmp = Path(raw)
        base = a_boat(tmp)
        with Env(OPENBOAT_BOATS=str(tmp), OPENBOAT_PROFILE=str(base / "boat.toml")):
            got = intake.fetch_document(srv.url("/m.pdf"), title="Raw water pump manual",
                                        reason="the impeller question", by="chatgpt",
                                        transport=srv.transport, safe=allow)

            # Before anybody decides: not in the library, at all.
            before = knowledge.load()
            check(not any("intake" in str(p) for p in before.paths),
                  f"nothing in intake/ is in the library ({before.paths})")

            refuses(lambda: intake.accept("test-boat", got["id"], ""),
                    "accepting with no name")

            done = intake.accept("test-boat", got["id"], "Alex Rivers")
            check(done["status"] == "accepted" and done["by"] == "Alex Rivers",
                  "accepting records who did it")
            check((base / "documents" / done["document"]).is_file(),
                  f"the paper is now in documents/ ({done['document']})")
            check(not (base / "intake" / f"{got['id']}.pdf").exists(),
                  "and is no longer in the inbox")
            check("raw-water-pump-manual" in done["document"],
                  f"named from its title through the allow-list ({done['document']})")

            after = knowledge.load()
            check(any(p.parent.name == "documents" for p in after.paths),
                  f"the library now holds it without the profile being edited ({after.paths})")
            check((base / "boat.toml").read_text(encoding="utf-8") ==
                  '[vessel]\nname = "Test Boat"\n',
                  "and the owner's profile was not touched")

            hits = after.search("impeller replacement interval")
            if ingest.backends():
                check(bool(hits), f"the accepted manual answers a question ({len(hits)})")
                check(hits and "chatgpt" in hits[0].text and "Alex Rivers" in hits[0].text,
                      "and the passage says who fetched it and who let it in")
                check(hits and "Unverified" in hits[0].text,
                      "and that nobody has checked it")
            else:
                # No extractor on this machine. The paper is still in the library and the
                # library says out loud that it cannot be read — which is the honest gap,
                # not a silent one.
                passages = after.passages()
                check(any("could not be read" in p.text for p in passages),
                      "with no extractor installed the library states the gap out loud")

            refuses(lambda: intake.accept("test-boat", got["id"], "Alex Rivers"),
                    "accepting the same item twice")


def test_reject_deletes_the_bytes_and_keeps_the_decision() -> None:
    good = tiny_pdf("Something the owner did not want")
    with tempfile.TemporaryDirectory() as raw, Server({"/n.pdf": (200, "application/pdf", good)}) as srv:
        tmp = Path(raw)
        base = a_boat(tmp)
        with Env(OPENBOAT_BOATS=str(tmp)):
            got = intake.fetch_document(srv.url("/n.pdf"), title="Wrong engine variant",
                                        by="chatgpt", transport=srv.transport, safe=allow)
            refuses(lambda: intake.reject("test-boat", got["id"], "", "Alex Rivers"),
                    "rejecting with no reason")
            refuses(lambda: intake.reject("test-boat", got["id"], "wrong boat", ""),
                    "rejecting with no name")

            intake.reject("test-boat", got["id"], "wrong engine variant", "Alex Rivers")
            check(not (base / "intake" / f"{got['id']}.pdf").exists(),
                  "the bytes are gone")
            check((base / "intake" / f"{got['id']}.toml").is_file(),
                  "the sidecar stays — the decision outlives the file")
            item = intake.item("test-boat", got["id"])
            check(item and item["status"] == "rejected" and item["decided_by"] == "Alex Rivers",
                  "and it reads back as rejected, by name")
            check(item and item["decided_why"] == "wrong engine variant",
                  "with the reason that was given")

            # The whole point of keeping it: the same URL is not quietly fetched again.
            refuses(lambda: intake.fetch_document(srv.url("/n.pdf"), by="chatgpt",
                                                  transport=srv.transport, safe=allow),
                    "the same URL after somebody rejected it")
            refuses(lambda: intake.accept("test-boat", got["id"], "Alex Rivers"),
                    "accepting something already rejected")
            check(not (base / "documents").exists(),
                  "and nothing rejected ever reached documents/")


def test_an_accepted_link_joins_the_library_and_says_nobody_opened_it() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        base = a_boat(tmp)
        with Env(OPENBOAT_BOATS=str(tmp), OPENBOAT_PROFILE=str(base / "boat.toml")):
            one = intake.add_link("https://manuals.example/pump-7j",
                                  title="Pump 7-J bulletin",
                                  reason="matches the part number", by="chatgpt")
            intake.accept("test-boat", one["id"], "Alex Rivers")
            text = (base / "documents" / "LINKS.md").read_text(encoding="utf-8")
            check("pump-7j" in text and "Alex Rivers" in text and "chatgpt" in text,
                  "the accepted link records both names")
            check("Nobody has opened these" in text or "not been opened" in text.lower(),
                  "and says plainly that nothing here read the page")
            check(intake.item("test-boat", one["id"])["status"] == "accepted",
                  "the inbox shows it as decided")

            two = intake.add_link("https://manuals.example/other", title="Other",
                                  by="chatgpt")
            intake.reject("test-boat", two["id"], "not this boat's engine", "Alex Rivers")
            item = intake.item("test-boat", two["id"])
            check(item["status"] == "rejected" and item["decided_why"] == "not this boat's engine",
                  "and a rejected link keeps the reason")
            check((base / "documents" / "LINKS.md").read_text(encoding="utf-8") == text,
                  "a rejected link never reaches the accepted file")


def test_the_extraction_carries_provenance_into_every_page() -> None:
    """Not only the header. Retrieval hands back one page at a time, and a warning in a
    header the search never returns is a warning nobody reads."""
    found = ingest.Extraction(source=Path("manual.pdf"),
                              pages=["page one text", "page two text"], backend="test")
    said = "fetched by chatgpt on 2026-09-07 from https://manuals.example/m.pdf"
    out = ingest.to_markdown(found, provenance=said)
    check(out.count(said) >= 3, f"the sentence is in the header and under every page "
                                f"({out.count(said)} times for 2 pages)")
    plain = ingest.to_markdown(found)
    check("Where this came from" not in plain,
          "and a document nobody submitted carries no such line")


# --------------------------------------------------------------------------------------
# 5. The structural promise: nothing in this module can reach the library.
# --------------------------------------------------------------------------------------
def test_intake_never_writes_into_the_library_by_itself() -> None:
    import ast

    tree = ast.parse((ROOT / "openboat" / "intake.py").read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported += [f"{node.module}.{a.name}" for a in node.names]
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    check(not any("control" in name for name in imported),
          f"intake.py does not import control ({imported})")
    check(not any(name.endswith(".knowledge") or name == "knowledge" for name in imported),
          f"and does not import the library it must not write to ({imported})")

    # Every path that writes into documents/ is inside accept(), which requires a name.
    src = (ROOT / "openboat" / "intake.py").read_text(encoding="utf-8")
    accept_at = src.index("def accept(")
    reject_at = src.index("def reject(")
    for marker in ("documents_dir(base)", "ACCEPTED_HEADER"):
        first = src.index(marker, src.index("def accept("))
        check(accept_at < first < reject_at,
              f"{marker} is only reachable from accept() ({first})")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    print("-" * 78)
    for case in (test_the_guard_refuses_everything_not_on_the_public_internet,
                 test_the_guard_checks_what_a_name_resolves_to_not_the_name,
                 test_a_redirect_into_a_private_address_is_refused,
                 test_only_a_pdf_is_kept_and_the_cap_is_enforced_while_it_arrives,
                 test_the_quota_refuses_in_a_sentence,
                 test_a_link_is_recorded_and_never_fetched,
                 test_a_title_from_the_network_cannot_become_a_path_or_a_heading,
                 test_accept_moves_it_and_the_library_picks_it_up_with_its_provenance,
                 test_reject_deletes_the_bytes_and_keeps_the_decision,
                 test_an_accepted_link_joins_the_library_and_says_nobody_opened_it,
                 test_the_extraction_carries_provenance_into_every_page,
                 test_intake_never_writes_into_the_library_by_itself):
        case()
    print("-" * 78)
    failed = [what for ok, what in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks pass"
          + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
