# Retained-growth verdict (AMD A-8 / 6fqt)

Computed fresh from `perf-results/RESULT-20261012-vmlocal/rss_final.json`.
This is not inherited from G13, G14, or any earlier conclusion — those were
reached on a different platform, window and workload.

## NO RETAINED GROWTH. The series rises through the first third and then FALLS, so net drift across the window is negative.

- window **40.01 min** (2400.9s), 2401 samples, 0 dropped
- target **uvicorn** pid 15166 (`is_self: False`) — the app process, server-side
- thirds **199.895 → 200.44 → 198.75 MB**
- drift **-1.145 MB** · end-to-end **+0.1523 MB** (+159744 bytes)
- workload 8281 requests @ 8 concurrency

## Why the endpoints alone would mislead

End to end this run grew +159744 bytes (+0.1523 MB), which read alone looks like a small positive leak. The thirds show the rise is bounded and confined to the first third (199.90 -> 200.44 -> 198.75 MB), with net drift -1.145 MB. Reporting the endpoint comparison alone would have called this a candidate leak.

## tracemalloc, attribution only

Role: `attribution-only`. Accounted 52077 bytes, 107667.0 bytes unattributed.

tracemalloc ATTRIBUTES and never concludes. Of the modest end-to-end change it accounts for 52077 bytes, leaving the remainder unattributed - consistent with allocator arena behaviour rather than Python heap growth. The verdict above is drawn from the RSS series alone.

## Caveat

This window ran BEFORE the per-user client-IP fix, so 18.2% of its requests were rejected (1,508 of 8,281). That does not weaken the verdict - a series flat under partly-rejected load cannot hide growth - but the traffic figure is not clean sustained load and is carried in the artefact rather than smoothed.
