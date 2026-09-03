# The berth is not the sea

This document exists because getting it wrong produced a real bug, and because the mistake
is easy, invisible and confident.

## What happened

Open-Meteo's coastal grid is roughly 0.125° — about 7.5 nautical miles. Every point within a
few miles of a harbour can therefore snap to the **same** grid cell, and if that cell contains
land, its wind is land wind.

Measured over a week of daylight hours at one Mediterranean harbour, comparing the cell
containing the marina against two cells of open water a few miles out:

| cell | median daylight wind | p90 | gust factor |
|---|---|---|---|
| the one containing the marina | 6.4 kn | 9.5 | **2.28** |
| open water, six miles south | 7.8 kn | 13.1 | 1.52 |
| open water, offshore | 7.6 kn | 12.3 | 1.56 |

A gust factor around 1.4–1.5 is normal over open water. **2.28 is the signature of surface
roughness ashore**, not of weather.

So the harbour cell is wrong in *both* directions at once: it under-reads the sustained wind
the boat will actually meet, and over-reads the gusts. And because a passage window is tested
against wind **and** gusts, the two errors do not cancel. Real windows get chopped up by
gusts that only exist over the car park. Over the same week, the harbour cell yielded 8
windows totalling 70 usable hours; the offshore cell yielded 7 totalling 78.

## What follows

A profile carries two positions, and they are different fields on purpose:

```toml
[berth]            # where the boat physically sits
lat = 50.3640
lon = -4.1310

[forecast_point]   # the water it runs in, a few miles out
lat = 50.3100
lon = -4.1500
```

`berth` answers *what is it doing at the boat*. `forecast_point` answers *can we go out*.
Both are real questions and they have different answers.

## There is deliberately no name meaning both

This is the part worth copying into your own code. The first attempt at this fix kept a
single name as an alias for the new default — and another module imported that name as a
*place*, which silently moved the harbour thirteen miles out to sea and made a beach beside
it the furthest point of a day's run.

The alias was the bug. A name has to mean one thing. If you find yourself wanting a constant
that means "the boat's location, roughly", you have found the same trap.

## Choosing your forecast point

Somewhere you actually go, in open water, far enough out that the cell contains no land.
Three to six miles is usually enough. Check it by comparing gust factors: if your point reads
a gust factor much above 1.6 in ordinary conditions while a point further out does not, your
cell still has a coastline in it.
