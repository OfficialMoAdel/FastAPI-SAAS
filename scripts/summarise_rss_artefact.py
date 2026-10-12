import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "reports/rss_l10_loaded.json"
d = json.load(open(path))
mb = 1048576.0
print("=== WINDOW ===")
print("  target          :", d["target"])
print("  host            :", d["host_identity"], "recorded=", d["host_recorded"])
print("  window_seconds  :", d["window_seconds"], "meets_minimum=", d["window_meets_minimum"])
print("  samples         :", d["sample_count"], "dropped=", d["cpu_series_seconds"]["dropped"])
print("  interval        :", d["interval_seconds"], "s")
print()
print("=== RSS (concluding instrument) ===")
print("  first  : %.2f MB" % (d["rss_first_bytes"] / mb))
print("  last   : %.2f MB" % (d["rss_last_bytes"] / mb))
print("  growth : %+.3f MB" % (d["rss_growth_bytes"] / mb))
vals = [v["value"] for v in d["rss_series_bytes"]["samples"]]
n = len(vals)
k = n // 3
avg = lambda xs: sum(xs) / float(len(xs))
print(
    "  thirds : %.2f -> %.2f -> %.2f MB"
    % (avg(vals[:k]) / mb, avg(vals[k : 2 * k]) / mb, avg(vals[2 * k :]) / mb)
)
print("  drift  : %+.3f MB" % ((avg(vals[2 * k :]) - avg(vals[:k])) / mb))
print()
print("=== CPU cost ===")
c = d["cpu_summary_seconds"]
print(
    "  median %.1f us  max %.1f us  (ceiling %s us)"
    % (c["median"] * 1e6, c["max"] * 1e6, d["cost_ceiling_us"])
)
print()
print("=== WORKLOAD ===")
print(" ", d["workload"])
print()
print("=== TRACEMALLOC DIVERGENCE ===")
print(json.dumps(d["tracemalloc_divergence"], indent=2)[:1200])
