# Charts, tiles and weather layers

The chart page draws two free layers and, if you give it a key, one paid one. Each comes
with terms, and one of them says something you need to hear before you trust it.

## What the chart is made of

| layer | source | licence | needs |
|---|---|---|---|
| base map | `tile.openstreetmap.org` | ODbL data, tiles © OpenStreetMap contributors | internet |
| seamarks | `tiles.openseamap.org/seamark` | ODbL data, tiles CC BY-SA 2.0 | internet |
| weather | Windy Map Forecast API | commercial, keyed | internet **and** a key |

Attribution is not decoration. OpenStreetMap's tile policy requires visible licence
attribution and specifies the wording; OpenSeaMap's tiles are CC BY-SA, which requires
attribution as a condition of the licence. Both strings live in the profile so you can
translate or re-word them, and both have a correct default. Do not delete them.

## There is no "download for offline" button, and there will not be one

This is the question everybody asks first, because the chart is the one page that dies at
sea, exactly when you want it. The answer is that OpenStreetMap's
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/) forbids it, in
those words:

> **Prohibited: bulk downloading ("scraping") and offline use.** Bulk downloading is any
> pre-emptive fetching of tiles other than those a user is actively viewing. […] Offline
> use is not permitted on tile.openstreetmap.org. Features such as "Download city/country
> for offline use" or "Save area for later" rely on prefetch/bulk downloading and are
> therefore prohibited.

Those tiles are rendered on donated hardware for the people who edit the map. A prefetch
button shipped in an open-source project is not one boat's traffic — it is every install of
that project, and it is how a whole project gets blocked. `tests/test_chart.py` fails the
build if a prefetch feature appears.

The policy also asks that the tile URL not be hard-coded, "allow switching without needing
a software update". That is why the URLs are profile settings, and it is also the way out.

## How to get a chart that works with no internet

Point the base layer at tiles you are allowed to keep. Three routes, cheapest first:

1. **Signal K's chart plugin.** `@signalk/charts-plugin` serves MBTiles from the Pi and is
   already in this project's Signal K plugin list. Drop an `.mbtiles` file on the boat and
   set `base_url` to the plugin's tile endpoint. Nothing else changes.
2. **Your own tile server.** Anything that speaks `{z}/{x}/{y}` works — the URL is a
   string in your profile. See [switch2osm.org](https://switch2osm.org/).
3. **A provider whose terms permit offline packaging.** Vector-tile providers commonly do;
   the OSM Foundation's own policy points at these as the correct alternative.

The seamark overlay is a separate question: OpenSeaMap publish a downloadable chart from
[openseamap.org](https://www.openseamap.org/), and the seamark tiles are CC BY-SA, so
keeping a copy is a licence question rather than a policy prohibition. Read their terms
before bulk-fetching their server too; the courtesy is the same.

**None of this makes OpenBoat a chartplotter.** It has no depth data, no rocks and no
knowledge of restricted areas. See [DISCLAIMER.md](../DISCLAIMER.md).

## Windy: read this before you rely on it

Windy's map is genuinely good and the free tier is genuinely limited. Both halves matter.

| | Testing (free) | Professional |
|---|---|---|
| price | free | €990/year, +€1,000 for ECMWF |
| sessions/day | 500 | 10,000 |
| models | GFS only | GFS, ICON, NAM, AROME, GEOS5, CAMS, HRRR, ECMWF |
| layers | wind, temperature, pressure | 40+, including waves and currents |
| permitted use | "Development purpose only, not intended for production" | production |

Two things follow, and neither is a matter of taste:

**The free map tier is not licensed for real use.** Windy's own documentation: *"This
version is for development only and is not allowed to be used in production."* The weather
chart page says so on screen for as long as a free key is in use.

**The free Point Forecast tier does not return real numbers.** Asked for a forecast on
2026-09-04, Windy's point API returned its data alongside this field:

> `"warning": "The testing API version is for development purposes only. This data is
> randomly shuffled and slightly modified."`

That is why there is no Windy column on the Weather page and no `windy_point_key` setting.
A shuffled forecast placed beside a real one does not read as a broken feature — it reads
as two models disagreeing, which is exactly the signal a go/no-go decision leans on. The
project already refuses to invent an engine alarm band. This is the same rule.

Open-Meteo, which the Weather page already uses, is free, needs no key, returns ECMWF, ICON
and GFS, and adds wave height. For the numbers a decision rests on it is not a compromise —
it is the better source. Windy is the picture; Open-Meteo is the answer.

## Turning the weather chart on

The key reaches the browser — that is how Windy's map API works, and no configuration
changes it. What matters is that it never reaches a repository:

```bash
export OPENBOAT_WINDY_MAP_KEY=…      # read first, from the environment
```

or, in a profile you are certain is gitignored:

```toml
[chart]
windy_map_key = "…"
base_url = "http://signalk.local:3000/charts/osm/{z}/{x}/{y}.png"   # your own tiles
```

With no key the tab is not registered at all, and the rest of the dashboard is unaffected.

When you create the key, leave **Domain restrictions blank** — the dashboard is served from
a private IP, and Windy's restriction only matches second-level domain names. Put your
repository or site URL in **Project identification**: Windy require an identifier they can
check, and a bare slug is not one.
