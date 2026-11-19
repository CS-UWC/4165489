"""Replay real node1 baseline data through the current TinyML.

Reads a run_node1_none_*.csv from a failed repeat and re-scores the
monitor phase with the sensor floors applied. Confirms the fix against
measured data rather than a synthetic payload.
"""
import csv
import glob
import math
import sys

import capture
from tiny_ml import TinyML


def rows_for(feat_rows):
    return {k: v for k, v in feat_rows.items() if v is not None}


def main():
    args = sys.argv[1:]
    # --drop FIELD excludes a feature from the fitted model, so the
    # contribution of an unstable field can be measured without editing the
    # shared capture schema.
    drop = set()
    while "--drop" in args:
        i = args.index("--drop")
        drop.add(args[i + 1])
        del args[i:i + 2]

    pattern = args[0] if args else ""
    path = glob.glob(pattern)
    if not path:
        print("no CSV matched", pattern)
        return
    path = path[0]
    print("file:", path)

    with open(path, newline="") as fh:
        data = list(csv.DictReader(fh))

    def num(x):
        if x is None or x == "":
            return None
        try:
            return float(x)
        except ValueError:
            return None

    # Baseline files use phases baseline/none; attack files use the attack
    # name (mimic, flood, fdi, replay, timing). Collect baseline separately and
    # treat every other phase as "the phase under test".
    base_rows = [r for r in data if r["phase"] == "baseline"]
    atk_phase = None
    for r in data:
        if r["phase"] != "baseline":
            atk_phase = r["phase"]
            break
    atk_rows = [r for r in data if r["phase"] == atk_phase] if atk_phase else []

    print("  baseline rows: %d   test phase: %s (%d rows)" % (
        len(base_rows), atk_phase, len(atk_rows)))

    def feats(rows):
        out = []
        for r in rows:
            feat = {}
            for f in capture.ML_FEATURES:
                feat[f] = num(r.get(f))
            out.append(feat)
        return out

    learn = [f for f in feats(base_rows) if len(rows_for(f)) >= 2]
    monitor = [f for f in feats(atk_rows) if len(rows_for(f)) >= 2]

    if len(learn) < 3 or len(monitor) < 3:
        print("not enough usable rows:", len(learn), "learn /",
              len(monitor), "test")
        return

    present = sorted({k for f in monitor for k in rows_for(f)})
    print("  fields present in this node's data:")
    print("   ", present)

    # Node3 publishes distance/proximity/reed rather than node1's sensor set,
    # so fit whatever this node actually reports instead of assuming node1's
    # schema. Requiring only the fields present keeps node3 analysable.
    fitted = [f for f in capture.ML_FEATURES if f in present and f not in drop]
    dropped = [f for f in capture.ML_FEATURES
               if f not in present or f in drop]
    print("  fitting on:", fitted)
    if dropped:
        print("  absent (no sensor for this node, skipped):", dropped)

    m = TinyML(list(fitted))
    for f in learn:
        m.learn(f)
    thr = m.autothreshold(learn)

    scores = [m.score(f) for f in monitor]
    flagged = sum(1 for s in scores if s > thr)
    rate = 100.0 * flagged / len(scores)
    mean = sum(scores) / len(scores)

    print()
    print("  features      :", fitted)
    print("  learn rows    :", len(learn))
    print("  monitor rows  :", len(monitor))
    print("  threshold     : %.3f (%s)" % (thr, m.threshold_source))
    print("  mean score    : %.2f" % mean)
    print("  max score     : %.2f" % max(scores))
    print("  flagged       : %d/%d (%.1f%%)" % (flagged, len(scores), rate))
    print()
    print("  GATE (must be <= 5.0%%): %s" % ("PASS" if rate <= 5.0 else "FAIL"))

    worst = max(monitor, key=lambda f: m.score(f))
    print()
    print("  worst monitor row score %.2f:" % m.score(worst))
    for k in fitted:
        if k not in rows_for(worst) or k not in m.gaussians:
            continue
        g = m.gaussians[k]
        v = worst[k]
        if g.std() is None:
            print("    %-16s v=%-10s constant in baseline, skipped" % (k, v))
            continue
        print("    %-16s v=%-10s mean=%-10.3f std=%-9.3f z=%+.2f" % (
            k, v, g.mean, g.std(), g.zscore(v)))

    # Which feature actually shifted between baseline and the attack phase.
    # A single field moving far outside its baseline spread explains a flag
    # burst; several moving together suggests a schema change instead.
    print()
    print("  per-feature shift (baseline mean/std vs attack mean, z of that mean):")
    print("    %-16s %10s %10s %10s %9s %9s" % (
        "feature", "base_mean", "base_std", "atk_mean", "z", "z>3%"))
    for k in fitted:
        if k not in m.gaussians:
            continue
        g = m.gaussians[k]
        bvals = [f[k] for f in learn if f.get(k) is not None]
        avals = [f[k] for f in monitor if f.get(k) is not None]
        if not bvals or not avals:
            continue
        amean = sum(avals) / len(avals)
        sd = g.std()
        if not sd:
            # A field held constant in the baseline has no spread to compare
            # against, so no z-score is meaningful. This is what tripped the
            # ZeroDivisionError on network_alerts.
            print("    %-16s %10.3f %10s %10.3f %9s %8.1f%%" % (
                k, g.mean, "0 (const)", amean, "-",
                100.0 * len(avals) / len(monitor)))
            continue
        z = (amean - g.mean) / sd
        # Reference only: share of attack samples with |z| > 3 for this field
        # alone. The model flags on an aggregate score, so this is a
        # per-feature indicator, not the model's own decision.
        hit = sum(1 for v in avals if abs(g.zscore(v)) > 3.0)
        print("    %-16s %10.3f %10.3f %10.3f %+9.2f %8.1f%%" % (
            k, g.mean, sd, amean, z, 100.0 * hit / len(monitor)))


if __name__ == "__main__":
    main()