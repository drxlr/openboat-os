#!/usr/bin/env python3
"""XDR sentences in, Signal K deltas out — the piece the engine data cannot arrive without.

## Why this exists

Signal K's NMEA 0183 parser ships hooks for exactly these sentences:

    ALK APB BOD BWC BWR DBK DBS DBT DPT DSC GGA GLL GNS GSV HDG HDM HDT HSC MDA MTA
    MTW MWD MWV RMB RMC ROT RPM RSA VDM VDO VDR VHW VLW VPW VTG VWR VWT XTE ZDA

**There is no XDR hook at all.** Verified by listing the parser's own hooks directory in
the running container. So `$IIRPM` from `engine_sender.ino` arrives as
`propulsion.engine_1.revolutions` and works, while the four `$IIXDR` sentences carrying
oil pressure, coolant temperature, battery voltage and tank level are read and silently
dropped. Nothing in NMEA 0183 that Signal K parses can carry them.

The Arduino keeps speaking XDR because that is the portable thing to speak — any plotter
or logger can take it. This bridge sits between it and Signal K and speaks Signal K's own
delta format, which the server accepts natively.

## Wiring

Read from the Arduino on the boat, from the simulator on the bench:

    python3 xdr_bridge.py --from-tcp localhost:10110 --port 10120     # bench
    python3 xdr_bridge.py --serial /dev/ttyUSB0 --port 10120          # aboard

Then add a second connection in Signal K of type **SignalK**, TCP, to `host:10120`.
It stays read-only: this process only ever writes to its own socket.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time

# XDR transducer name -> (Signal K path, converter from the sentence's own unit).
# Signal K is SI throughout: kelvin, pascal, volts, ratio 0..1, hertz.
MAPPING = {
    "OILPRESS": ("propulsion.engine_1.oilPressure", lambda v: v),               # already Pa
    "ENGTEMP": ("propulsion.engine_1.temperature", lambda v: v + 273.15),       # °C -> K
    "BATT1": ("electrical.batteries.house.voltage", lambda v: v),
    "FUEL": ("tanks.fuel.main.currentLevel", lambda v: max(0.0, min(1.0, v))),
    "BILGE": ("sensors.bilge.state", lambda v: bool(v)),
}

SOURCE = {"label": "engine-sender", "type": "NMEA0183"}


def checksum_ok(line: str) -> bool:
    """A sentence with a bad checksum is a sentence that arrived wrong. Drop it."""
    if "*" not in line or not line.startswith("$"):
        return False
    body, _, given = line[1:].partition("*")
    value = 0
    for char in body:
        value ^= ord(char)
    return f"{value:02X}" == given.strip().upper()[:2]


def parse_xdr(line: str) -> list[tuple[str, float]]:
    """`$IIXDR,C,82.4,C,ENGTEMP,U,13.85,V,BATT1*hh` — quadruples of type, value, unit, name."""
    fields = line[1:].split("*")[0].split(",")[1:]
    found = []
    for i in range(0, len(fields) - 3, 4):
        _kind, raw, _unit, name = fields[i:i + 4]
        entry = MAPPING.get(name.strip().upper())
        if not entry or not raw:
            continue
        try:
            found.append((entry[0], entry[1](float(raw))))
        except ValueError:
            continue          # a sender that has gone open-circuit sends an empty field
    return found


def delta(values: list[tuple[str, float]]) -> str:
    return json.dumps({"updates": [{
        "source": SOURCE,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "values": [{"path": path, "value": value} for path, value in values],
    }]}) + "\r\n"


def lines_from_tcp(host: str, port: int):
    """Reconnecting reader. The bench simulator restarts; the boat's power blinks."""
    while True:
        try:
            with socket.create_connection((host, port), timeout=10) as connection:
                buffer = b""
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, _, buffer = buffer.partition(b"\n")
                        yield raw.decode("ascii", "replace").strip()
        except OSError:
            time.sleep(2)


def lines_from_serial(device: str, baud: int):
    """No pyserial — stty and a plain read, so this runs on a Pi with nothing installed."""
    import subprocess
    subprocess.run(["stty", "-F", device, str(baud), "raw", "-echo"], check=True)
    with open(device, "rb", buffering=0) as port:
        buffer = b""
        while True:
            chunk = port.read(256)
            if not chunk:
                time.sleep(0.1)
                continue
            buffer += chunk
            while b"\n" in buffer:
                raw, _, buffer = buffer.partition(b"\n")
                yield raw.decode("ascii", "replace").strip()


# A sender that has died must go quiet, not repeat itself. The bridge re-sent whatever it
# last held once a second with a fresh timestamp, so a dead Arduino looked to Signal K like
# a healthy engine at constant rpm — and the engine log's staleness guard, which is what
# stops the hour meter counting, never fired. Phantom engine hours are worse than no hours.
MAX_AGE_S = 10.0


def serve(source, host: str, port: int) -> None:
    """Hold the newest reading and hand it to whoever connects. Signal K is the only client."""
    latest: dict[str, tuple[float, float]] = {}      # path -> (value, monotonic seen-at)
    lock = threading.Lock()

    def read() -> None:
        for line in source:
            if not line.startswith("$") or "XDR" not in line[:8] or not checksum_ok(line):
                continue
            found = parse_xdr(line)
            if found:
                seen = time.monotonic()
                with lock:
                    latest.update({path: (value, seen) for path, value in found})

    threading.Thread(target=read, daemon=True).start()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(5)
    print(f"Signal K deltas on {host}:{port} — add a SignalK/TCP connection to it", flush=True)

    def talk(client: socket.socket, address) -> None:
        print(f"  connected: {address}", flush=True)
        try:
            while True:
                now = time.monotonic()
                with lock:
                    values = [(path, value) for path, (value, seen) in latest.items()
                              if now - seen <= MAX_AGE_S]
                if values:
                    client.sendall(delta(values).encode())
                time.sleep(1)
        except OSError:
            print(f"  gone: {address}", flush=True)
        finally:
            client.close()

    while True:
        client, address = listener.accept()
        threading.Thread(target=talk, args=(client, address), daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-tcp", metavar="HOST:PORT",
                        help="read NMEA from a TCP source, e.g. the bench simulator")
    parser.add_argument("--serial", metavar="DEVICE", help="read NMEA from a serial port")
    parser.add_argument("--baud", type=int, default=4800)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=10120)
    args = parser.parse_args()

    if args.serial:
        source = lines_from_serial(args.serial, args.baud)
    elif args.from_tcp:
        host, _, port = args.from_tcp.partition(":")
        source = lines_from_tcp(host, int(port or 10110))
    else:
        parser.error("give either --from-tcp or --serial")

    serve(source, args.host, args.port)
