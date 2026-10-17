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
def avg(xs: list[float]) -> float:
    return sum(xs) / float(len(xs))


print(
    f"  thirds : {avg(vals[:k]) / mb:.2f} -> {avg(vals[k : 2 * k]) / mb:.2f} -> {avg(vals[2 * k :]) / mb:.2f} MB"
)
print(f"  drift  : {(avg(vals[2 * k :]) - avg(vals[:k])) / mb:+.3f} MB")
print()
print("=== CPU cost ===")
c = d["cpu_summary_seconds"]
print(
    f"  median {c['median'] * 1e6:.1f} us  max {c['max'] * 1e6:.1f} us  (ceiling {d['cost_ceiling_us']} us)"
)
print()
print("=== WORKLOAD ===")
print(" ", d["workload"])
print()
print("=== TRACEMALLOC DIVERGENCE ===")
print(json.dumps(d["tracemalloc_divergence"], indent=2)[:1200])
