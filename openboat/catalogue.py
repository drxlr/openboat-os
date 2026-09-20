"""Every part somebody has found for this boat, across every shop.

`parts.py` answers "what does *this job* need". This answers the question that comes
before it: **what exists for this boat at all, and who sells it.** One is a kit; this is
the index the kit is built out of.

An index is per boat and multi-shop by construction. One file per shop under
`catalogue/` beside your `boat.toml`:

    boats/your-boat/catalogue/first-shop.json
    boats/your-boat/catalogue/second-shop.json
    …

Each file carries its shop's own terms — currency, whether prices include VAT, where it
ships from, and **the date somebody looked**. Those live on the shop rather than on each
row because they are facts about the merchant, and repeating them per row is how one row
ends up disagreeing with the next.

    python3 -m openboat.catalogue                    # what is indexed, per shop
    python3 -m openboat.catalogue --find "impeller"  # search names, numbers, specs
    python3 -m openboat.catalogue --number TTA5603   # one part, in full

## The rules, which are the same three the rest of this repo lives by

1. **A stale index is labelled, never silently trusted.** Each shop says when it was
   scraped. Past `STALE_DAYS` every row from it is marked stale, because a chandler's
   price from last spring is a number nobody has stood behind since.
2. **Fitment is quoted, not inferred.** A row lists the models the shop itself says it
   fits. This module never decides that a part "probably" fits a boat because the names
   look similar.
3. **Nothing here buys anything.** It is a read-only index with links out. Ordering
   happens on the shop's own site, under the shop's own terms, by a person who read them.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .profile import Profile, load as load_profile

#: How long an indexed price stays worth repeating out loud. Same figure as `parts.py`,
#: for the same reason — and deliberately the same constant so the two screens agree.
STALE_DAYS = 60


def _age_days(checked: str) -> int | None:
    if not checked:
        return None
    try:
        return (date.today() - date.fromisoformat(checked)).days
    except ValueError:
        return None


@dataclass(frozen=True)
class Shop:
    """One merchant, and the terms every row from it is quoted under."""

    id: str
    name: str = ""
    url: str = ""
    currency: str = "EUR"
    vat: str = ""            # "incl" | "excl" | "" when the shop did not say
    ships_from: str = ""
    checked: str = ""
    note: str = ""
    source: str = ""         # the listing this was taken from, so it can be re-run

    @property
    def age_days(self) -> int | None:
        return _age_days(self.checked)

    @property
    def stale(self) -> bool:
        age = self.age_days
        return age is None or age > STALE_DAYS

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name or self.id, "url": self.url,
                "currency": self.currency, "vat": self.vat,
                "ships_from": self.ships_from, "checked": self.checked,
                "note": self.note, "source": self.source,
                "age_days": self.age_days, "stale": self.stale}


@dataclass(frozen=True)
class Item:
    """One part in one shop's catalogue."""

    shop: str
    number: str
    name: str
    category: str = ""
    price_eur: float | None = None
    url: str = ""
    fits: tuple[str, ...] = ()
    description: str = ""
    brand: str = ""

    def haystack(self) -> str:
        return " ".join((self.number, self.name, self.category, self.brand,
                         self.description)).lower()

    def as_dict(self, full: bool = True) -> dict:
        out = {"shop": self.shop, "number": self.number, "name": self.name,
               "category": self.category, "price_eur": self.price_eur,
               "url": self.url, "fits": list(self.fits), "brand": self.brand}
        if full:
            out["description"] = self.description
        else:
            # The list view searches on it, so it cannot be dropped — only shortened.
            out["description"] = self.description[:400]
        return out


@dataclass
class Index:
    """Every shop indexed for one boat, and everything they list."""

    shops: tuple[Shop, ...] = ()
    items: tuple[Item, ...] = ()
    path: Path | None = None

    def categories(self) -> list[str]:
        return sorted({i.category for i in self.items if i.category})

    def find(self, query: str = "", shop: str = "", category: str = "",
             fits: str = "", limit: int = 50) -> list[Item]:
        """Search names, numbers, categories and specifications.

        Every whitespace-separated word must appear somewhere in the row. That is stricter
        than an any-word match and it is the right default for a parts search: "furling
        rope" should not return every rope and every furler.
        """
        words = [w for w in re.split(r"\s+", query.strip().lower()) if w]
        out = []
        for item in self.items:
            if shop and item.shop != shop:
                continue
            if category and not item.category.startswith(category):
                continue
            if fits and not any(fits.lower() in f.lower() for f in item.fits):
                continue
            if words:
                hay = item.haystack()
                if not all(w in hay for w in words):
                    continue
            out.append(item)
            if limit and len(out) >= limit:
                break
        return out

    def by_number(self, number: str) -> Item | None:
        want = number.strip().lower()
        return next((i for i in self.items if i.number.lower() == want), None)

    def as_dict(self, full: bool = False) -> dict:
        return {"source": str(self.path) if self.path else "",
                "stale_days": STALE_DAYS,
                "shops": [s.as_dict() for s in self.shops],
                "categories": self.categories(),
                "count": len(self.items),
                "items": [i.as_dict(full) for i in self.items]}


def load(profile: Profile | None = None, path: str | Path | None = None) -> Index:
    """Read every `catalogue/*.json` beside this boat's profile.

    A boat with no catalogue directory is not an error — it is a boat whose owner has not
    indexed a shop yet, which is the normal state on day one.
    """
    boat = profile or load_profile()
    folder = Path(path) if path else None
    if folder is None and boat.path:
        folder = Path(boat.path).resolve().parent / "catalogue"
    if folder is None or not folder.is_dir():
        return Index()

    shops: list[Shop] = []
    items: list[Item] = []
    for file in sorted(folder.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # One unreadable shop file must not take the whole parts screen down.
            continue
        meta = data.get("shop") or {}
        shop = Shop(
            id=str(meta.get("id") or file.stem),
            name=str(meta.get("name", "")),
            url=str(meta.get("url", "")),
            currency=str(meta.get("currency", "EUR")),
            vat=str(meta.get("vat", "")).lower(),
            ships_from=str(meta.get("ships_from", "")),
            checked=str(meta.get("checked", "")),
            note=str(meta.get("note", "")),
            source=str(meta.get("source", "")),
        )
        shops.append(shop)
        for row in data.get("items") or []:
            number = str(row.get("number") or "").strip()
            name = str(row.get("name") or "").strip()
            if not number and not name:
                continue
            price = row.get("price_eur")
            items.append(Item(
                shop=shop.id,
                number=number,
                name=name,
                category=str(row.get("category") or ""),
                price_eur=float(price) if price is not None else None,
                url=str(row.get("url") or ""),
                fits=tuple(row.get("fits") or ()),
                description=str(row.get("description") or ""),
                brand=str(row.get("brand") or ""),
            ))
    return Index(shops=tuple(shops), items=tuple(items), path=folder)


def _money(v: float | None) -> str:
    return f"€{v:,.2f}".replace(",", " ") if v is not None else "—"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Every part indexed for this boat.")
    ap.add_argument("--find", default="", help="search names, numbers and specifications")
    ap.add_argument("--number", default="", help="show one part in full")
    ap.add_argument("--shop", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--profile")
    args = ap.parse_args(argv)

    boat = load_profile(args.profile) if args.profile else load_profile()
    idx = load(boat)

    if not idx.items:
        print("No shop indexed yet. Put one JSON file per shop in a catalogue/ directory "
              "beside this boat's boat.toml.")
        return

    if args.number:
        item = idx.by_number(args.number)
        if not item:
            print(f"No part numbered {args.number} in the index.")
            return
        print(f"{item.number}  {item.name}")
        print(f"  shop     {item.shop}")
        print(f"  category {item.category or '—'}")
        print(f"  price    {_money(item.price_eur)}")
        print(f"  url      {item.url}")
        print(f"  fits     {', '.join(item.fits) if item.fits else '—'}")
        if item.description:
            print(f"\n{item.description}")
        return

    for shop in idx.shops:
        n = sum(1 for i in idx.items if i.shop == shop.id)
        flag = "  ⚠️ STALE" if shop.stale else ""
        print(f"{shop.id:<18} {n:>5} parts · {shop.vat or 'VAT unknown'} · "
              f"checked {shop.checked or '—'}{flag}")
    print()

    hits = idx.find(args.find, shop=args.shop, category=args.category, limit=args.limit)
    print(f"{len(hits)} shown of {len(idx.items)} indexed"
          + (f" for '{args.find}'" if args.find else ""))
    for item in hits:
        print(f"  {item.number:<12} {_money(item.price_eur):>11}  {item.name[:56]}")
        if item.category:
            print(f"  {'':<12} {'':>11}  {item.category}")


if __name__ == "__main__":
    main()
