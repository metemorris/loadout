# Performance analysis

## Scope and method

This note analyzes the API read path using the bundled example catalog. It is
not a production capacity claim: the example is small, timings vary by machine,
and no concurrent or write-heavy workload was sampled.

Measurements were taken with Python 3.12 by copying the shared schema files and
example state files into a temporary runtime directory. Each operation was
timed with `time.perf_counter_ns`; the cold-snapshot profile was collected with
`cProfile`. The observed results were:

| Operation | Samples | Median | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Cold catalog snapshot | 20 | 1,084.69 ms | 1,338.84 ms | 1,403.76 ms |
| Warm catalog snapshot | 1,000 | 0.24 ms | 0.43 ms | 16.66 ms |
| Trip-detail payload from an existing snapshot | 1,000 | 0.84 ms | 1.94 ms | 15.58 ms |

After adding snapshot-scoped loader reuse, the same 20-sample cold benchmark
measured a 318.66 ms median, 377.13 ms p95, and 392.75 ms maximum. This is about
a 71% median reduction on this machine; it remains a diagnostic observation,
not a performance guarantee.

These figures are diagnostic observations, not regression thresholds. A second,
instrumented cold run took 2.77 seconds because profiler overhead is substantial
for this call graph.

## Slowest path

The slowest measured read path is a **cold catalog snapshot**. The API computes
a signature for six state files and `_load_catalog_snapshot` then constructs the
inventory, trip, packing-plan, and execution catalogs. On a cache hit, requests
avoid reconstruction and only perform the signature checks.

The cold profile attributes 2.69 of its profiler-inflated 2.77 seconds to 46
`yaml.safe_load` calls. Those calls are repeated because the four top-level
loaders independently load and validate overlapping documents; nested
cross-reference validation loads some catalogs again. `load_inventory` appeared
eight times, `load_trips` four times, and `load_packing_plans` twice in this one
snapshot. YAML scanning and parsing, rather than payload formatting, therefore
dominates cold-start and cache-invalidation latency for the example data.

The relevant complexity is proportional not merely to the six file sizes, but
to the repeated parse count and the number of records validated on each pass.
Any write that changes a signature invalidates the whole cached snapshot, so the
next read pays this cold cost. The snapshot builder now supplies a bounded load
context: nested validators reuse catalogs already constructed during that build.
The context is discarded when construction ends, so a later write cannot leave
stale objects in a process-wide loader cache.

## Are there enough metrics?

Not yet. The measurements above are sufficient to form a local hypothesis about
the example catalog. The API now exposes bounded, in-process aggregates for
request count/status/latency, concurrency, snapshot cache outcomes, and snapshot
duration at `/api/metrics`. It uses normalized route templates and does not
retain request content or physical identifiers.

Those metrics reset at process restart and do not yet include per-loader timing,
YAML parse counts, catalog cardinality, durable-write phases, host comparison, or
external HTTP and browser time. Test duration reporting describes the test suite,
not runtime behavior.

Consequently, there is not enough evidence to identify the slowest production
endpoint, quantify user impact, choose a latency objective, or decide whether
cold rebuilds occur often enough to justify optimization. In particular, these
measurements omit HTTP serialization, browser/network time, concurrent requests,
filesystem variance, personal catalog sizes, and durable write/reload paths.

To make a production judgment, retain the implemented request and snapshot
aggregates and add:

1. Per-loader duration, YAML document parse counts, and source byte/record counts
   for each rebuild.
2. Durable action duration and failure counts, separated into validation,
   serialization, atomic replacement, and post-write reload phases.
3. Process and application-version context for comparing runs without exposing
   inventory data.
4. Export to a durable metrics backend if observations must survive process
   restarts.

Report median, p95, and p99 over representative interactive sessions and preserve
the distinction between reads, confirmed writes, and the execution ledger. Do
not attach possession names, notes, physical IDs, trip IDs, paths, or user-entered
reasons to telemetry.

## Next experiment

Build a deterministic benchmark fixture at several catalog sizes and exercise
the API through its ASGI boundary. Compare the current builder with a prototype
that parses every YAML document once, while asserting that validation behavior
and immutable catalog outputs remain identical. Optimize only if runtime metrics
show that snapshot misses materially affect an agreed latency objective.
