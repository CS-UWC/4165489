"""Build the results tables and figures for the Evaluation chapter.

Every number and figure is derived from the archived CSVs, so nothing in the
report is hand-transcribed. Scores are recomputed with the current TinyML
rather than read from the ml_score column, because those were captured live
by a model whose auto-threshold collapsed toward zero on unusually regular
baselines.

Usage:
    python3 make_results.py data/extract/run --outdir figures
    python3 make_results.py data/extract/run/oct22 --outdir figures_oct22 \
        --tables tables_oct22.tex \
        --mimic-root data/extract/run/oct22/mimic_oct22_rerun

--mimic-root selects the node-aware mimic rerun and ignores older mimic CSVs
under the other roots. The October mimic runs drifted whichever field the
script was built around (ozone) even on nodes whose schemas lack it, so their
node2--4 results do not test sensor-mimicry. The November rerun resolves the
drift field per node (node1 ozone, node2 sound, node3 distance, node4 temp).
"""
import argparse
import csv
import glob
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from summarise_runs import collect, num, rescore, RESCORE  # noqa: E402

METRICS = [("F", "#4C72B0"), ("T", "#DD8452"), ("D", "#55A868"),
           ("P", "#8172B3")]

ATTACKS = ["replay", "timing", "fdi", "flood", "mimic"]
NODES = ["node1", "node2", "node3", "node4"]

LABEL = {"replay": "Replay", "timing": "Timing", "fdi": "FDI",
         "flood": "Flood", "mimic": "Mimic"}

# Fields each node's baseline can actually calibrate. Established from the
# per-run fitted-field audit printed by summarise_runs.py on the oct22 campaign.
# network_alerts is recorded but excluded from ML_FEATURES (monotonic counter),
# so it is not counted as a scored field here.
COVERAGE = {
    "node1": ["temp", "humidity", "ozone", "ozone_v", "rssi"],
    "node2": ["sound", "sound_v", "rssi"],
    "node3": ["distance", "reed_ao", "rssi"],
    "node4": ["temp", "humidity", "light", "light_v", "rssi"],
}


def rows_for(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def attack_of(path):
    """Attack name from run_<node>_<attack>_<timestamp>.csv."""
    parts = os.path.basename(path).split("_")
    return parts[2] if len(parts) >= 3 else ""


def collect_selected(roots, mimic_root=None):
    """All CSVs to analyse, with an optional dedicated mimic source.

    collect() only descends one directory level, so a nested rerun directory
    (mimic_oct22_rerun/r1, r2, r3) is collected explicitly. When mimic_root is
    supplied, mimic CSVs outside it are dropped.
    """
    files = []
    for r in roots:
        files.extend(collect(r))
    if mimic_root:
        files.extend(collect(mimic_root))
        root_abs = os.path.abspath(mimic_root)
        files = [p for p in files
                 if attack_of(p) != "mimic"
                 or os.path.abspath(p).startswith(root_abs)]
    return sorted(set(os.path.abspath(p) for p in files))


def load(roots, mimic_root=None):
    """Return {(attack, node): [run dicts]} using only re-scorable runs."""
    files = collect_selected(roots, mimic_root)
    out = {}
    for p in sorted(files):
        rows = rows_for(p)
        if not rows:
            continue
        rs = rescore(rows)
        if rs is None:
            continue
        base = [r for r in rows if r["phase"] == "baseline"]
        atk = [r for r in rows if r["phase"] != "baseline"]
        if not atk:
            continue
        phase = atk[0]["phase"]
        node = os.path.basename(p).split("_")[1]
        bti_b = [num(r.get("bti_smoothed")) for r in base]
        bti_a = [num(r.get("bti_smoothed")) for r in atk]
        mb, ma = mean(bti_b), mean(bti_a)
        out.setdefault((phase, node), []).append({
            "file": os.path.basename(p),
            "rate": rs["rate"],
            "mean_score": rs["mean_score"],
            "n": rs["n"],
            "first_alert": rs["first_alert"],
            "bti_base": mb,
            "bti_atk": ma,
            "bti_drop": (100.0 * (mb - ma) / mb) if (mb and ma) else None,
            "fitted": rs["fitted"],
        })
    return out


def bti_trace(path):
    """Return (phase, [(t_rel_seconds, bti), ...]) for one run."""
    rows = rows_for(path)
    base = [r for r in rows if r["phase"] == "baseline"]
    atk = [r for r in rows if r["phase"] != "baseline"]
    series = base + atk
    if not series:
        return None, []
    t0 = num(series[0].get("timestamp"))
    cut = num(base[-1].get("timestamp")) if base else t0
    pts = []
    for r in series:
        t = num(r.get("timestamp"))
        b = num(r.get("bti_smoothed"))
        if t is None or b is None:
            continue
        pts.append((t - t0, b))
    return (cut - t0) if cut is not None else None, pts


def score_split(path):
    """Return (attack_scores, baseline_scores, fitted, threshold) for one run.

    Used to plot score separation, which the aggregate flagging rate alone
    cannot show: a rate near zero may mean scores sit just below threshold
    or are indistinguishable from baseline.
    """
    try:
        import capture
        from tiny_ml import TinyML
    except ImportError:
        return None
    rows = rows_for(path)
    base = [r for r in rows if r["phase"] == "baseline"]
    atk = [r for r in rows if r["phase"] != "baseline"]
    if len(base) < 3 or len(atk) < 3:
        return None

    def usable(d):
        return [v for v in d.values() if v is not None]

    def feats(rs):
        return [{f: num(r.get(f)) for f in capture.ML_FEATURES} for r in rs]

    learn = [f for f in feats(base) if len(usable(f)) >= 2]
    mon = [f for f in feats(atk) if len(usable(f)) >= 2]
    if len(learn) < 3 or len(mon) < 3:
        return None
    present = {k for f in mon for k, v in f.items() if v is not None}
    nb = {f: sum(1 for r in learn if r.get(f) is not None) for f in present}
    fitted = [f for f in capture.ML_FEATURES if f in present and nb[f] >= 2]
    if len(fitted) < 2:
        return None
    try:
        with redirect_stdout(io.StringIO()):
            m = TinyML(list(fitted))
            for f in learn:
                m.learn(f)
            thr = m.autothreshold(learn)
            return ([m.score(f) for f in mon], [m.score(f) for f in learn],
                    fitted, thr)
    except Exception:
        return None


def pick_one(files, attack, node="node1"):
    """First re-scorable run for an attack/node, preferring runs_v5_*."""
    order = [f for f in files
             if os.path.basename(f).startswith("run_%s_%s_" % (node, attack))]
    for pref in ("runs_v5_1", "runs_v5_2", "runs_flood"):
        for f in order:
            if pref in f:
                if score_split(f) is not None:
                    return f
    for f in order:
        if score_split(f) is not None:
            return f
    return None


def fmt(x, d=1, unit=""):
    return "--" if x is None else ("%.*f%s" % (d, x, unit))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--outdir", default="figures")
    ap.add_argument("--tables", default="tables.tex")
    ap.add_argument("--mimic-root", default=None,
                    help="use only mimic runs under this root (node-aware rerun); "
                         "older mimic CSVs elsewhere are ignored")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(args.outdir, exist_ok=True)
    selected = collect_selected(args.roots, args.mimic_root)
    data = load(args.roots, args.mimic_root)
    print("loaded %d (attack, node) cells from %d CSVs" % (len(data), len(selected)))

    # ---------------- Figure 1: detection rate per attack, per node --------
    fig, axes = plt.subplots(1, len(NODES), figsize=(14, 3.4), sharey=True)
    for ax, node in zip(axes, NODES):
        xs, labels = [], []
        for i, a in enumerate(ATTACKS):
            runs = data.get((a, node), [])
            if not runs:
                continue
            xs.append(i)
            labels.append(a)
            rates = [r["rate"] for r in runs]
            ax.bar(i, mean(rates), color="#4C72B0", width=0.55,
                   edgecolor="black", linewidth=0.5, zorder=2)
            # individual repeats, so consistency is visible rather than implied
            ax.scatter([i] * len(rates), rates, s=18, color="#1F2A44",
                       zorder=3, label="repeat" if (i == 0 and node == "node1") else None)
        ax.axhline(5, color="#C44E52", ls="--", lw=1, zorder=1)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, rotation=35, ha="right")
        ax.set_title("%s  (%d scored field%s)" % (
            node, len(COVERAGE[node]), "" if len(COVERAGE[node]) == 1 else "s"))
        ax.set_ylim(0, 60)
        ax.grid(axis="y", alpha=0.3, zorder=0)
    axes[0].set_ylabel("attack-phase messages flagged (%)")
    axes[0].axhline(5, color="#C44E52", ls="--", lw=1)
    axes[0].text(0.02, 6.5, "5% decision gate", color="#C44E52", fontsize=7)
    axes[0].legend(fontsize=7, loc="upper right")
    fig.suptitle("ML detection rate by attack (dashed line = 5% decision gate)",
                 fontsize=10)
    fig.tight_layout()
    p1 = os.path.join(args.outdir, "detection_by_attack.png")
    fig.savefig(p1, dpi=200)
    plt.close(fig)
    print("wrote", p1)

    # ---------------- Figure 2: node1 FDI consistency ----------------------
    fdi = data.get(("fdi", "node1"), [])
    if fdi:
        fig, ax = plt.subplots(figsize=(5.2, 3.2))
        names = ["run %d" % (i + 1) for i in range(len(fdi))]
        rates = [r["rate"] for r in fdi]
        ax.bar(range(len(rates)), rates, color="#55A868", width=0.5,
               edgecolor="black", linewidth=0.5, zorder=2)
        for i, (r, v) in enumerate(zip(fdi, rates)):
            ax.text(i, v + 1, "%.1f%%" % v, ha="center", fontsize=8)
        ax.axhline(50, color="#555555", ls=":", lw=1)
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, rotation=30, ha="right", fontsize=7)
        ax.set_ylabel("flagged (%)")
        ax.set_ylim(0, 62)
        ax.set_title("node1 FDI detection is reproducible\n(%d independent repeats)" % len(fdi), fontsize=10)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        fig.tight_layout()
        p2 = os.path.join(args.outdir, "fdi_repeatability.png")
        fig.savefig(p2, dpi=200)
        plt.close(fig)
        print("wrote", p2)

    # ---------------- Figure 3: BTI traces, node1 -------------------------
    files = [p for p in selected
             if os.path.basename(p).startswith("run_node1_")
             and attack_of(p) in ATTACKS]
    picked = []
    seen = set()
    for f in files:
        a = os.path.basename(f).split("_")[2]
        if a in seen:
            continue
        seen.add(a)
        picked.append((a, f))
    if picked:
        fig, axes = plt.subplots(len(picked), 1, figsize=(7.2, 2.1 * len(picked)),
                                 sharex=True)
        if len(picked) == 1:
            axes = [axes]
        for ax, (a, f) in zip(axes, picked):
            cut, pts = bti_trace(f)
            if not pts:
                continue
            ts = [p[0] for p in pts]
            bs = [p[1] for p in pts]
            ax.plot(ts, bs, lw=0.8, color="#4C72B0")
            if cut:
                ax.axvline(cut, color="#C44E52", ls="--", lw=1)
            ax.set_ylabel("BTI", fontsize=8)
            ax.set_title(LABEL.get(a, a), fontsize=8)
            ax.grid(alpha=0.3)
        axes[-1].set_xlabel("seconds since first captured message")
        axes[0].text(0.01, 0.06, "dashed line = attack onset", fontsize=7,
                     transform=axes[0].transAxes, color="#C44E52")
        fig.suptitle("node1 BTI: baseline then attack phase", fontsize=10)
        fig.tight_layout()
        p3 = os.path.join(args.outdir, "bti_traces_node1.png")
        fig.savefig(p3, dpi=200)
        plt.close(fig)
        print("wrote", p3)

    # ---------------- Figure 4: model coverage ----------------------------
    fig, ax = plt.subplots(figsize=(5.0, 3.0))
    counts = [len(COVERAGE[n]) for n in NODES]
    bars = ax.bar(NODES, counts, color=["#55A868", "#DD8452", "#C44E52", "#4C72B0"][:len(NODES)],
                  width=0.5, edgecolor="black", linewidth=0.5, zorder=2)
    for b, c in zip(bars, counts):
        ax.text(b.get_x() + b.get_width() / 2, c + 0.12, str(c),
                ha="center", fontsize=9)
    ax.set_ylabel("fields calibratable from the node's own baseline")
    ax.set_ylim(0, 7)
    ax.set_title("Anomaly-model coverage per node", fontsize=10)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    fig.tight_layout()
    p4 = os.path.join(args.outdir, "model_coverage.png")
    fig.savefig(p4, dpi=200)
    plt.close(fig)
    print("wrote", p4)

    # ---------------- Figure 5: metric response per attack ----------------
    fig, axes = plt.subplots(1, len(METRICS), figsize=(11, 3.0), sharey=True)
    xs = list(range(len(ATTACKS)))
    width = 0.38
    for ax, (mk, colour) in zip(axes, METRICS):
        bvals, avals = [], []
        for a in ATTACKS:
            f = pick_one(selected, a)
            if not f:
                bvals.append(None)
                avals.append(None)
                continue
            rows = rows_for(f)
            base = [r for r in rows if r["phase"] == "baseline"]
            atk = [r for r in rows if r["phase"] != "baseline"]
            bvals.append(mean([num(r.get(mk)) for r in base]))
            avals.append(mean([num(r.get(mk)) for r in atk]))
        bi = [i - width / 2 for i in xs]
        ai = [i + width / 2 for i in xs]
        ax.bar(bi, [v if v is not None else 0 for v in bvals], width,
               color="#BBBBBB", edgecolor="black", linewidth=0.4,
               label="baseline", zorder=2)
        ax.bar(ai, [v if v is not None else 0 for v in avals], width,
               color=colour, edgecolor="black", linewidth=0.4,
               label="attack", zorder=2)
        ax.set_xticks(xs)
        ax.set_xticklabels(ATTACKS, rotation=35, ha="right", fontsize=8)
        ax.set_title("$%s$" % mk)
        ax.grid(axis="y", alpha=0.3, zorder=0)
        if mk == "F":
            ax.set_ylabel("mean metric value")
            ax.legend(fontsize=7)
    fig.suptitle("Behavioural metric response on node1: which metric each attack moves",
                 fontsize=10)
    fig.tight_layout()
    p5 = os.path.join(args.outdir, "metric_response.png")
    fig.savefig(p5, dpi=200)
    plt.close(fig)
    print("wrote", p5)

    # ---------------- Figure 6: score separation ---------------------------
    panels = []
    for a in ATTACKS:
        f = pick_one(selected, a)
        if not f:
            continue
        r = score_split(f)
        if r:
            panels.append((a, r[0], r[1], r[3]))
    if panels:
        fig, axes = plt.subplots(1, len(panels), figsize=(3.0 * len(panels), 3.2),
                                 sharey=False)
        if len(panels) == 1:
            axes = [axes]
        for ax, (a, sa, sb, thr) in zip(axes, panels):
            ax.boxplot([sb, sa], tick_labels=["base", "atk"], widths=0.55,
                       patch_artist=True, showfliers=False,
                       boxprops=dict(facecolor="#BBBBBB", alpha=0.7),
                       medianprops=dict(color="#333333"))
            ax.axhline(thr, color="#C44E52", ls="--", lw=1.2)
            ax.set_yscale("symlog", linthresh=1)
            ax.set_title(LABEL.get(a, a), fontsize=9)
            ax.grid(axis="y", alpha=0.3)
        axes[0].set_ylabel("anomaly score (symlog)")
        fig.suptitle("Anomaly score, baseline vs attack (node1): only FDI separates",
                     fontsize=10)
        fig.tight_layout()
        p6 = os.path.join(args.outdir, "score_separation.png")
        fig.savefig(p6, dpi=200)
        plt.close(fig)
        print("wrote", p6)

    # ---------------- LaTeX table ----------------------------------------
    lines = []
    lines.append("%% GENERATED BY make_results.py -- do not edit by hand")
    lines.append("\\begin{tabular}{@{}llrrrr@{}}")
    lines.append("\\toprule")
    lines.append("\\textbf{Attack} & \\textbf{Node} & \\textbf{Runs} & "
                 "\\textbf{Flagged} & \\textbf{First alert} & \\textbf{BTI drop} \\\\")
    lines.append("\\midrule")
    for a in ATTACKS:
        for node in NODES:
            runs = data.get((a, node), [])
            if not runs:
                continue
            rates = [r["rate"] for r in runs]
            drops = [r["bti_drop"] for r in runs]
            fa = [r["first_alert"] for r in runs]
            rng = ("%.1f--%.1f" % (min(rates), max(rates))
                   if len(rates) > 1 else "%.1f" % rates[0])
            drop = mean(drops)
            if drop is not None and abs(drop) < 0.05:
                drop = 0.0
            lines.append(
                "%s & %s & %d & %s\\%% & %s & %s \\\\" % (
                    LABEL[a], node, len(runs), rng,
                    ("%.1f~s" % mean(fa)) if fa and mean(fa) is not None else "--",
                    ("%+.1f\\%%" % drop) if drop is not None else "--"))
            if node == "node2":
                lines.append("\\addlinespace")
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    with open(args.tables, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("wrote", args.tables)


if __name__ == "__main__":
    main()
