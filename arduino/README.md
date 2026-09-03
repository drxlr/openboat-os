# The engine bridge — and the reason to be careful

An older 5.7 V8 predates every marine data bus. There is no NMEA 2000 to tap, no J1939, no
SmartCraft: there are resistive senders driving analogue gauges. `engine_sender.ino` reads
them in parallel with the gauges and speaks NMEA 0183 over USB, which Signal K understands.

## ⚠️ Read this before buying anything

**A petrol engine compartment is a flammable-vapour space.** Electrical equipment installed
there has to be ignition-protected (ISO 8846 / ABYC E-11). A bare Arduino, its regulator and
any relay are not. This is not a formality: it is the difference between a boat and a bomb,
and it is the reason petrol boats have sealed alternators and starters while diesel ones
do not.

**So: mount the board outside the bay** — under the helm, in the cabin — and run the sender
wires out to it. The senders are already installed and already suitable; the computer is the
new object, so put the new object where the vapour is not.

Second rule: this taps senders **in parallel** with the gauges. It must never become the
reason a gauge reads wrong. After wiring, compare every gauge against its pre-wiring reading
at idle and at temperature. If one has moved, the input impedance is loading the sender —
stop and fix it before going further.

## ⚠️ Untested

**The sketch has never been run against a real engine.** Every calibration constant in it is
read off a published sender curve, not measured on any particular boat:

| Constant | Placeholder | How to get the real one |
|---|---|---|
| `PULSES_PER_REV` | 4 | Run the engine at a known idle, compare against the helm tachometer, adjust until they agree |
| `OIL_R_AT_0_PSI` / `_80_PSI` | 240 Ω / 33 Ω | Bench: swap the sender for a decade resistance box, note the gauge reading at each step |
| `TEMP_R_AT_40C` / `_100C` | 700 Ω / 55 Ω | Same, or a pot of water and a thermometer |
| `FUEL_R_EMPTY` / `_FULL` | 240 Ω / 33 Ω | US-standard is 240–33 Ω; European senders are often 0–180 Ω. **Check which is fitted** |

Do not trust a single gauge reading out of this sketch until every constant above has been
bench-calibrated against your own senders. The NMEA checksum routine *is* verified — it
reproduces the checksums of known-good GPRMC, GPGGA and SDDPT sentences — but a correct
checksum says nothing about whether the number inside it is true.

## ⚠️ Signal K does not parse XDR — verified, not assumed

Listing the parser's hooks inside a running Signal K container gives the complete set of
NMEA 0183 sentences it understands:

```
ALK APB BOD BWC BWR DBK DBS DBT DPT DSC GGA GLL GNS GSV HDG HDM HDT HSC MDA MTA MTW
MWD MWV RMB RMC ROT RPM RSA VDM VDO VDR VHW VLW VPW VTG VWR VWT XTE ZDA
```

**There is no XDR hook at all**, and nothing else in that list carries oil pressure, engine
temperature, battery voltage or tank level. So `$IIRPM` arrives as
`propulsion.engine_1.revolutions` and works; the four `$IIXDR` sentences are read and
silently dropped. Confirmed on the bench: RPM appeared, the other four never did.

The sketch still speaks XDR, because XDR is the portable thing to speak — any plotter or
logger can take it, and changing that would tie the boat to one server. Instead
[`../xdr_bridge.py`](../xdr_bridge.py) sits between the Arduino and Signal K and translates:

```bash
python3 xdr_bridge.py --serial /dev/ttyUSB0 --port 10120     # aboard
python3 xdr_bridge.py --from-tcp localhost:10110 --port 10120  # on the bench
```

Then add a second connection in Signal K of type **SignalK**, TCP, to that port. It reads
the sentence, checks the checksum, and emits Signal K's own delta format on the standard
SI paths — pascal, kelvin, volts, a 0–1 ratio. No dependencies; `stty` and a plain read, so
it runs on a Pi with nothing installed.

Verified on the bench: oil pressure, coolant, battery and tank all appear in `vessels/self`
and land on a helm panel's gauges. `xdr_bridge.py` also goes quiet on a stale sender rather
than repeating its last reading with a fresh timestamp — see the `MAX_AGE_S` comment in that
file for why that distinction matters for an engine-hours counter.

## What it emits

```
$IIRPM,E,1,2400,,A*50                 engine rpm
$IIXDR,P,241316,P,OILPRESS*40         oil pressure, pascals
$IIXDR,C,82.4,C,ENGTEMP*1E            coolant, °C
$IIXDR,U,13.85,V,BATT1*5E             battery volts
$IIXDR,V,0.620,P,FUEL*78              tank fraction
$IIXDR,S,1,,BILGE*..                  bilge float
```

`RPM` and the standard `XDR` types map straight into Signal K. The trailing names are ours
and may need a mapping rule in the server's NMEA 0183 settings — check the data browser
before assuming a path exists.

## Why bother

On an older engine with no logged history, engine hours are usually a guess — a rebuilt
long-block might have run 50 hours or 500, and nobody aboard can say which. An hour counter
is `rpm > 0` integrated over time, and from the day this runs, that number stops being a
guess and starts being a record a surveyor, an insurer or a buyer will accept.

The second reason is that cooling and exhaust failures show up in temperature and pressure
long before they show up as a noise. Logged data turns *it feels hotter than last year* into
a chart.
