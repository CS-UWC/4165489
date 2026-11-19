"""
=========================================================
TINY ML — ONLINE GAUSSIAN NOVELTY DETECTOR
=========================================================
A deliberately small, explainable learner: one incremental
Gaussian per feature (Welford's algorithm — no full data
buffering, O(1) memory) and a Mahalanobis-like distance as
the anomaly score.

  learn(features)     → update the model on baseline traffic
  autothreshold(...)  → pick a flag threshold from baseline
  detect(features)    → (score, is_anomaly)

Swap this module out for scikit-learn later if the thesis
needs a heavier model — the controller only uses score()/detect().
=========================================================
"""

import math


class OnlineGaussian:
    """Incremental mean / variance via Welford's algorithm."""

    # Weight given to a field whose baseline variance was exactly zero but which
    # has now produced a different value. Large enough to cross a normal
    # threshold on its own, small enough not to swamp the other features.
    ZERO_STD_DEPARTURE = 4.0

    # Per-sensor minimum deviation, in the sensor's own published units.
    #
    # A short learn window can produce a baseline std far below what the
    # hardware can physically resolve. Measured on node1 (DHT11 + MQ-131) with
    # LEARN=120: temp sd was exactly 0.000 (DHT11 quantises to 1 deg C) and
    # humidity sd was 0.018 %RH. A DHT11 is rated +/-2 %RH with 2 %RH
    # resolution, so that baseline std is ~1000x finer than the sensor. The
    # resulting z-scores were meaningless: a 0.17 %RH ambient shift scored
    # 9.40, and temp's quantisation alone contributed a flat 4.0, giving an
    # aggregate of 10.57 and flagging 93% of healthy traffic.
    #
    # Flooring at the datasheet quantum keeps those sub-resolution shifts at
    # zero while leaving real attacks intact: a 538% FDI swing on a field with
    # a 1 deg C floor still scores in the hundreds of z. This is deliberately
    # NOT the blanket relative floor rejected earlier (investigations.md) -
    # that one hid a real 26% drift by raising denominators across every field,
    # whereas this only discounts noise finer than the sensor can report.
    SENSOR_FLOOR = {
        "temp": 1.0,        # DHT11: 1 deg C quantisation
        "humidity": 2.0,    # DHT11: 2 %RH quantisation
        "ozone": 12.0,      # MQ-131 12-bit ADC, ~3 LSB
        "ozone_v": 0.01,
        "light": 1.0,
        "light_v": 0.01,
        # node2 (mic on MIC_PIN). analogRead returns 0..4095 and the firmware
        # derives sound_v = sound * (3.3/4095). Floors are the sensor's own
        # quantum, NOT a unit-blind constant: applying a 0.5 floor to a
        # voltage field is what let FDI drive ozone_v to +/-5.0 V.
        "sound": 12.0,       # ~3 LSB of the 12-bit ADC
        "sound_v": 0.01,     # ~0.01 V ADC resolution
        # node3 is an ARDUINO NANO (ATmega328P), so REED_AO is a 10-bit ADC
        # spanning 0..1023, not the 0..4095 of the ESP32 ADCs on node1/node2.
        # ~3 LSB is therefore 3 counts, not 12.
        "distance": 1.0,     # HC-SR04 is specified to +/-1 cm
        "reed_ao": 3.0,      # ~3 LSB of the 10-bit ADC
    }

    def __init__(self, field=None):
        self.field = field
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def update(self, x):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    def std(self):
        if self.n < 2:
            return None
        s = math.sqrt(self.m2 / (self.n - 1))
        floor = self.SENSOR_FLOOR.get(self.field, 0.0)
        return max(s, floor)

    def zscore(self, x):
        if self.n < 2:
            return 0.0
        if x == self.mean:
            return 0.0
        s = self.std()
        if s is None:
            return 0.0
        if s == 0.0:
            # Only reachable for a field with no SENSOR_FLOOR entry (rssi,
            # network_alerts). A perfectly constant one - a counter that
            # never moved - carries no distributional information. Returning a
            # large sentinel here saturated the whole score and made every
            # message read as anomalous: one such field gave
            # sqrt(100^2 + rest) ~ 100 regardless of how normal the node was.
            # A new value on a previously flat field is worth surfacing, but
            # it must not manufacture a score of its own magnitude.
            return self.ZERO_STD_DEPARTURE
        return (x - self.mean) / s


class TinyML:
    """Tiny online Gaussian novelty detector over a feature vector."""

    def __init__(self, fields, threshold=None):
        self.fields = list(fields)
        self.gaussians = {f: OnlineGaussian(f) for f in self.fields}
        self.threshold = threshold
        self.threshold_source = "manual" if threshold is not None else None
        self.baseline_scores = []
        self.n_train = 0

    def learn(self, features):
        for f in self.fields:
            v = features.get(f)
            if v is not None:
                self.gaussians[f].update(v)

    def score(self, features):
        """Aggregate z-distance: sqrt(sum of squared per-feature z-scores)."""
        zs = []
        for f in self.fields:
            v = features.get(f)
            if v is not None and self.gaussians[f].std() is not None:
                zs.append(self.gaussians[f].zscore(v))
        if not zs:
            return 0.0
        return math.sqrt(sum(z * z for z in zs))

    # A threshold of zero flags every message with any deviation at all. That
    # happens whenever a baseline happens to be very regular: all its scores
    # are ~0, so mean + k*std collapses to ~0 and the detector becomes
    # infinitely sensitive. Reproduce 2 and 3 of the mimic attack on the same
    # firmware and this was the entire difference between 1.2% and 88.8%
    # flagged. The floor keeps a quiet baseline from disarming the detector.
    MIN_THRESHOLD = 3.0

    def autothreshold(self, features_list, k=3.0, default=5.0):
        """Set threshold from the baseline score distribution (mean + k*std).

        MUST be called with the node's own training features. Passing an
        empty/short list silently falls back to `default`, which is not a
        per-node calibration — see investigations.md #10.
        """
        scores = [self.score(f) for f in features_list]
        self.baseline_scores = scores
        self.n_train = len(scores)
        if len(scores) < 2:
            self.threshold = default
            self.threshold_source = "default"
            print(f"[tiny_ml] WARNING: only {len(scores)} baseline score(s) — "
                  f"falling back to fixed threshold {default:.2f} "
                  f"(NOT calibrated for this node)")
            return self.threshold
        mean = sum(scores) / len(scores)
        var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
        raw = mean + k * math.sqrt(var)
        self.threshold = max(raw, self.MIN_THRESHOLD)
        if raw < self.MIN_THRESHOLD:
            self.threshold_source = "auto(floored)"
            print(f"[tiny_ml] baseline too regular: computed threshold "
                  f"{raw:.3f} < floor {self.MIN_THRESHOLD:.2f}, using floor")
        else:
            self.threshold_source = "auto"
        return self.threshold

    def detect(self, features):
        s = self.score(features)
        anomaly = s > self.threshold if self.threshold is not None else False
        return s, anomaly
