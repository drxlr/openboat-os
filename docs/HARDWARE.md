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

## Adapters that actually exist

The categories above map to real products. Prices are still deliberately absent — check
each manufacturer's page before buying. ⚠️ marks anything this list could not confirm from
a primary source.

Everything here was read off the manufacturers' own pages, the projects' own repositories,
or gpsd's hardware compatibility database, checked on **4 September 2026** and linked from
each entry below. If you check one and it has changed, a pull request correcting this table
is welcome — that is the kind of fact that goes stale fastest.

### NMEA 2000 → USB (the main route on anything built after roughly 2005)

| Product | Power | Isolated | Signal K / canboat | Note |
|---|---|---|---|---|
| [Actisense NGT-1](https://www.actisense.com/products/) | Bus-powered from N2K | Yes | [canboat](https://github.com/canboat/canboat)'s native driver targets this device's protocol | **Discontinued**, replaced by the NGX-1 |
| [Actisense NGX-1](https://www.actisense.com/product/ngx-1/) | Bus-powered from N2K (9–30 V) | Yes, opto/ISO-Drive | ⚠️ Not named explicitly in [canboat's docs](https://github.com/canboat/canboat), but the direct successor to the NGT-1, widely reported to run in NGT-1-compatible mode | Replaces both the NGT-1 and the NGW-1; also speaks NMEA 0183 |
| [Yacht Devices YDNU-02](https://www.yachtd.com/products/usb_gateway.html) | ⚠️ Not stated on the product page | Yes, galvanic | [canboat](https://github.com/canboat/canboat) reads its RAW format natively (shared with the YDWG-02) | Several connector variants |
| [Digital Yacht iKonvert](https://digitalyachtamerica.com/product/ikonvert-usb/) | Bus-powered from N2K | Yes, full galvanic | **Confirmed by the manufacturer**: "already compatible with CanBoat and the popular Signal K Node Server" | The most directly-confirmed plug-and-play option |
| [Maretron USB100](https://www.maretron.com/products/usb100.php) | ⚠️ Not stated | ⚠️ Not stated | Not mentioned by Maretron; [canboat](https://github.com/canboat/canboat) names only the Ethernet-based IPG100, not this USB unit | Built for Maretron's N2KView — weaker fit for an open-source stack |

### NMEA 2000 → Raspberry Pi HAT (CAN on the GPIO header)

| Product | Power | Isolated | Signal K | Note |
|---|---|---|---|---|
| [PICAN-M](https://www.skpang.co.uk/products/pican-m-with-can-bus-micro-c-and-rs422-connector-3a-smps) (SK Pang) | Optional 3 A SMPS variant runs the Pi off the 12 V line; a no-SMPS variant exists | CAN side isolated; ⚠️ 12 V-to-Pi power path isolation isn't stated | **Confirmed**: manufacturer lists SocketCAN, Signal K, CANboat, OpenCPN and OpenPlotter by name | Also sold in the US through [Copperhill Technologies](https://copperhilltech.com), same board |
| [Waveshare 2-CH CAN FD HAT](https://www.waveshare.com/2-ch-can-fd-hat.htm) | No onboard 12 V supply — Pi power is still your problem | Yes, isolated CAN transceivers with surge/ESD/short-circuit protection | ⚠️ Not marine-branded, not mentioned; works through the same SocketCAN path any CAN HAT does | Two independent CAN channels — N2K on one, engine J1939 on the other; generic industrial board |

### NMEA 2000 → ESP32 (a microcontroller instead of a Pi)

A CAN HAT assumes a Raspberry Pi underneath it. If the boat computer is somewhere else
entirely — a Mac indoors, a phone, nothing at all — an ESP32 on the bus is a smaller, colder
and much cheaper way in: it sits on the backbone, speaks NMEA 2000, and hands the data over
WiFi to whatever is listening.

| Product | Power | Isolated | Signal K | Note |
|---|---|---|---|---|
| [M5Stack Atom CAN kit](https://docs.m5stack.com/en/atom/atom_can) (K057) | From the bus, 12 V tolerant | Yes — CA-IS3050G isolated transceiver | Through the firmware below, which emits NMEA 0183 and SeaSmart over WiFi | Snap-together, no soldering. The whole gateway is roughly the price of a HAT's connector |
| [M5Stack Unit CAN](https://docs.m5stack.com/en/unit/can) (U085) | From the host module | Yes — same CA-IS3050G | Same route | The same transceiver as a plain breakout, for a controller you already have |
| [Waveshare SN65HVD230 board](https://www.waveshare.com/sn65hvd230-can-board.htm) | 3.3 V | **No** | Same route | Bare transceiver, no isolation. Cheapest and the one to avoid unless you understand what shares a ground with the engine block |

The firmware nearly everyone uses is
[esp32-nmea2000](https://github.com/wellenvogel/esp32-nmea2000) — a configurable
N2K ↔ 0183 ↔ WiFi gateway. For engine data specifically,
[VolvoPenta-N2K_Interface](https://github.com/buhhe/VolvoPenta-N2K_Interface) bridges a
Volvo Penta engine's J1939 bus onto NMEA 2000 with the Waveshare transceiver above.

### Reaching the autopilot at all

Every adapter above is about getting data **into** Signal K. `openboat/control/` needs the
opposite — a path **out** to the pilot — and it is worth being explicit that this is a
different problem with a different answer.

- **SeaTalkNG is NMEA 2000 electrically.** Raymarine's own connector, the same CAN bus
  underneath. Anything above that speaks N2K reaches it through an adapter cable.
- **SeaTalk1 is not.** The original Raymarine bus is three wires — +12 V, data, ground —
  wired in parallel, 4800 baud, 11-bit frames, no addressing and no master. Raymarine never
  published it; the whole DIY world works from
  [Thomas Knauf's reverse-engineered reference](http://www.thomasknauf.de/rap/seatalk1.htm).
  Reading it takes one transistor as an inverter. Writing to it takes a 74LS07 open-collector
  buffer between the 12 V line and a 5 V UART — the approach used by
  [AK-Homberger's autopilot remote](https://github.com/AK-Homberger/Seatalk-Autopilot-Remote-Control)
  (GPL-3.0; a reference to read, not code to copy into this project). That repository also
  documents a genuinely nasty gotcha: parts sold as "74LS07" are often CMOS, which clamp
  the output to 5 V and cannot reach SeaTalk's 12 V idle. Reading works; writing fails
  silently.

> **The bus does not know who you are.** SeaTalk1 has no addressing and no authentication:
> a course computer cannot tell OpenBoat's gated, armed, rate-limited command from a stray
> byte on the wire. Anything that can reach the bus can steer. That is a fact about the
> boat, not about this software, and it is why `openboat/control/` describes itself as a
> remote control with an audit trail rather than a safety system — the gate governs
> OpenBoat's own path and nothing else.

### NMEA 0183 → USB

The gap that matters here is isolation, not USB-to-serial conversion — any USB-serial chip
moves the bytes.

- **[Actisense USG-2](https://actisense.com/products/usg-2/)**: opto-isolated listener, isolated (ISO-Drive) talker, bidirectional, RS422/RS232, 300–230400 baud, shows up as a virtual COM port — a proper isolated marine adapter.
- A plain USB-to-serial cable (FTDI, CH340, whatever came with a cheap Arduino) has none of that. It moves the same bytes but ties your computer's ground straight to the boat's — the ground-loop and pin-killing risk from the isolation note above. Fine on a test bench, not on a boat.

### NMEA 0183 ↔ NMEA 2000, bidirectional

For a boat carrying both generations of instrument:

| Product | Note |
|---|---|
| [Actisense NGX-1](https://www.actisense.com/product/ngx-1/) | Same device as above — one box, both protocols, both directions |
| [Actisense NGW-1](https://www.actisense.com/products/) | **Discontinued**, folded into the NGX-1 |
| [Yacht Devices YDNG-03](https://www.yachtd.com/products/) | Confirmed bidirectional 0183↔2000, with AIS sentence pass-through |
| [Quark-elec A032](https://www.quark-elec.com) | Bidirectional N2K/0183 gateway with USB and WiFi in the same unit |

### GPS sources

- **[Digital Yacht GPS160-USB](https://digitalyachtamerica.com/product/gps160-usb/)**: multi-GNSS (GPS, GLONASS, BeiDou, Galileo), self-powered from USB, manufacturer states Linux support directly, appears as a virtual COM port. ⚠️ Chipset not stated.
- **u-blox chipset USB "mouse" GPS pucks**: the cheap, generic route, and it works because [gpsd's hardware compatibility database](https://gpsd.io/hardware.html) lists many as known-good — e.g. the Navisys GR-701W (u-blox 7, USB, PPS output). Nothing to install; it shows up as a serial port.
- **A phone already aboard**: an app that broadcasts NMEA 0183 over WiFi (UDP or TCP) feeds Signal K's network input with zero extra hardware — the phones-are-underrated-hardware point below, applied to position specifically.

### AIS receivers

- **[dAISy-catcher](https://shop.wegmatt.com/products/daisy-catcher-high-performance-ais-receiver)** (Wegmatt, built with the [AIS-catcher](https://github.com/jvde-github/AIS-catcher) project): a genuine simultaneous dual-channel receiver, not channel-hopping — USB device or Raspberry Pi HAT, outputs standard NMEA AIVDM sentences.
- **An RTL-SDR dongle running [AIS-catcher](https://github.com/jvde-github/AIS-catcher)** (open source): decodes both AIS channels off one wideband SDR. Confirmed support includes RTL-SDR (including the RTL-SDR Blog v4 and ShipXplorer AIS dongle), Airspy, HackRF, SDRplay and SoapySDR. Output is NMEA over UDP/TCP/HTTP, which lands in Signal K through its ordinary NMEA 0183-over-network input. Cheapest route, more setup, and the SDR can't do anything else while it runs.
- **Antenna**: a receive-only setup is happy on its own cheap antenna. Sharing the VHF antenna needs an *active* splitter with a fail-safe bypass, not a passive Y-cable — the [Digital Yacht SPL1500](https://digitalyachtamerica.com/product/spl1500-vhf-ais-antenna-splitter/) is a confirmed example: 12 V/24 V powered, passes VHF straight through if it loses power, rated for Class B transmit too, not just receive.

### Analogue sender front ends

- **[ADS1115](https://www.adafruit.com/product/1085) breakout**: the same 16-bit I2C ADC named in the pins section above — 4 single-ended or 2 differential channels, programmable gain up to 16×, address-selectable so up to 4 boards share one I2C bus.
- **[Yacht Devices YDTA-04](https://www.yachtd.com/products/)**: up to four resistive tank senders into one NMEA 2000 box, with preset curves for common tank shapes.
- **[Maretron TLA100](https://www.maretron.com/products/tla100.php)**: one resistive tank sender, American (240–33 Ω) or European (10–180 Ω) standard, or any custom curve from 0–300 Ω, calibratable for irregular tank shapes.

Both are built for **tanks** specifically. There is no equally tidy off-the-shelf N2K box
for a generic oil-pressure or coolant-temperature sender — that is still
ADS1115-and-calibrate-it-yourself territory, or the microcontroller route this project uses.

### Tank level and DC monitoring, past the resistive sender

Two gaps that a resistive float and a voltmeter do not fill.

| Product | What it does | Note |
|---|---|---|
| DS1603L ultrasonic sensor | Reads tank level through the wall — glued to the outside, nothing drilled, nothing in the fluid | Works on plastic and GRP tanks of sane thickness, not on a baffled or irregular one. Signal K path via [UltrasonicTankSensor](https://github.com/frewie/UltrasonicTankSensor) + SensESP. ⚠️ No manufacturer page found worth citing; the community repo is the reference |
| Peacefair PZEM-017 | DC energy meter — 0–300 V, 10–300 A through an external shunt, Modbus RTU out | The honest answer to state of charge: voltage under load tells you almost nothing, and a shunt counts what actually left the battery. Usable unmodified as a Modbus slave; the [Tasmota PZEM-0XX notes](https://tasmota.github.io/docs/PZEM-0XX/) cover wiring it to an ESP for a standalone WiFi monitor |

### WiFi bridges and multiplexers

| Product | Note |
|---|---|
| [Quark-elec A034B](https://www.quark-elec.com) | Bidirectional WiFi ↔ NMEA 2000, with NMEA 0183 and SeaTalk in and out — current, closest one-box answer to "get everything onto WiFi" |
| [Yacht Devices YDWG-02](https://www.yachtd.com/products/) | NMEA 2000 to WiFi, built for viewing on a phone or tablet |
| [Shipmodul MiniPlex-3](https://www.shipmodul.com) family | NMEA 0183 multiplexer/router/filter, USB and WiFi variants. The **Wi-N2K** variant bridges NMEA 2000 ↔ 0183 both ways and reads SeaTalk1; ⚠️ the caveat about the **PRO** line stands, because no PRO + N2K product exists |
| [Actisense W2K-2](https://www.actisense.com/product/w2k-2-nmea-2000-wifi-gateway/) | NMEA 2000 to WiFi. Replaces the **W2K-1, which Actisense now mark Retired** — check which one a listing is actually selling |
| Digital Yacht iKommunicate | The device Signal K's docs long pointed to. **Appeared discontinued when checked on 4 September 2026** — absent from [Digital Yacht's NMEA-interfaces catalogue](https://digitalyachtamerica.com/product-category/interfacing/nmea-interfaces/) then. Their current NMEA-2000-to-network product, [NjordLINK+](https://digitalyachtamerica.com/product/njordlink/), targets Digital Yacht's own cloud service, not a local Signal K bridge, so it isn't a drop-in replacement |

### Which one do I need

Start from the photograph, not this list. A boat with an existing NMEA 2000 backbone needs
one thing plugged into it: an iKonvert or NGX-1 for the best-documented path into Signal K,
a YDNU-02 for hardware canboat reads natively, or a PICAN-M if the computer is a Pi and you
want the gateway and compute in one box. A boat with only NMEA 0183 instruments and no
N2K backbone needs a converter that creates one — the YDNG-03 or the NGX-1 again — because
every other adapter above assumes that bus exists. A boat with neither, or with instruments
you'd rather not touch, is exactly the case the ADS1115-or-microcontroller route was built
for.

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
