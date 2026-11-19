#!/usr/bin/env python3
"""
=========================================================
CAPTURE — shared data-capture module + standalone recorder
=========================================================
This is the capture/logging core used by the controller
AND a standalone tool for pure data collection:

  * controller.py imports CaptureSession so its learn/record
    logic lives in exactly one place.
  * Standalone: record baseline traffic, or record the MQTT
    stream while you run an attack script in another terminal.

Standalone RUN:
  python capture.py --label baseline --learn 60 --duration 300
  python capture.py --label monitor  --learn 60 --duration 120

Every recorded message is written to a CSV row with:
  timestamp, phase, ML score, ML anomaly flag, the ML features,
  and the dashboard's own F/T/D/P/BTI metrics computed locally.
=========================================================
"""

import argparse
import csv
import json
import math
import os
import re
import threading
import time
from collections import deque

from adaptive_learner import build_profile, make_client
from tiny_ml import TinyML

# =========================
# CONFIG (defaults — override with CLI args)
# =========================
BROKER         = os.environ.get("MQTT_BROKER", "localhost")
PORT           = 1883
USER           = "esp32user"
PASSWORD       = os.environ.get("MQTT_PASS", "")
TARGET_TOPIC   = "iot/+/data"

# BTI weights and smoothing — identical to the dashboard (app.py)
W1, W2, W3, W4 = 0.25, 0.30, 0.25, 0.20
ALPHA = 0.7
WINDOW = 100

# Features the tiny ML learns (interval + payload size + per-node numeric fields).
# Every node publishes some subset of these. Fields a node does not send are
# simply absent from the feature dict and TinyML skips them, so the set is a
# union across the whole network rather than a per-node list.
FEATURE_FIELDS = [
    # node1 (DHT11 + MQ-131)
    "temp", "humidity", "ozone", "ozone_v",
    # node4 (AHT20 on I2C + LDR). temp and humidity are shared with node1
    # and are listed once above; light/light_v are unique to node4.
    "light", "light_v",
    # node2's OWN sensors (mic on MIC_PIN). These are the fields node2
    # publishes about itself, and are distinct from the node2_sound value
    # that node1 reports below as a peer echo.
    "sound", "sound_v",
    # node3's OWN sensors (HC-SR04 ultrasonic + reed switch on REED_AO).
    # reed_do is a firmware label ("magnet"/"clear") and proximity is a
    # categorical string ("near"/"far"/...), so neither can enter a numeric
    # feature vector; both are captured by field_audit.py for traceability but
    # neither appears in ML_FEATURES below.
    "distance", "reed_ao", "reed_v",
    # shared
    "rssi", "network_alerts",
    # Cross-node state each node reports. Recorded in the CSV for traceability,
    # but deliberately EXCLUDED from the ML feature vector below: these are
    # topology, not sensor behaviour. A node that learns while peers are
    # offline memorises node3_distance=0 / node2_sound=0 with a near-zero std,
    # and when a peer comes online the z-score explodes and every message reads
    # as an anomaly. Trust should be computed over a node's own readings and
    # cadence, not over whether its neighbours happen to be up.
    "node1_online", "node2_online", "node3_online", "node4_online",
    "node3_magnet", "node2_sound", "node3_distance",
]

# Sensor fields plus publish cadence. This, not FEATURE_FIELDS, is what the
# anomaly model scores. Excludes the *_online / node2_sound / node3_distance /
# node3_magnet block above, which describes network state rather than the
# node's own behaviour.
#
# payload_len is recorded in the CSV but deliberately NOT scored. It looks like
# a size signal, but the peer fields above are serialised into the same JSON,
# so the byte length changes whenever THEY change. Measured on
# runs_v4/iter4_r3 node3: flagged rows were 251/255/256 B, clean rows only
# 250/251 B - payload_len re-encoded the excluded peer noise.
#
# `interval` was scored here previously, on the reasoning that it carries
# publish cadence. That turned out not to hold. Publish timestamps quantise to
# whole seconds, so its baseline standard deviation depends almost entirely on
# how many late messages happen to land in a given ~58-sample baseline window:
# measured 0.093, 0.945 and 1.504 across identical unmodified firmware. Because
# the detector combines features as a root-sum-square, that 10x swing in one
# denominator decided whether the mimic attack was reported at 1.2% or 88.8%
# for identical behaviour, and independently pushed node2 fdi to 20.0%.
# interval is therefore recorded but NOT scored. Every run re-scored without
# it agrees: node1 fdi holds at 50.0/50.6/50.0/49.8% across four independent
# repeats while mimic, replay, timing and flood all fall to 0-4%.
ML_FEATURES = [
    "temp", "humidity", "ozone", "ozone_v", "light", "light_v",
    "sound", "sound_v",
    "distance", "reed_ao", "reed_v",
    "rssi",
]

CSV_HEADER = (["timestamp", "node", "phase", "ml_score", "ml_anomaly",
               "interval", "payload_len"] + FEATURE_FIELDS +
              ["F", "T", "D", "P", "bti", "bti_smoothed"])


def clamp01(x):
    return max(0.0, min(1.0, x))


class Stream:
    """Thread-safe MQTT message buffer (subscriber -> main loop)."""

    def __init__(self):
        self.items = deque()
        self.lock = threading.Lock()

    def push(self, payload, ts, topic=""):
        with self.lock:
            self.items.append((ts, payload, topic))

    def drain(self):
        with self.lock:
            out = list(self.items)
            self.items.clear()
        return out


def extract_features(payload, ts, prev_ts):
    """Turn one raw message into the tiny ML feature vector."""
    features = {}
    if prev_ts is not None:
        features["interval"] = ts - prev_ts
    features["payload_len"] = len(payload)
    try:
        data = json.loads(payload)
    except Exception:
        data = {}
    for f in FEATURE_FIELDS:
        v = data.get(f)
        if isinstance(v, bool):
            features[f] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            features[f] = float(v)
    return features


def build_tiny_ml(raw_payloads, timestamps, threshold=None, k=3.0):
    """Train a TinyML on ONE node's baseline and calibrate its threshold.

    Single source of truth for tiny-ML learning. controller.py, capture.py,
    attack_each.py and attack_all.py all call this, so the anomaly threshold
    is always derived from that node's own baseline score distribution
    rather than a shared constant. See investigations.md #10.
    """
    if not raw_payloads:
        return None

    # ML_FEATURES, not FEATURE_FIELDS: the model scores only this node's own
    # sensor readings plus message cadence. The *_online / node2_sound /
    # node3_distance / node3_magnet fields stay in the CSV but must not enter
    # the model, or a peer coming online makes every message look anomalous.
    tiny_ml = TinyML(list(ML_FEATURES))
    train_features = []
    prev = None
    for p, ts in zip(raw_payloads, timestamps):
        feats = extract_features(p, ts, prev)
        train_features.append(feats)
        tiny_ml.learn(feats)
        prev = ts

    if threshold is not None:
        tiny_ml.threshold = float(threshold)
        tiny_ml.threshold_source = "manual"
        tiny_ml.n_train = len(train_features)
    else:
        tiny_ml.autothreshold(train_features, k=k)

    return tiny_ml


# --- BTI metrics (mirrors dashboard app.py) ---
def f_metric(times):
    """Original frequency consistency: rewards rates up to/above expected (clamped).

    Note: can increase under flooding (high rate -> higher F). Kept for
    backward-compatible analysis; see f_metric_rate_aware for a symmetric variant.
    """
    if len(times) < 2:
        return 0.5
    duration = times[-1] - times[0]
    freq = len(times) / duration if duration != 0 else 0
    expected_freq = 0.5
    return clamp01(freq / expected_freq * 0.7)


def f_metric_rate_aware(times, expected_freq=0.5):
    """Rate-aware frequency consistency (analysis/comparison variant).

    Penalizes deviation both above and below the expected publish rate:
      F = clamp(1 - |rate/expected - 1|)
    expected -> 1.0, 2x (flood) -> 0.0, 0.5x -> 0.5, 0 -> 0.0.
    This addresses frequency-metric saturation observed under flooding.
    """
    if len(times) < 2:
        return 0.5
    duration = times[-1] - times[0]
    freq = len(times) / duration if duration != 0 else 0
    if expected_freq <= 0:
        return 0.5
    return clamp01(1.0 - abs((freq / expected_freq) - 1.0))


def t_metric(times):
    if len(times) < 3:
        return 0.5
    intervals = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    intervals = sorted(intervals)
    # Transport glitches (dropped/reordered frames, bursty delivery) produce
    # extreme intervals that wrongly zero a raw CV. Trim the top/bottom 10%
    # so legitimate baseline jitter doesn't tank T.
    trim = max(1, len(intervals) // 10)
    core = intervals[trim:-trim] if len(intervals) > 2 * trim else intervals
    if not core:
        core = intervals
    mean = sum(core) / len(core)
    if mean <= 0:
        return 0.5
    var = sum((d - mean) ** 2 for d in core) / len(core)
    cv = math.sqrt(var) / mean
    return clamp01(1.0 - cv)


def d_metric(payloads):
    if len(payloads) < 2:
        return 0.5
    # Collect per-field value sequences across the window
    field_vals = {}
    for p in payloads:
        try:
            data = json.loads(p)
            for k, v in data.items():
                # Skip metadata fields that aren't sensor data
                if k in ("node", "ip", "rssi", "source", "online",
                         "node1_online", "node2_online", "node3_online",
                         "node1_temp", "node2_sound", "network_alerts"):
                    continue
                if isinstance(v, (int, float)):
                    field_vals.setdefault(k, []).append(float(v))
        except Exception:
            continue
    # Average stability across all numeric sensor fields
    if not field_vals:
        return 0.5
    stabilities = []
    for vals in field_vals.values():
        if len(vals) < 2:
            continue
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        if mean != 0:
            stabilities.append(1 - var / (mean ** 2 + 1e-6))
    if not stabilities:
        return 0.5
    return clamp01(sum(stabilities) / len(stabilities))


def p_metric(payloads):
    if len(payloads) < 2:
        return 0.5
    lengths = [len(p) for p in payloads]
    mean = sum(lengths) / len(lengths)
    var = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    return clamp01(1 - math.sqrt(var) / (mean + 1e-6))


def compute_metrics(history):
    """history: deque of (timestamp, payload). Returns F, T, D, P."""
    times = [t for t, _ in history]
    payloads = [p for _, p in history]
    return (f_metric(times), t_metric(times),
            d_metric(payloads), p_metric(payloads))


def _new_stats():
    return {"n": 0, "score_sum": 0.0, "anomalies": 0, "first_alert": None,
            "F": 0.0, "T": 0.0, "D": 0.0, "P": 0.0, "bti": 0.0}


class CaptureSession:
    """Subscribes to a topic, learns the baseline with the tiny ML,
    then records every message (raw + features + ML + F/T/D/P/BTI).

    Typical use:
        session = CaptureSession(broker, port, user, password, topic, out)
        profile, raw, times, tiny_ml = session.learn(60, threshold=None)
        session.attack_start = time.time()
        session.open_csv("run_fdi.csv")
        for p, ts in zip(raw, times):
            session.record("baseline", ts, p, tiny_ml)
        ... session.record("attack", ts, payload, tiny_ml) per live message ...
        session.close_csv()
        session.close()
        session.print_summary()
    """

    def __init__(self, broker=BROKER, port=PORT, user=USER, password=PASSWORD,
                 topic=TARGET_TOPIC, out="runs"):
        self.broker = broker
        self.port = port
        self.user = user
        self.password = password
        self.topic = topic
        self.out = out
        os.makedirs(out, exist_ok=True)

        self.stream = Stream()
        # Unique client id per session so concurrent CaptureSessions
        # (capture.py + attack scripts, or multiple nodes in attack_all)
        # don't kick each other off the broker (duplicate client_id rule).
        cid = "cap_" + re.sub(r"[^A-Za-z0-9]", "_", topic) + "_" + str(os.getpid())
        self.sub = make_client(cid)
        self.sub.username_pw_set(user, password)
        self.sub.on_message = lambda c, u, m: self.stream.push(m.payload.decode(), time.time(), m.topic)
        self.sub.connect(broker, port, 60)
        self.sub.subscribe(topic)
        self.sub.loop_start()

        self.attack_start = time.time()
        self.stats = {"baseline": _new_stats()}
        self._history = deque(maxlen=WINDOW)
        self._bti_smooth = 1.0
        self._prev_ts = None
        self._csv_path = None
        self._writer = None

    def close(self):
        self.sub.loop_stop()
        self.sub.disconnect()

    def learn(self, duration, threshold=None):
        """Collect baseline traffic, train the tiny ML, build the profile.

        Returns (profile, raw_payloads, timestamps, tiny_ml).
        tiny_ml is None if no messages were learned (node offline).
        """
        samples, raw_payloads, timestamps = [], [], []
        end = time.time() + duration
        print(f"=== LEARN PHASE: observing {self.topic} for {duration}s ===")
        while time.time() < end:
            for ts, payload, topic in self.stream.drain():
                raw_payloads.append(payload)
                timestamps.append(ts)
                try:
                    samples.append(json.loads(payload))
                except Exception:
                    samples.append({})
            time.sleep(0.05)

        profile = build_profile(samples, timestamps, [len(p) for p in raw_payloads])
        print(profile)

        if not raw_payloads:
            print("\nNo messages learned — is the node publishing? Exiting.")
            return profile, [], [], None

        tiny_ml = build_tiny_ml(raw_payloads, timestamps, threshold)
        print(f"TinyML trained on {tiny_ml.n_train} samples — "
              f"anomaly threshold = {tiny_ml.threshold:.2f} "
              f"({tiny_ml.threshold_source})")

        profile_path = os.path.join(self.out, "profile.json")
        with open(profile_path, "w") as fh:
            json.dump({
                "sample_count": profile.sample_count,
                "interval_mean": profile.interval_mean,
                "interval_std": profile.interval_std,
                "payload_len_mean": profile.payload_len_mean,
                "payload_len_std": profile.payload_len_std,
                "field_stats": profile.field_stats,
                "string_stats": profile.string_stats,
            }, fh, indent=2)
        print(f"Profile saved to {profile_path}")

        return profile, raw_payloads, timestamps, tiny_ml

    def open_csv(self, name):
        self._csv_path = os.path.join(self.out, name)
        self._fh = open(self._csv_path, "w", newline="")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(CSV_HEADER)

    def close_csv(self):
        if self._writer:
            self._fh.close()
            self._writer = None

    def record(self, phase, ts, payload, tiny_ml, node="unknown"):
        """Score one message, compute metrics, append a CSV row.

        Returns (F, T, D, P, bti, score, anomaly).
        """
        features = extract_features(payload, ts, self._prev_ts)
        score, anomaly = tiny_ml.detect(features)
        self._history.append((ts, payload))
        F, T, D, P = compute_metrics(self._history)
        bti = W1 * F + W2 * T + W3 * D + W4 * P

        stats = self.stats.setdefault(phase, _new_stats())
        stats["n"] += 1
        stats["score_sum"] += score
        stats["F"] += F
        stats["T"] += T
        stats["D"] += D
        stats["P"] += P
        stats["bti"] += bti
        if anomaly:
            stats["anomalies"] += 1
            if stats["first_alert"] is None:
                stats["first_alert"] = ts - self.attack_start

        feat_cols = []
        for f in FEATURE_FIELDS:
            v = features.get(f)
            feat_cols.append(f"{v:.3f}" if isinstance(v, float) else
                             (int(v) if isinstance(v, bool) else v))
        self._writer.writerow(
            [f"{ts:.3f}", node, phase, f"{score:.3f}", int(anomaly),
             f"{features['interval']:.3f}" if "interval" in features else "",
             features.get("payload_len", "")]
            + feat_cols
            + [f"{F:.3f}", f"{T:.3f}", f"{D:.3f}", f"{P:.3f}",
               f"{bti:.3f}", f"{self._bti_smooth:.3f}"])

        self._bti_smooth = ALPHA * self._bti_smooth + (1 - ALPHA) * bti
        self._prev_ts = ts
        return F, T, D, P, bti, score, anomaly

    def record_drain(self, phase, tiny_ml):
        """Record every message currently buffered in the stream."""
        for ts, payload, topic in self.stream.drain():
            node = self._extract_node(topic)
            self.record(phase, ts, payload, tiny_ml, node)

    @staticmethod
    def _extract_node(topic):
        """Extract node ID from MQTT topic like iot/node1/data."""
        try:
            parts = topic.split("/")
            if len(parts) == 3 and parts[0] == "iot":
                return parts[1]
        except Exception:
            pass
        return "unknown"

    def print_summary(self):
        print(f"\n=== RESULTS ({self._csv_path}) ===")
        base = self.stats.get("baseline")
        base_bti = None
        if base and base["n"]:
            print(f"Baseline:  {base['n']} msgs, mean ml_score={base['score_sum']/base['n']:.2f}, "
                  f"flagged={base['anomalies']} ({100*base['anomalies']/base['n']:.1f}%)")
            base_bti = base["bti"] / base["n"]
        for phase, st in self.stats.items():
            if phase == "baseline" or st["n"] == 0:
                continue
            print(f"Phase '{phase}': {st['n']} msgs, mean ml_score={st['score_sum']/st['n']:.2f}, "
                  f"flagged={st['anomalies']} ({100*st['anomalies']/st['n']:.1f}%)")
            if st["first_alert"] is not None:
                print(f"  first alert after {st['first_alert']:.2f}s of phase")
            print(f"  mean F/T/D/P = {st['F']/st['n']:.3f}/{st['T']/st['n']:.3f}/"
                  f"{st['D']/st['n']:.3f}/{st['P']/st['n']:.3f}")
            if base_bti:
                att_bti = st["bti"] / st["n"]
                print(f"  BTI: baseline {base_bti:.3f} -> {phase} {att_bti:.3f} "
                      f"(drop {100*(base_bti-att_bti)/max(base_bti,1e-9):.1f}%)")


def parse_args():
    p = argparse.ArgumentParser(
        description="Standalone MQTT data capture + tiny ML anomaly scoring")
    p.add_argument("--broker", default=BROKER)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--user", default=USER)
    p.add_argument("--password", default=PASSWORD)
    p.add_argument("--topic", default=TARGET_TOPIC)
    p.add_argument("--learn", type=float, default=60.0,
                   help="baseline learning duration in seconds")
    p.add_argument("--duration", type=float, default=0.0,
                   help="recording duration in seconds (0 = run until Ctrl+C)")
    p.add_argument("--label", default="capture",
                   help="phase label written to every recorded row")
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

    session.open_csv(f"capture_{args.label}_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    for p, ts in zip(raw, times):
        session.record("baseline", ts, p, tiny_ml)

    session.attack_start = time.time()
    if args.duration > 0:
        print(f"=== RECORDING PHASE: '{args.label}' for {args.duration}s ===")
        end = time.time() + args.duration
        while time.time() < end:
            session.record_drain(args.label, tiny_ml)
            time.sleep(0.05)
    else:
        print(f"=== RECORDING PHASE: '{args.label}' (Ctrl+C to stop) ===")
        try:
            while True:
                session.record_drain(args.label, tiny_ml)
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nStopping capture...")
    session.record_drain(args.label, tiny_ml)

    session.close_csv()
    session.close()
    session.print_summary()


if __name__ == "__main__":
    main()
