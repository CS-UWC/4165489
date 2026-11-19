#!/usr/bin/env python3
"""
=========================================================
CONTROLLER — the tiny ML learns the network, then you push
whichever attack script at it.
=========================================================
Flow:
  1. LEARN PHASE   : listen to the target topic, train the tiny
                     ML on baseline traffic, capture the real
                     node's messages (used by replay), build the
                     NetworkProfile the attack scripts adapt to.
  2. ATTACK PHASE  : launch the selected attack in a thread,
                     keep scoring every incoming message with the
                     frozen tiny ML, compute the F/T/D/P metrics
                     exactly like the dashboard, and log everything
                     to CSV (per-message features + ML + BTI).

All learn/capture/record logic lives in capture.py.

RUN:
  python controller.py --attack fdi
  python controller.py --attack flood --duration 120
  python controller.py --attack replay
  python controller.py --attack none        # baseline data only
  python controller.py --attack mimic --learn 90 --duration 120
=========================================================
"""

import argparse
import threading
import time

from capture import (CaptureSession, BROKER, PORT, USER, PASSWORD,
                     TARGET_TOPIC)

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


def _launch_attack(name, profile, raw_payloads, args, stop_event):
    module = ATTACK_MODULES[name]
    try:
        module.run(
            profile,
            broker=args.broker, port=args.port, user=args.user,
            password=args.password, target_topic=args.topic,
            duration=args.duration, stop_event=stop_event,
            captured=raw_payloads,
        )
    except Exception as exc:
        print(f"[controller] attack thread error ({name}): {exc}")


def parse_args():
    p = argparse.ArgumentParser(description="Tiny ML network learner + attack controller")
    p.add_argument("--broker", default=BROKER)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--user", default=USER)
    p.add_argument("--password", default=PASSWORD)
    p.add_argument("--topic", default=TARGET_TOPIC)
    p.add_argument("--learn", type=float, default=60.0,
                   help="baseline learning duration in seconds")
    p.add_argument("--duration", type=float, default=60.0,
                   help="attack/monitoring duration in seconds")
    p.add_argument("--attack", choices=list(ATTACK_MODULES) + ["none"],
                   default="fdi", help="attack to push at the network")
    p.add_argument("--out", default="runs", help="output directory for CSVs")
    p.add_argument("--threshold", type=float, default=None,
                   help="ML anomaly threshold (default: auto from baseline)")
    return p.parse_args()


def main():
    args = parse_args()

    session = CaptureSession(args.broker, args.port, args.user, args.password,
                             args.topic, args.out)
    profile, raw, times, tiny_ml = session.learn(args.learn, args.threshold)
    if tiny_ml is None:
        session.close()
        return

    # ---------- ATTACK PHASE ----------
    attack_name = args.attack
    session.attack_start = time.time()
    stop_event = threading.Event()
    attack_thread = None
    if attack_name != "none":
        print(f"=== ATTACK PHASE: {attack_name.upper()} for {args.duration}s ===")
        attack_thread = threading.Thread(
            target=_launch_attack,
            args=(attack_name, profile, raw, args, stop_event),
            daemon=True,
        )
        attack_thread.start()
    else:
        print(f"=== MONITORING PHASE: baseline for {args.duration}s ===")

    session.open_csv(f"run_{attack_name}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    for p, ts in zip(raw, times):
        session.record("baseline", ts, p, tiny_ml)

    phase = attack_name
    run_until = time.time() + args.duration if attack_name == "none" else None
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
    session.print_summary()


if __name__ == "__main__":
    main()
