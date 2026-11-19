"""
preflight.py - verify the live nodes match the capture schema BEFORE spending
11 minutes per run.

Why this exists: the archived campaign ran for weeks with node2 and node3
producing connectivity fields only, because capture.py had no columns for
sound, distance, reed_ao or reed_v. Nothing failed loudly; the data was
silently incomplete and only became visible when the model coverage was
audited. A schema mismatch between what a node publishes and what capture.py
records will produce exactly the same silent gap.

This subscribes to iot/+/data for --seconds and, for every node that speaks,
reports:
  - which keys the node actually publishes
  - whether those keys match the payloads.NODE_SCHEMAS entry for that node
  - which of the new sensor fields (sound, sound_v, distance, reed_ao, reed_v)
    are present and whether they carry VARYING values

The variation check matters for node3: an ultrasonic sensor with no echo reads
0, and a constant 0 gives a degenerate baseline that cannot be scored. That
would invalidate a node3 capture, so it is worth 20 seconds now rather than
11 minutes later.

Usage:
    python preflight.py
    python preflight.py --seconds 30
"""

import argparse
import collections
import json
import os
import sys
import time

import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import payloads  # noqa: E402

BROKER = os.environ.get("MQTT_BROKER", "localhost")
PORT = 1883
USER = "esp32user"
PASSWORD = os.environ.get("MQTT_PASS", "")

# New sensor fields this campaign exists to collect.
NEW_SENSOR_FIELDS = ("sound", "sound_v", "distance", "reed_ao", "reed_v")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--broker", default=BROKER)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--user", default=USER)
    ap.add_argument("--password", default=PASSWORD)
    args = ap.parse_args()

    keys = collections.defaultdict(collections.Counter)
    values = collections.defaultdict(lambda: collections.defaultdict(list))
    count = collections.Counter()
    samples = {}

    def on_message(_c, _u, msg):
        parts = msg.topic.split("/")
        if len(parts) < 2:
            return
        node = parts[1]
        try:
            payload = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        count[node] += 1
        samples[node] = payload
        for k, v in payload.items():
            keys[node][k] += 1
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values[node][k].append(v)

    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                      client_id="preflight_py")
    cli.username_pw_set(args.user, args.password)
    cli.on_message = on_message
    cli.connect(args.broker, args.port, 60)
    cli.subscribe("iot/+/data", qos=0)
    cli.loop_start()
    print("listening on %s:%s for %.0fs ..." % (args.broker, args.port,
                                                args.seconds))
    time.sleep(args.seconds)
    cli.loop_stop()
    cli.disconnect()

    if not count:
        print("\nNO NODES PUBLISHING on iot/+/data")
        print("check: nodes powered? broker reachable? topic names?")
        return 2

    print("\n" + "=" * 68)
    print("PREFLIGHT - %d nodes publishing" % len(count))
    print("=" * 68)

    # Constant sensor fields are a WARNING, not a blocker. Quantised sensors
    # (DHT11 reports integer degrees) are constant over short windows by
    # construction, and TinyML.SENSOR_FLOOR exists precisely to neutralise the
    # degenerate baseline that results. A missing field is the blocker,
    # because a capture started then cannot record the data at all.
    warnings = []
    blocking = []

    for node in sorted(count):
        observed = list(keys[node])
        schema = payloads.NODE_SCHEMAS.get(node)
        print("\n%s  (%d msgs, %d keys)" % (node.upper(), count[node],
                                            len(observed)))
        print("  publishes: %s" % ", ".join(observed))

        if schema is None:
            print("  schema:    MISSING from payloads.NODE_SCHEMAS")
            blocking.append("%s has no schema" % node)
            continue

        expected = list(schema["fields"])
        missing = [f for f in expected if f not in observed]
        extra = [f for f in observed if f not in expected]
        if not missing and not extra:
            print("  schema:    MATCHES payloads.NODE_SCHEMAS exactly")
        else:
            print("  schema:    MISMATCH")
            if missing:
                print("    declared but NOT published: %s" % missing)
                blocking.append("%s does not publish %s" % (node, missing))
            if extra:
                print("    published but not declared: %s" % extra)

        sensor = list(schema["sensor_fields"])
        if not sensor:
            print("  sensors:   none declared")
            continue
        print("  sensor fields:")
        for f in sensor:
            if f not in values[node]:
                print("    %-10s ABSENT" % f)
                blocking.append("%s: sensor field %s absent" % (node, f))
                continue
            vs = values[node][f]
            lo, hi = min(vs), max(vs)
            spread = hi - lo
            constant = spread == 0
            print("    %-10s n=%-4d min=%-9s max=%-9s spread=%-9s %s"
                  % (f, len(vs), round(lo, 3), round(hi, 3),
                     round(spread, 3),
                     "CONSTANT - degenerate baseline" if constant else ""))
            if constant and len(vs) >= 3:
                warnings.append(
                    "%s: sensor field %s is CONSTANT (%s) over this window - "
                    "expect a floored baseline" % (node, f, lo))

        for f in ("proximity", "reed_do"):
            if f in observed:
                print("  %s: %r" % (f, samples[node].get(f)))

    print("\n" + "=" * 68)
    if warnings:
        print("WARNINGS (non-blocking, but expect reduced baseline quality):")
        for w in warnings:
            print("  ? %s" % w)
        print()
    if blocking:
        print("BLOCKING - resolve before starting a capture run:")
        for b in blocking:
            print("  ! %s" % b)
        return 1
    if warnings:
        print("PASSED WITH WARNINGS - schemas match and every sensor field is "
              "live, but the fields listed above are constant over this "
              "window. Their models will be floored, which is correct "
              "behaviour, though a shorter-than-expected baseline is expected "
              "for them.")
    else:
        print("OK - all publishing nodes match their schemas and sensor "
              "fields are present and varying.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
