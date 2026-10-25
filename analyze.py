#!/usr/bin/env python3
"""Compare measured runs and write the performance report.

    python3 analyze.py results/baseline.json results/dynamic.json results/qos.json

Prints a per-class table for every run, Jain's fairness index (raw and weighted
by each class's offered rate), and the baseline-vs-QoS deltas. Writes
results/report.md, plus results/throughput.png when matplotlib is available.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_proto as P

ORDER = ["voice", "video", "bulk"]


def jain(values):
    """Jain's fairness index: 1.0 = perfectly equal, 1/n = fully monopolised."""
    values = [v for v in values if v is not None]
    if not values or all(v == 0 for v in values):
        return 0.0
    total = sum(values)
    return (total * total) / (len(values) * sum(v * v for v in values))


def load(path):
    with open(path) as fh:
        return json.load(fh)


def events_for(label, results_dir):
    path = os.path.join(results_dir, "events-%s.jsonl" % label)
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def run_table(report):
    rows = [("class", "Mbps", "loss %", "lat avg ms", "lat max ms", "jitter ms", "pkts")]
    for name in ORDER:
        st = report["classes"].get(name)
        if not st:
            continue
        rows.append((name,
                     "%.3f" % st["throughput_mbps"],
                     "%.2f" % st["loss_pct"],
                     "%.2f" % st["latency_ms"]["avg"],
                     "%.2f" % st["latency_ms"]["max"],
                     "%.2f" % st["jitter_ms"],
                     str(st["received"])))
    return rows


def render(rows):
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for n, row in enumerate(rows):
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return lines


def md_table(rows):
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "|".join("---" for _ in rows[0]) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out


def fairness(report):
    tputs, weighted = [], []
    for name in ORDER:
        st = report["classes"].get(name)
        if not st:
            continue
        tputs.append(st["throughput_mbps"])
        target = P.CLASSES[name]["rate_bps"] / 1e6
        weighted.append(min(st["throughput_mbps"] / target, 1.0))
    return jain(tputs), jain(weighted)


def plot(reports, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    fig, axes = plt.subplots(1, len(reports), figsize=(5 * len(reports), 3.6),
                             sharey=True, squeeze=False)
    for ax, rep in zip(axes[0], reports):
        for name in ORDER:
            st = rep["classes"].get(name)
            if not st:
                continue
            tl = st["timeline"]
            ax.plot([p["t"] for p in tl], [p["mbps"] for p in tl], label=name)
        ax.set_title(rep["label"])
        ax.set_xlabel("time (s)")
        ax.grid(alpha=0.3)
    axes[0][0].set_ylabel("throughput (Mbps)")
    axes[0][-1].legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", nargs="*", help="result JSON files")
    ap.add_argument("--out", default=None, help="markdown report path")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.join(here, "results")
    paths = args.results or sorted(
        p for p in glob.glob(os.path.join(results_dir, "*.json"))
        if not p.endswith("-tx.json") and os.path.basename(p) != "report.json")
    if not paths:
        sys.exit("no result files; run topology.py first")

    reports = [load(p) for p in paths]
    md = ["# QoS Traffic Management - Performance Evaluation", "",
          "Bottleneck: %.0f Mbps. Offered load: %s = %.2f Mbps."
          % (P.BOTTLENECK_BPS / 1e6,
             " + ".join("%s %.2f" % (n, P.CLASSES[n]["rate_bps"] / 1e6) for n in ORDER),
             sum(P.CLASSES[n]["rate_bps"] for n in ORDER) / 1e6), ""]

    for rep in reports:
        rows = run_table(rep)
        raw, weighted = fairness(rep)
        print("\n=== %s ===" % rep["label"])
        print("\n".join(render(rows)))
        print("Jain fairness: raw=%.4f  weighted-by-demand=%.4f" % (raw, weighted))
        evs = [e for e in events_for(rep["label"], results_dir)
               if e.get("kind") in ("congestion_detected", "congestion_cleared",
                                    "policy_change", "queue_remap")]
        if evs:
            print("Controller events:")
            for e in evs:
                print("  %s" % json.dumps(e))

        md += ["## Scenario: %s" % rep["label"], ""] + md_table(rows) + [
            "", "Jain fairness index: raw **%.4f**, weighted by offered demand "
                "**%.4f**." % (raw, weighted), ""]
        if evs:
            md += ["Controller events:", "", "```"] + \
                  [json.dumps(e) for e in evs] + ["```", ""]

    by_label = {r["label"]: r for r in reports}
    if "baseline" in by_label:
        base = by_label["baseline"]
        for label, rep in by_label.items():
            if label == "baseline":
                continue
            rows = [("class", "Mbps base -> %s" % label, "loss %% base -> %s" % label,
                     "latency ms base -> %s" % label)]
            for name in ORDER:
                b = base["classes"].get(name)
                c = rep["classes"].get(name)
                if not b or not c:
                    continue
                rows.append((
                    name,
                    "%.3f -> %.3f (%+.1f%%)" % (
                        b["throughput_mbps"], c["throughput_mbps"],
                        100.0 * (c["throughput_mbps"] - b["throughput_mbps"])
                        / b["throughput_mbps"] if b["throughput_mbps"] else 0.0),
                    "%.2f -> %.2f" % (b["loss_pct"], c["loss_pct"]),
                    "%.2f -> %.2f" % (b["latency_ms"]["avg"], c["latency_ms"]["avg"]),
                ))
            print("\n=== baseline vs %s ===" % label)
            print("\n".join(render(rows)))
            md += ["## Baseline vs %s" % label, ""] + md_table(rows) + [""]

    png = plot(reports, os.path.join(results_dir, "throughput.png"))
    if png:
        md += ["## Throughput over time", "", "![throughput](throughput.png)", ""]
        print("\nwrote %s" % png)

    out = args.out or os.path.join(results_dir, "report.md")
    with open(out, "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
