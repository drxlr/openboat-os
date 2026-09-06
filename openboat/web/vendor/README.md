# Vendored assets

Everything in this directory is checked into the repo rather than loaded from a CDN at
runtime. The dashboard's whole promise is that it works with no network — a tablet at the
end of a pontoon on bad or absent marina wifi — and a `<script src="https://...">` breaks
that promise the moment the connection drops: the page degrades or goes blank instead of
just working. Vendoring turns every one of these into a local file the server already
ships, so the dashboard loads the same way whether the boat has signal or not.

The one exception is `windy.html`'s `libBoot.js`, which is Windy's own hosted API, keyed to
their service — it cannot be mirrored here, and the page says so inline. That is now the
*only* runtime network fetch left in the dashboard, and only the Windy weather layer needs
it; the rest of the app, including the base chart, works offline.

## Files

| File | Version | Source | Licence | SHA-256 |
|---|---|---|---|---|
| `alpine.min.js` | 3.17.1 | https://unpkg.com/alpinejs@3.17.1/dist/cdn.min.js | MIT | `b30997fc126d808b1a9b20ab3f504ded88df957818c02d6249bba3ec114eb0ec` |
| `sortable.min.js` | 1.15.7 | https://unpkg.com/sortablejs@1.15.7/Sortable.min.js | MIT | `bf4241bc73fef7f11c59a283a69fe8051cdd31c6d8ff5a2b9ba219e7831fcf76` |
| `leaflet.js` | 1.9.4 | https://unpkg.com/leaflet@1.9.4/dist/leaflet.js | BSD-2-Clause | `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a` |
| `leaflet.css` | 1.9.4 | https://unpkg.com/leaflet@1.9.4/dist/leaflet.css | BSD-2-Clause | `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6` |
| `leaflet-1.4.0.js` | 1.4.0 | https://unpkg.com/leaflet@1.4.0/dist/leaflet.js | BSD-2-Clause | `e8165148436ade4c48e186010ea276df1834af51b04c7129be9de891d688a81c` |
| `images/layers.png` | 1.9.4 | https://unpkg.com/leaflet@1.9.4/dist/images/layers.png | BSD-2-Clause | `1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6` |
| `images/layers-2x.png` | 1.9.4 | https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png | BSD-2-Clause | `066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf` |
| `images/marker-icon.png` | 1.9.4 | https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png | BSD-2-Clause | `574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437` |

Two Leaflets, on purpose: `index.html` runs 1.9.4. `windy.html` is pinned to 1.4.0 because
Windy's map API requires that exact version and tells integrators not to load Leaflet's own
CSS alongside it — see the comment at the top of `windy.html`. Vendoring both keeps that
documented constraint intact instead of silently upgrading a version Windy's library was
never tested against. `leaflet.css`'s `images/` references (`layers.png`, `layers-2x.png`,
`marker-icon.png`) are the only image assets it needs — the dashboard draws its own markers
with `L.circleMarker` rather than Leaflet's default pin, so no other marker image ships.

`jobs.html` loads both, with `defer`, in that order (Alpine before Sortable): Sortable
handles drag-to-reorder on the page's own lane sections via `handle: '.grip'`, persisting
the order to `localStorage` — it never touches boat state. Alpine drives everything else on
the page. This is the core Alpine build (`cdn.min.js`), which has no Collapse plugin, so
`jobs.html` opens and closes panels with plain `x-show` rather than `x-collapse`.

## Updating a file

```bash
curl -sL -o alpine.min.js       https://unpkg.com/alpinejs@<version>/dist/cdn.min.js
curl -sL -o sortable.min.js     https://unpkg.com/sortablejs@<version>/Sortable.min.js
curl -sL -o leaflet.js          https://unpkg.com/leaflet@<version>/dist/leaflet.js
curl -sL -o leaflet.css         https://unpkg.com/leaflet@<version>/dist/leaflet.css
curl -sL -o leaflet-1.4.0.js    https://unpkg.com/leaflet@1.4.0/dist/leaflet.js
curl -sL -o images/layers.png       https://unpkg.com/leaflet@<version>/dist/images/layers.png
curl -sL -o images/layers-2x.png    https://unpkg.com/leaflet@<version>/dist/images/layers-2x.png
curl -sL -o images/marker-icon.png  https://unpkg.com/leaflet@<version>/dist/images/marker-icon.png
```

After updating, recompute the SHA-256 (`shasum -a 256 <file>`) and update the table above.
