"""What a job actually needs, and where it can be got.

A service item in the profile says *what is owed* — an impeller every so many hours, anodes
every season. It does not say what to buy, and that is the half that costs a season. The
part number is usually findable; the *fitment* is not, and neither is the answer to the only
question that decides a purchase: who has it on a shelf, at what delivered price, in how
many days, to here.

This module holds that second half: a **kit** — the line items one service item consumes —
and, per line, the **offers** somebody actually checked. It is data, not code. Kits live in
`kits.toml` beside your `boat.toml`; the repository ships only a demonstration file whose
prices are invented and marked as invented, because a placeholder price that is not labelled
is indistinguishable from a quote.

    python3 -m openboat.parts                  # every kit, and what it consumes
    python3 -m openboat.parts --item impeller  # one kit, with its offers

## The rule this module exists to enforce

**An unverified offer is never quoted.** Prices and stock drift without notice, so every
offer carries the date somebody last looked at it. Past `STALE_DAYS` it is still shown —
hiding it would lose the supplier — but it is marked stale, and no total is computed from
it. A confident sum built out of eight-month-old prices is exactly the silent wrong answer
this project is not allowed to produce.

Nothing here writes, orders, or holds a basket. A basket is a thing a person carries in
their own browser; see `web/console/cart.js`. Buying happens on the supplier's own site,
under the supplier's own terms, by a human who read them.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .profile import Profile, load as load_profile

#: Shipped with the repository so a fresh clone shows the shape of the thing. Its prices are
#: invented. `placeholder = true` in that file is what makes every screen say so.
DEMO_KITS = Path(__file__).resolve().parent.parent / "profiles" / "demo-kits.toml"

#: After this many days an offer stops counting toward a total. Not a hiding rule — a
#: quoting rule. Chandlery prices move with the season and with the euro; two months is
#: about how long a checked price stays worth repeating out loud.
STALE_DAYS = 60


@dataclass(frozen=True)
class Offer:
    """One supplier's answer for one line, on the day somebody asked."""

    supplier: str
    price_eur: float | None = None
    #: "incl", "excl", or "" when nobody wrote it down. A chandler quotes inc-VAT to a
    #: consumer and ex-VAT to a trade account, and the two differ by a fifth. Summing
    #: across the two bases produces a number that is wrong in a direction nobody notices,
    #: so a kit whose offers disagree gets no total at all — see `Kit.vat_basis`.
    vat: str = ""
    lead_days: int | None = None
    stock: str = ""
    checked: str = ""
    url: str = ""
    ships_to: str = ""
    note: str = ""

    @property
    def age_days(self) -> int | None:
        """Days since anybody looked, or None if the offer never said when."""
        if not self.checked:
            return None
        try:
            return (date.today() - date.fromisoformat(self.checked)).days
        except ValueError:
            return None

    @property
    def stale(self) -> bool:
        """True when this offer must not be added into a total.

        An offer with no `checked` date is stale by definition: nobody can say when it was
        true, so it can never be quoted. That is stricter than treating a missing date as
        "probably fine", and it is the strictness that keeps the totals honest.
        """
        age = self.age_days
        return age is None or age > STALE_DAYS

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "price_eur": self.price_eur, "vat": self.vat,
                "lead_days": self.lead_days, "stock": self.stock, "checked": self.checked,
                "url": self.url, "ships_to": self.ships_to, "note": self.note,
                "age_days": self.age_days, "stale": self.stale}


@dataclass(frozen=True)
class Line:
    """One thing that has to be in the box before the job can start."""

    part: str
    number: str = ""
    qty: int = 1
    why: str = ""
    optional: bool = False
    offers: tuple[Offer, ...] = ()

    @property
    def best(self) -> Offer | None:
        """The cheapest offer that is still quotable, or None if none is.

        Cheapest, not fastest: lead time is on the card and it is frequently the reason to
        pay more, but this module will not decide that trade for anybody. It picks the
        number a total can be built from and leaves the judgement on screen.
        """
        live = [o for o in self.offers if not o.stale and o.price_eur is not None]
        return min(live, key=lambda o: o.price_eur) if live else None

    def as_dict(self) -> dict:
        best = self.best
        return {"part": self.part, "number": self.number, "qty": self.qty, "why": self.why,
                "optional": self.optional,
                "offers": [o.as_dict() for o in self.offers],
                "best": best.as_dict() if best else None,
                "line_eur": round(best.price_eur * self.qty, 2) if best else None}


@dataclass(frozen=True)
class Kit:
    """The parts one service item consumes, with the traps written down."""

    #: The maintenance item this kit serves, if it serves one.
    item: str = ""
    #: The snag it serves instead, identified by the timestamp SNAGS.md filed it under.
    #: A kit belongs to exactly one of the two: work arrives on a boat either because the
    #: engine is owed something or because somebody stood in front of a fault, and those
    #: are the only two doors.
    snag: str = ""
    title: str = ""
    note: str = ""
    lines: tuple[Line, ...] = ()

    @property
    def key(self) -> str:
        return self.item or f"snag:{self.snag}"

    @property
    def vat_basis(self) -> str:
        """"incl", "excl", "mixed", or "" — whichever the counted offers agree on.

        Only the offers a total would actually be built from are considered: a stale offer
        on a different basis cannot poison a figure it is not part of.
        """
        seen = {l.best.vat for l in self.lines if l.best}
        seen.discard("")
        if not seen:
            return ""
        return seen.pop() if len(seen) == 1 else "mixed"

    @property
    def quotable(self) -> bool:
        """True only when every non-optional line has a live price.

        Deliberately all-or-nothing. A kit total missing one line is not a smaller total,
        it is a wrong one — and the line that goes missing is reliably the gasket nobody
        remembered, which is the failure that strands a job after the freight is paid.
        """
        if self.vat_basis == "mixed":
            return False
        return all(line.best for line in self.lines if not line.optional)

    def _sum(self, optional: bool) -> float | None:
        chosen = [l for l in self.lines if l.optional is optional and l.best]
        if not chosen:
            return None
        return round(sum(l.best.price_eur * l.qty for l in chosen), 2)

    @property
    def total_eur(self) -> float | None:
        """What the job costs. Required lines only.

        The optional lines are tools and spares — real money, and deliberately not inside
        this number. A total that quietly includes a €58 extraction pump is not the cost of
        an oil change, and the person deciding whether to do the job this weekend is asking
        about the oil change.
        """
        return self._sum(optional=False) if self.quotable else None

    @property
    def optional_eur(self) -> float | None:
        """The prudent extras, priced separately so the trade is visible."""
        return self._sum(optional=True)

    def as_dict(self) -> dict:
        missing = [line.part for line in self.lines if not line.optional and not line.best]
        if self.vat_basis == "mixed" and not missing:
            missing = ["— offers mix ex-VAT and inc-VAT prices"]
        return {"item": self.item, "snag": self.snag, "key": self.key,
                "title": self.title, "note": self.note,
                "lines": [line.as_dict() for line in self.lines],
                "quotable": self.quotable, "total_eur": self.total_eur,
                "optional_eur": self.optional_eur, "vat_basis": self.vat_basis,
                "missing_prices": missing}


@dataclass
class Catalogue:
    """Every kit this boat has written down, and where they were read from."""

    kits: tuple[Kit, ...] = ()
    path: Path | None = None
    placeholder: bool = False

    def for_item(self, item: str) -> Kit | None:
        return next((k for k in self.kits if k.item and k.item == item), None)

    def for_snag(self, when: str) -> Kit | None:
        return next((k for k in self.kits if k.snag and k.snag == when), None)

    def as_dict(self) -> dict:
        return {"source": str(self.path) if self.path else "",
                # The one flag every screen must honour. True means the prices below were
                # typed to demonstrate the shape and were never quoted by anybody.
                "placeholder": self.placeholder,
                "stale_days": STALE_DAYS,
                "kits": [k.as_dict() for k in self.kits]}


def _offer(data: dict) -> Offer:
    return Offer(
        supplier=str(data.get("supplier", "")).strip(),
        price_eur=float(data["price_eur"]) if data.get("price_eur") is not None else None,
        vat=str(data.get("vat", "")).strip().lower(),
        lead_days=int(data["lead_days"]) if data.get("lead_days") is not None else None,
        stock=str(data.get("stock", "")).strip(),
        checked=str(data.get("checked", "")).strip(),
        url=str(data.get("url", "")).strip(),
        ships_to=str(data.get("ships_to", "")).strip(),
        note=str(data.get("note", "")).strip(),
    )


def _line(data: dict) -> Line:
    return Line(
        part=str(data.get("part", "")).strip(),
        number=str(data.get("number", "")).strip(),
        qty=int(data.get("qty", 1)),
        why=str(data.get("why", "")).strip(),
        optional=bool(data.get("optional", False)),
        offers=tuple(_offer(o) for o in (data.get("offer") or [])),
    )


def load(profile: Profile | None = None, path: str | Path | None = None) -> Catalogue:
    """Read the kits file for this boat.

    Order: the argument, then `kits.toml` beside the profile, then the demonstration file.
    A boat with no kits file is not an error and not an empty screen — it is a boat whose
    owner has not written any down yet, which is the normal state on day one.
    """
    boat = profile or load_profile()
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    if boat.path:
        candidates.append(Path(boat.path).resolve().parent / "kits.toml")
    candidates.append(DEMO_KITS)

    chosen = next((c for c in candidates if c.is_file()), None)
    if chosen is None:
        return Catalogue()

    with chosen.open("rb") as fh:
        data = tomllib.load(fh)

    kits: list[Kit] = []
    for entry in data.get("kit") or []:
        item = str(entry.get("item", "")).strip()
        snag = str(entry.get("snag", "")).strip()
        # A kit that names neither is attached to nothing and would never be found. Skipped
        # rather than raised: one malformed block in a file somebody edits by hand at a
        # pontoon must not take the whole parts screen down with it.
        if not item and not snag:
            continue
        kits.append(Kit(
            item=item,
            snag=snag,
            title=str(entry.get("title", "")).strip(),
            note=str(entry.get("note", "")).strip(),
            lines=tuple(_line(l) for l in (entry.get("line") or [])),
        ))
    return Catalogue(kits=tuple(kits), path=chosen,
                     placeholder=bool(data.get("placeholder", False)))


def _money(value: float | None) -> str:
    return f"€{value:,.2f}".replace(",", " ") if value is not None else "—"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="What a job needs, and where it can be got.")
    ap.add_argument("--item", help="show one kit, with every offer")
    ap.add_argument("--profile", help="path to boat.toml")
    ap.add_argument("--kits", help="path to kits.toml")
    args = ap.parse_args(argv)

    boat = load_profile(args.profile) if args.profile else load_profile()
    cat = load(boat, args.kits)

    if not cat.kits:
        print("No kits written down yet. Create kits.toml beside your boat.toml —"
              f"\n{DEMO_KITS} shows the shape.")
        return

    print(f"kits: {cat.path}")
    if cat.placeholder:
        print("⚠️  PLACEHOLDER DATA — these prices were invented to show the shape. "
              "Nobody quoted them.")
    print()

    for kit in cat.kits:
        if args.item and kit.key != args.item and kit.item != args.item:
            continue
        vat = f" ({kit.vat_basis} VAT)" if kit.vat_basis else ""
        total = (_money(kit.total_eur) + vat) if kit.quotable else "not quotable"
        extra = (f" · {_money(kit.optional_eur)} of optional extras"
                 if kit.optional_eur is not None else "")
        print(f"{kit.key:<14} {kit.title}")
        if kit.note:
            print(f"{'':<14} {kit.note}")
        print(f"{'':<14} {len(kit.lines)} lines · {total}{extra}")
        for line in kit.lines:
            mark = "·" if not line.optional else "○"
            num = f" [{line.number}]" if line.number else ""
            best = line.best
            price = f"{_money(best.price_eur)} {best.supplier}" if best else "no live price"
            print(f"   {mark} {line.qty}× {line.part}{num} — {price}")
            if line.why:
                print(f"     {line.why}")
            if args.item:
                for o in line.offers:
                    flag = " STALE" if o.stale else ""
                    lead = f"{o.lead_days} d" if o.lead_days is not None else "lead unknown"
                    vat = f" {o.vat}. VAT" if o.vat else " VAT basis unknown"
                    print(f"       {o.supplier:<26} {_money(o.price_eur):>10}{vat:<20}"
                          f" {lead:<14} {o.stock or 'stock unknown':<16}"
                          f" checked {o.checked or '—'}{flag}")
        if not kit.quotable:
            print(f"{'':<14} no total: no live price for "
                  f"{', '.join(kit.as_dict()['missing_prices'])}")
        print()


if __name__ == "__main__":
    main()
