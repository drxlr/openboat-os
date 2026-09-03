# The hardware, and how little of it you need

OpenBoat is a Python package. It runs anywhere Python 3.11 runs, and the interesting claim in
this document is how far down that goes: **you can run the whole thing today, on a laptop,
with no boat, no Pi and nothing bought.**

Hardware is what you add when you want the boat's own numbers in it.

## Four rungs, and each one is useful on its own

| Rung | What you need | What you get |
|---|---|---|
| **0. Nothing** | any computer | Forecasts, passage windows, route legs with per-leg weather, the climatology, the dashboard, the MCP server. No boat required |
| **1. A simulator** | the same computer, plus Docker | The whole boat stack on your kitchen table: a Signal K server, a boat moving in a circle, engine gauges reading. This is where you learn the software |
| **2. A boat computer + a position source** | a small always-on computer aboard, and a GPS | Tracks, an anchor watch, and a boat you can check on from home |
| **3. Sensors** | a gateway or a microcontroller | Engine hours, temperatures, pressures, tank levels — the boat's own instruments, logged |

Most of the value is on rungs 0 and 2. Rung 3 is where the soldering is, and it is optional
for as long as you like.

## The boat computer can be almost anything

A Raspberry Pi is the usual answer and a good one, but it is **one option, not a
requirement**. What the job actually demands:

- it is on when the boat is on, and ideally when the boat is alone
- it runs Python 3.11 and, if you want Signal K, Docker or Node
- it survives being unattended, warm and damp

Anything meeting that works: a Pi 4 or 5, a mini PC, an old laptop with the lid shut, a NAS,
a Mac mini, a spare Android tablet running Linux. The one property that separates them in
practice is not speed:

**Power.** A Pi runs from 12 V through a small DC-DC converter, drawing a few watts, so it
can stay on at anchor and overnight on the house battery. Anything mains-powered needs an
inverter, wastes energy converting, and dies when shore power blinks — which means it is a
boat computer that only works alongside. The anchor alarm matters at anchor and the engine
log matters under way, so if the machine is going to live aboard permanently, 12 V native
wins and it is not close.

If your boat is always on shore power, or you only want rungs 0 and 1, none of this applies
and your laptop is fine.

> Do not power a Pi from a cigarette-lighter USB adapter. It will brown out on engine start
> and corrupt the SD card. A fused DC-DC converter off the house battery costs little and is
> the difference between a computer and an annoyance.

## Can the Pi connect to the boat's wires through its pins?

Yes — but **never directly to a sender**, and this is the part that costs people a Pi.

Two hard facts about the GPIO header:

1. **It is 3.3 V logic and it is not 5 V tolerant**, let alone 12 V. A boat's sender wire
   sits on a 12 V circuit. Putting it on a GPIO pin destroys the pin, and often the Pi.
2. **A Pi has no analogue input at all.** No ADC, on any model. Classic boat senders —
   coolant temperature, oil pressure, fuel level — are *resistive*: they are a variable
   resistor whose value the gauge reads. A GPIO pin can tell you high from low and nothing
   in between, so it physically cannot read one.

So the pins are useful, but for buses and digital sensors, not for senders:

| Through the pins | Needs | Good for |
|---|---|---|
| **CAN bus (NMEA 2000)** | a CAN HAT with a marine-grade isolated supply | The whole N2K network: GPS, depth, wind, modern engine data. The cleanest route on a boat built after roughly 2005 |
| **Serial / UART (NMEA 0183)** | an opto-isolated RS-422 level shifter | Older instruments, a talker-listener pair |
| **1-Wire** | a DS18B20 sensor and one resistor | Temperatures — cabin, fridge, engine bay, raw-water intake. Genuinely easy |
| **I²C / SPI** | an ADC breakout, e.g. a 16-bit ADS1115 | Reading resistive senders *after all* — this is the no-microcontroller route, and it works, but you are now building the analogue front end yourself: divider, protection, calibration |

**Isolation is not optional.** A boat's DC negative is a noisy, current-carrying thing, and
tying a computer's ground to it directly invites both damage and galvanic corrosion. Every
interface above should be opto- or galvanically isolated. The marine CAN HATs that include
their own isolated 12 V supply exist precisely because of this.

## Why this project uses a microcontroller for the senders

`arduino/` holds a sketch that reads analogue senders and emits NMEA sentences over USB. A
microcontroller is not there because a Pi could not do it — with an ADC HAT it could. It is
there because:

- it has real analogue inputs and tolerates 5 V natively
- it costs a few euros, so putting it where the heat and vibration are is an acceptable risk
- it does one thing in a loop and cannot be brought down by a full SD card or an OS update
- it is the isolation boundary: the noisy end is the cheap end, connected by a USB cable

That is a judgement, not a rule. If you would rather run an ADC HAT on the Pi and skip the
microcontroller, the rest of the stack neither knows nor cares — it consumes Signal K.

> ⚠️ **The sketch in this repository has never been tested against a real engine.** Every
> calibration constant in it is a placeholder taken from a published sender curve, not a
> measurement. Calibrate against your own gauge before you believe a number, and read the
> safety note in `arduino/README.md` before mounting anything near a petrol engine.

## Buy the gateway before the sensors

The single most useful thing you can do before spending money: **photograph the back of your
helm and your instrument panel.** That photograph decides whether you need an NMEA 2000
gateway, an NMEA 0183 adapter, or nothing at all because everything you want is already on a
bus. Buying before that photo exists is buying a guess.

Prices move and vary by country, so this document quotes none. Check them on the day.

## The display

Any browser is the display. A tablet at the helm is the obvious one, and the dashboard is
built for it — big touch targets, day and night palettes, arrangeable tiles.

The catch is brightness, not software: a consumer tablet in direct summer sun is hard to read
and gets hot. A sun cover fixes it cheaply. A genuinely sunlight-readable panel costs more
than the rest of this stack combined, and whether that is worth it is a question about how
much you sail at midday, not a question about software.

Phones already aboard are the underrated hardware. Each one has a GPS better than most 1990s
boat electronics, a barometer nobody is reading, a camera and its own battery and modem. An
old phone cable-tied in the cabin on a charger is an engine-bay camera or a bilge watcher,
and it is the shortest path between "is the boat alright" and a photograph of the boat.
