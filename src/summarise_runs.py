"""Aggregate iteration-4 attack CSVs into the tables the paper needs.

Usage:
    python3 summarise_runs.py runs_v6

Reads every run_<node>_<phase>_*.csv under <root>/iter4_r*/ and reports,
per repeat and per attack:
  - flagged rate during the attack phase
  - mean anomaly score
  - mean BTI in baseline vs attack phase, and the drop

Also prints a detection-rate matrix and flags two consistency problems that
were visible in the console log but need checking against the CSVs:
  1. identical baseline figures for the same attack across repeats
     (possible console transcription error)
  2. node3 mimic disagreeing between runs
"""
import csv
import glob
import io
import os
import statistics as st
import sys
from collections import defaultdict
from contextlib import redirect_stdout

# Re-score every run against the current TinyML instead of trusting the
# ml_score/ml_anomaly columns captured live. The stored values came from a
# model whose autothreshold collapsed toward zero whenever a baseline was
# unusually regular, which alone moved node1 mimic from 1.8% to 88.8% and
# node2 fdi from 0.8% to 20.0% on identical firmware. Pass --stored to see
# the original live values for comparison.
RESCORE = True

# Drop runs that cannot be re-scored instead of reporting their pre-fix live
# scores. These come from firmware that published truncated JSON, so their
# numbers were produced by a model that no longer exists and are not
# comparable to anything re-scored.
SKIP_STALE = False

# Fields excluded from the fitted model, for measuring how much an unstable
# feature drives the result. Kept in the CSV regardless.
DROP = set()


def rows_for(d):
    return {k: v for k, v in d.items() if v is not None}


def rescore(rows):
    """Recompute scores/threshold from raw feature columns.

    Mirrors replay_check.py: fit only the fields this node actually reports,
    calibrate on the baseline phase, then score the attack phase. Silently
    returns None if the file cannot support a model so the caller can fall
    back to the stored values.
    """
    try:
        import capture
        from tiny_ml import TinyML
    except ImportError:
        return None

    base = [r for r in rows if r["phase"] == "baseline"]
    atk = [r for r in rows if r["phase"] != "baseline"]

    def feats(rs):
        return [{f: num(r.get(f)) for f in capture.ML_FEATURES} for r in rs]

    learn = [f for f in feats(base) if len(rows_for(f)) >= 2]
    monitor = [f for f in feats(atk) if len(rows_for(f)) >= 2]
    if len(learn) < 3 or len(monitor) < 3:
        return None

    present = set()
    for f in monitor:
        present |= set(rows_for(f))
    # A field is only usable if the BASELINE can calibrate it. Fitting a
    # Gaussian with no baseline samples leaves std() as None, zscore() as 0.0
    # and the field silently inert - which is how node2 came to report
    # detection rates from a model that never saw temp/humidity/ozone, the
    # very fields its FDI attack falsifies.
    n_base = {}
    for f in present:
        n_base[f] = sum(1 for r in learn if r.get(f) is not None)
    fitted = [f for f in capture.ML_FEATURES
              if f in present and f not in DROP and n_base[f] >= 2]
    uncal = [f for f in capture.ML_FEATURES
             if f in present and f not in DROP and n_base[f] < 2]
    if len(fitted) < 2:
        return None

    # autothreshold warns on stderr/stdout; keep the tables clean.
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            m = TinyML(list(fitted))
            for f in learn:
                m.learn(f)
            thr = m.autothreshold(learn)
            scores = [m.score(f) for f in monitor]
    except Exception:
        return None

    flagged = sum(1 for s in scores if s > thr)
    first_alert = None
    for i, s in enumerate(scores):
        if s > thr:
            t0 = num(atk[0].get("timestamp"))
            ti = num(atk[i].get("timestamp"))
            if t0 is not None and ti is not None:
                first_alert = ti - t0
            break
    return {
        "rate": 100.0 * flagged / len(scores),
        "flagged": flagged,
        "n": len(scores),
        "mean_score": sum(scores) / len(scores),
        "threshold": thr,
        "source": m.threshold_source,
        "fitted": list(fitted),
        "uncal": list(uncal),
        "first_alert": first_alert,
    }


def num(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def summarise(path):
    rows = read_csv(path)
    node = os.path.basename(path).split("_")[1]
    base = [r for r in rows if r["phase"] == "baseline"]
    atk = [r for r in rows if r["phase"] not in ("baseline",)]
    out = {
        "file": os.path.basename(path),
        "node": node,
        "phase": atk[0]["phase"] if atk else "none",
        "n_base": len(base),
        "n_atk": len(atk),
        "bti_base": st.mean([num(r["bti"]) for r in base if num(r["bti"]) is not None])
        if any(num(r["bti"]) is not None for r in base) else None,
        "bti_atk": st.mean([num(r["bti"]) for r in atk if num(r["bti"]) is not None])
        if any(num(r["bti"]) is not None for r in atk) else None,
    }
    sc = [num(r["ml_score"]) for r in atk if num(r["ml_score"]) is not None]
    fl = [r for r in atk if r.get("ml_anomaly") == "1"]
    out["mean_score"] = st.mean(sc) if sc else None
    out["flagged"] = len(fl)
    out["rate"] = 100.0 * len(fl) / len(atk) if atk else 0.0
    out["source"] = "stored"
    rs = rescore(rows) if RESCORE else None
    if rs:
        out["rate"] = rs["rate"]
        out["flagged"] = rs["flagged"]
        out["mean_score"] = rs["mean_score"]
        out["n_atk"] = rs["n"]
        out["threshold"] = rs["threshold"]
        out["first_alert"] = rs.get("first_alert")
        out["source"] = "rescored(%s)" % rs["source"]
        out["fitted"] = rs.get("fitted")
        out["uncal"] = rs.get("uncal")
        if rs.get("uncal"):
            out["source"] += "!uncalibrated:%s" % ",".join(rs["uncal"])
    for m in "FTDP":
        vals = [num(r[m]) for r in atk if num(r[m]) is not None]
        out[m] = st.mean(vals) if vals else None
    return out


def drop_pct(b, a):
    if b is None or a is None or b == 0:
        return None
    return 100.0 * (b - a) / b


def collect(root):
    """CSVs either directly inside root or one level below a repeat dir.

    Campaign layouts differ: runs_v6/iter4_r1/*.csv nests under a repeat
    directory, while runs_flood_v1_1/*.csv sits flat. Accept both so the same
    script summarises either shape.
    """
    found = sorted(glob.glob(os.path.join(root, "*.csv")))
    for entry in sorted(glob.glob(os.path.join(root, "*"))):
        if os.path.isdir(entry):
            found.extend(sorted(glob.glob(os.path.join(entry, "*.csv"))))
    return found


def main():
    global RESCORE, DROP
    argv = sys.argv[1:]
    if "--stored" in argv:
        RESCORE = False
    if "--skip-stale" in argv:
        SKIP_STALE = True
    roots = []
    i = 0
    while i < len(argv):
        if argv[i] == "--drop":
            DROP.add(argv[i + 1])
            i += 2
            continue
        if not argv[i].startswith("--"):
            roots.append(argv[i])
        i += 1
    roots = roots or ["runs_v6"]
    files = []
    for root in roots:
        files.extend(collect(root))
    if not files:
        print("no CSVs under", " ".join(roots))
        return

    per_repeat = defaultdict(list)
    for p in files:
        rep = os.path.basename(os.path.dirname(p)).replace("iter4_", "")
        r = summarise(p)
        if SKIP_STALE and r.get("source") == "stored":
            print("skipping (not re-scorable): %s/%s" % (rep, r["file"]))
            continue
        per_repeat[rep].append(r)

    allrows = [r for rows in per_repeat.values() for r in rows]
    stale = [r for r in allrows if r.get("source") == "stored"]
    if stale and RESCORE:
        print("!" * 78)
        print("WARNING: %d/%d files could not be re-scored and still show" % (
            len(stale), len(allrows)))
        print("live-captured scores from the pre-fix model (marked * below):")
        for r in stale:
            print("   %-6s %-8s %-38s %5.1f%%" % (
                r["node"], r["phase"], r["file"], r["rate"]))
        print("These are NOT comparable to the re-scored cells. Exclude them.")
        print("!" * 78)
        print()

    for rep in sorted(per_repeat):
        rows = per_repeat[rep]
        print("=" * 78)
        print("REPEAT %s" % rep)
        print("=" * 78)
        print("%-6s %-8s %6s %6s %8s %9s %8s %8s %8s" % (
            "node", "phase", "n_base", "n_atk", "flagged", "rate%",
            "meanS", "BTIb", "drop%"))
        for r in sorted(rows, key=lambda x: (x["phase"], x["node"])):
            d = drop_pct(r["bti_base"], r["bti_atk"])
            mark = "*" if r.get("source") == "stored" else " "
            print("%-6s%-8s %6d %6d %8d %8.1f%%%s %8s %8s %8s" % (
                r["node"], r["phase"], r["n_base"], r["n_atk"],
                r["flagged"], r["rate"], mark,
                "%.2f" % r["mean_score"] if r["mean_score"] is not None else "-",
                "%.3f" % r["bti_base"] if r["bti_base"] is not None else "-",
                "%+.1f" % d if d is not None else "-"))

    print()
    print("=" * 78)
    print("DETECTION MATRIX  (flagged %% during attack phase)")
    print("=" * 78)
    print("scores: %s%s" % (
        "RESCORED with current TinyML" if RESCORE else "STORED live values (--stored)",
        "  [dropped: %s]" % ",".join(sorted(DROP)) if DROP else ""))
    attacks = ["replay", "timing", "fdi", "flood", "mimic"]
    reps = sorted(per_repeat)
    print("%-8s %-24s %s" % ("attack", "node", " -> ".join(reps)))
    idx = {}
    for rep in per_repeat:
        for r in per_repeat[rep]:
            idx[(rep, r["node"], r["phase"])] = r
    for a in attacks:
        for node in ("node1", "node2", "node3"):
            cells = []
            for rep in reps:
                r = idx.get((rep, node, a))
                cells.append("%.1f%%%s" % (
                    r["rate"], "*" if r.get("source") == "stored" else "")
                    if r else "MISSING")
            print("%-8s %-24s %s" % (a, node, "   ->   ".join(cells)))

    print()
    print("=" * 78)
    print("MODEL COVERAGE  (fields the anomaly model could actually score)")
    print("=" * 78)
    for rep in sorted(per_repeat):
        for r in sorted(per_repeat[rep], key=lambda x: (x["node"], x["phase"])):
            if not r.get("fitted"):
                continue
            uncal = r.get("uncal") or []
            note = ""
            if uncal:
                note = "   UNSCORED (no baseline data): %s" % ",".join(uncal)
            print("  %-16s %-6s %-8s %2d fields: %s%s" % (
                rep, r["node"], r["phase"], len(r["fitted"]),
                ",".join(r["fitted"]), note))

    print()

    print("=" * 78)
    print("CONSISTENCY CHECKS")
    print("=" * 78)

    # 1. identical baseline stats for same attack+node across repeats
    print("\n1. Identical baseline figures across repeats (possible")
    print("   console transcription error -- verify against CSVs):")
    for a in attacks:
        for node in ("node1", "node2", "node3"):
            seen = {}
            for rep in reps:
                r = idx.get((rep, node, a))
                if not r:
                    continue
                key = (round(r["bti_base"], 3), round(r["bti_atk"], 3))
                seen.setdefault(key, []).append(rep)
            for key, r_ in seen.items():
                if len(r_) > 1:
                    print("   %s/%s: identical BTI %s in repeats %s" % (
                        a, node, key, ",".join(r_)))

    # 2. node3 mimic spread
    print("\n2. node3 mimic across all attack CSVs:")
    for p in sorted(f for f in files if os.path.basename(f).startswith("run_node3_mimic_")):
        r = summarise(p)
        print("   %s" % os.path.basename(p))
        print("      flagged %d/%d (%.1f%%)  meanS=%s  thr=%s  F/T/D/P=%s/%s/%s/%s  [%s]" % (
            r["flagged"], r["n_atk"], r["rate"],
            "%.2f" % r["mean_score"] if r["mean_score"] is not None else "-",
            "%.3f" % r["threshold"] if r.get("threshold") is not None else "-",
            "%.3f" % r["F"] if r["F"] is not None else "-",
            "%.3f" % r["T"] if r["T"] is not None else "-",
            "%.3f" % r["D"] if r["D"] is not None else "-",
            "%.3f" % r["P"] if r["P"] is not None else "-",
            r.get("source", "stored")))


if __name__ == "__main__":
    main()