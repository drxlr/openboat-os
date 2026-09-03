/*
 * engine_sender — analogue senders on a 5.7 V8 -> NMEA 0183 over USB -> Signal K
 *
 * This engine predates every marine data bus. There is no N2K to tap, no J1939, no
 * SmartCraft: there are resistive senders driving analogue gauges, and that is all.
 * This reads them in parallel with the gauges and speaks NMEA, which Signal K understands.
 *
 * ┌─────────────────────────────────────────────────────────────────────────────────┐
 * │ ⚠️  PETROL ENGINE BAY. Do NOT mount this board in the engine compartment.        │
 * │                                                                                  │
 * │ A gasoline engine space is a flammable-vapour space. Electrical equipment there  │
 * │ must be ignition-protected (ISO 8846 / ABYC E-11) — an open Arduino, its         │
 * │ regulator and any relay are not. Mount the board OUTSIDE the bay, in the cabin   │
 * │ or under the helm, and run the sender wires to it. The senders themselves are    │
 * │ already installed and already certified; the computer is the new risk, so put    │
 * │ the new risk where the vapour is not.                                            │
 * │                                                                                  │
 * │ Also: this taps senders in parallel. It must never become the reason a gauge     │
 * │ reads wrong. Verify every gauge still matches after wiring — see README.md.      │
 * └─────────────────────────────────────────────────────────────────────────────────┘
 *
 * ⚠️  UNTESTED ON THE BOAT. Every calibration constant below is a placeholder from the
 *     sender's published curve, not a measurement of this engine. Bench-calibrate against
 *     known resistances before believing a single number. See README.md.
 *
 * Wiring, one sender at a time, all grounds common with the engine:
 *   A0  oil pressure sender  via divider, see R_PULLUP
 *   A1  coolant temp sender  via divider
 *   A2  fuel level sender    via divider
 *   A3  battery +12 V        via divider, HIGH side (never straight to A3)
 *   D2  tach signal          from the tachometer's own feed, opto-isolated
 *   D3  bilge float switch   to ground, INPUT_PULLUP
 *
 * Output: one burst per second at 4800 baud, the NMEA 0183 standard rate.
 */

#include <Arduino.h>

// --- pins -----------------------------------------------------------------------------
const uint8_t PIN_OIL   = A0;
const uint8_t PIN_TEMP  = A1;
const uint8_t PIN_FUEL  = A2;
const uint8_t PIN_VOLTS = A3;
const uint8_t PIN_TACH  = 2;
const uint8_t PIN_BILGE = 3;

// --- calibration — PLACEHOLDERS, measure these ----------------------------------------
const float R_PULLUP     = 220.0;   // ohms, the fixed leg of each divider
const float VREF         = 5.0;     // board reference; use 3.3 on a 3V3 board
const float VOLTS_DIVIDER = 5.7;    // (R1+R2)/R2 for the battery divider
const uint8_t PULSES_PER_REV = 4;   // V8, points-style tach feed. VERIFY on this engine.

// US-standard senders. Both curves are approximations of a published table.
const float OIL_R_AT_0_PSI   = 240.0;
const float OIL_R_AT_80_PSI  = 33.0;
const float TEMP_R_AT_40C    = 700.0;
const float TEMP_R_AT_100C   = 55.0;

// Fuel sender resistance at the two tank extremes.
const float FUEL_R_EMPTY = 240.0;
const float FUEL_R_FULL  = 33.0;

// --- tach counting --------------------------------------------------------------------
volatile unsigned long pulses = 0;
unsigned long lastReport = 0;

void countPulse() { pulses++; }

// --- helpers --------------------------------------------------------------------------

// Resistance of the sender, from the voltage across a fixed pull-up divider.
float senderOhms(uint8_t pin) {
  float counts = analogRead(pin);
  if (counts <= 0) return 0.0;
  if (counts >= 1022) return 1.0e6;          // open circuit: disconnected sender
  float volts = counts * VREF / 1023.0;
  return R_PULLUP * volts / (VREF - volts);
}

// Straight-line interpolation between two calibration points. Good enough over the
// working range of these senders; replace with a lookup table if a gauge disagrees.
float interpolate(float value, float inLow, float inHigh, float outLow, float outHigh) {
  if (inHigh == inLow) return outLow;
  return outLow + (value - inLow) * (outHigh - outLow) / (inHigh - inLow);
}

void nmea(const String &body) {
  uint8_t sum = 0;
  for (unsigned int i = 0; i < body.length(); i++) sum ^= body[i];
  char tail[6];
  snprintf(tail, sizeof(tail), "*%02X", sum);
  Serial.print('$'); Serial.print(body); Serial.println(tail);
}

// --- setup / loop ---------------------------------------------------------------------

void setup() {
  Serial.begin(4800);                        // NMEA 0183 standard
  pinMode(PIN_TACH, INPUT);
  pinMode(PIN_BILGE, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_TACH), countPulse, RISING);
  lastReport = millis();
}

void loop() {
  unsigned long now = millis();
  if (now - lastReport < 1000) return;

  noInterrupts();
  unsigned long counted = pulses;
  pulses = 0;
  interrupts();

  float seconds = (now - lastReport) / 1000.0;
  lastReport = now;

  float rpm = (counted / seconds) * 60.0 / PULSES_PER_REV;

  float oilPsi = interpolate(senderOhms(PIN_OIL), OIL_R_AT_0_PSI, OIL_R_AT_80_PSI, 0.0, 80.0);
  float tempC  = interpolate(senderOhms(PIN_TEMP), TEMP_R_AT_40C, TEMP_R_AT_100C, 40.0, 100.0);
  float fuel   = interpolate(senderOhms(PIN_FUEL), FUEL_R_EMPTY, FUEL_R_FULL, 0.0, 1.0);
  float volts  = analogRead(PIN_VOLTS) * VREF / 1023.0 * VOLTS_DIVIDER;
  bool  bilge  = digitalRead(PIN_BILGE) == LOW;

  fuel = constrain(fuel, 0.0, 1.0);

  // RPM: standard sentence, engine, unit 1.
  nmea("IIRPM,E,1," + String(rpm, 0) + ",,A");

  // XDR transducer sentences. P is pressure in pascals, C is temperature in Celsius,
  // U is voltage, V is volume. Signal K's NMEA 0183 plugin maps the standard types;
  // the trailing names are ours and may need a mapping rule — see README.md.
  nmea("IIXDR,P," + String(oilPsi * 6894.76, 0) + ",P,OILPRESS");
  nmea("IIXDR,C," + String(tempC, 1) + ",C,ENGTEMP");
  nmea("IIXDR,U," + String(volts, 2) + ",V,BATT1");
  nmea("IIXDR,V," + String(fuel, 3) + ",P,FUEL");
  nmea("IIXDR,S," + String(bilge ? 1 : 0) + ",,BILGE");
}
