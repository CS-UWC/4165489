"""
Compare original frequency metric (F, rewards high rate when scaled) with a
rate-aware symmetric variant on archived CSV runs.

Rate-aware F = clamp(1 - |rate/expected - 1|), expected=0.5 Hz.
BTI_rate_aware_raw = 0.25*F_rate_aware + 0.30*T + 0.25*D + 0.20*P
(using per-row T,D,P from CSV; smoothed BTI is not recomputed).

Usage:
  python analyze_rate_aware_f.py data/extract/run/oct22
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
from collections import defaultdict, deque
from statistics import mean

WINDOW = 100
EXPECTED_HZ = 0.5


def clamp01(x):
    try:
        x = float(x)
    except Exception:
        return 0.5
    if math.isnan(x) or math.isinf(x):
        return 0.5
    return max(0.0, min(1.0, x))


def parse_ts(v):
    try:
        return float(v)
    except Exception:
        return None


def f_rate_aware(times, expected_hz=EXPECTED_HZ):
    if len(times) < 2:
        return 0.5
    duration = times[-1] - times[0]
    if duration <= 0:
        return 0.5
    rate = len(times) / duration
    if expected_hz <= 0:
        return 0.5
    return clamp01(1.0 - abs((rate / expected_hz) - 1.0))


def f_original(times, expected_hz=EXPECTED_HZ):
    if len(times) < 2:
        return 0.5
    duration = times[-1] - times[0]
    if duration <= 0:
        return 0.5
    rate = len(times) / duration
    return clamp01(rate / expected_hz * 0.7)


def getf(row, *keys, default=None):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except Exception:
                pass
    return default


def analyze_file(path):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8", errors="ignore")))
    if not rows:
        return None
    rows.sort(key=lambda r: parse_ts(r.get("timestamp")) or 0.0)
    base = os.path.basename(path)
    parts = base.replace(".csv", "").split("_")
    node = next((p for p in parts if p.startswith("node")), rows[0].get("node", "unknown"))
    attack = parts[2] if len(parts) > 2 else rows[0].get("phase", "unknown")
    hist = deque(maxlen=WINDOW)
    stats = defaultdict(lambda: {"n": 0, "F": [], "Fra": [], "BTI": [], "BTIra": []})
    for r in rows:
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        hist.append(ts)
        F = getf(r, "F", default=f_original(hist))
        Fra = f_rate_aware(hist)
        T = getf(r, "T", default=0.5)
        D = getf(r, "D", default=0.5)
        P = getf(r, "P", default=0.5)
        bti = getf(r, "bti", default=0.25 * F + 0.30 * T + 0.25 * D + 0.20 * P)
        btira = 0.25 * Fra + 0.30 * T + 0.25 * D + 0.20 * P
        ph = r.get("phase", "unknown")
        key = "attack" if ph != "baseline" else "baseline"
        s = stats[key]
        s["n"] += 1
        s["F"].append(F); s["Fra"].append(Fra); s["BTI"].append(bti); s["BTIra"].append(btira)

    def avg(a):
        return mean(a) if a else None

    b = stats["baseline"]; a = stats["attack"]
    return {
        "file": base, "node": node, "attack": attack,
        "base_n": b["n"], "atk_n": a["n"],
        "base_F": avg(b["F"]), "atk_F": avg(a["F"]),
        "base_Fra": avg(b["Fra"]), "atk_Fra": avg(a["Fra"]),
        "base_BTI": avg(b["BTI"]), "atk_BTI": avg(a["BTI"]),
        "base_BTIra": avg(b["BTIra"]), "atk_BTIra": avg(a["BTIra"]),
        "drop_BTI": (100 * (avg(b["BTI"]) - avg(a["BTI"])) / avg(b["BTI"])) if avg(b["BTI"]) else None,
        "drop_BTIra": (100 * (avg(b["BTIra"]) - avg(a["BTIra"])) / avg(b["BTIra"])) if avg(b["BTIra"]) else None,
    }


def fmt(x, pct=False):
    if x is None:
        return "--"
    return f"{x:+.1f}%" if pct else f"{x:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="data/extract/run/oct22", help="CSV file, directory, or glob root")
    ap.add_argument("--out", default="rate_aware_f_analysis_oct22")
    args = ap.parse_args()

    if os.path.isfile(args.path):
        files = [args.path]
    else:
        files = sorted(glob.glob(os.path.join(args.path, "**", "run_*.csv"), recursive=True))
        if not files:
            files = sorted(glob.glob(os.path.join(args.path, "run_*.csv")))
    results = [r for r in (analyze_file(f) for f in files) if r]
    if not results:
        print("No CSV runs found.")
        return

    # Per-run table
    lines = ["# Rate-aware F analysis (oct22)", "", "Rate-aware F = clamp(1 - |rate/expected - 1|), expected=0.5 Hz.", "BTI_rate_aware = 0.25*F_ra + 0.30*T + 0.25*D + 0.20*P (raw; uses CSV T,D,P).", "", "## Per run", "", "| node | attack | file | base F | atk F | base F_ra | atk F_ra | base BTI | atk BTI | BTI drop | base BTI_ra | atk BTI_ra | BTI_ra drop |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['node']} | {r['attack']} | `{r['file']}` | {fmt(r['base_F'])} | {fmt(r['atk_F'])} | {fmt(r['base_Fra'])} | {fmt(r['atk_Fra'])} | {fmt(r['base_BTI'])} | {fmt(r['atk_BTI'])} | {fmt(r['drop_BTI'],1)} | {fmt(r['base_BTIra'])} | {fmt(r['atk_BTIra'])} | {fmt(r['drop_BTIra'],1)} |")

    # Aggregate by (attack,node)
    groups = defaultdict(list)
    for r in results:
        groups[(r["attack"], r["node"])].append(r)
    lines += ["", "## Aggregate (mean across runs)", "", "| attack | node | runs | mean atk F | mean atk F_ra | mean BTI drop | mean BTI_ra drop |", "|---|---|---:|---:|---:|---:|---:|"]
    for (attack, node), rs in sorted(groups.items()):
        def m(k):
            vals = [x[k] for x in rs if x.get(k) is not None]
            return mean(vals) if vals else None
        lines.append(f"| {attack} | {node} | {len(rs)} | {fmt(m('atk_F'))} | {fmt(m('atk_Fra'))} | {fmt(m('drop_BTI'),1)} | {fmt(m('drop_BTIra'),1)} |")

    report = "\n".join(lines) + "\n"
    print(report)
    md_path = args.out + ".md"
    csv_path = args.out + ".csv"
    open(md_path, "w", encoding="utf-8").write(report)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"Wrote {md_path} and {csv_path}")


if __name__ == "__main__":
    main()
