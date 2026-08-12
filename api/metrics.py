"""Small, dependency-free, bounded runtime metrics for the local API."""

from collections import defaultdict
from threading import Lock
from time import perf_counter
from typing import DefaultDict, Dict, Tuple


LATENCY_BUCKETS_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class ApiMetrics:
    """Aggregate metrics without retaining user identifiers or request content."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._started_at = perf_counter()
        self._active = 0
        self._max_active = 0
        self._requests: DefaultDict[Tuple[str, str, int], Dict[str, object]] = defaultdict(
            lambda: {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0,
                     "buckets": [0] * (len(LATENCY_BUCKETS_SECONDS) + 1)}
        )
        self._snapshots = {
            "hit": {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0},
            "miss": {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0},
            "repository": {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0},
        }

    def request_started(self) -> None:
        with self._lock:
            self._active += 1
            self._max_active = max(self._max_active, self._active)

    def request_finished(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self._active -= 1
            metric = self._requests[(method, route, status)]
            metric["count"] += 1
            metric["total_seconds"] += seconds
            metric["max_seconds"] = max(metric["max_seconds"], seconds)
            bucket = next(
                (index for index, bound in enumerate(LATENCY_BUCKETS_SECONDS) if seconds <= bound),
                len(LATENCY_BUCKETS_SECONDS),
            )
            metric["buckets"][bucket] += 1

    def snapshot_finished(self, result: str, seconds: float) -> None:
        with self._lock:
            metric = self._snapshots[result]
            metric["count"] += 1
            metric["total_seconds"] += seconds
            metric["max_seconds"] = max(metric["max_seconds"], seconds)

    def export(self) -> dict:
        with self._lock:
            requests = []
            for (method, route, status), value in sorted(self._requests.items()):
                requests.append({
                    "method": method, "route": route, "status": status,
                    "count": value["count"],
                    "totalSeconds": round(value["total_seconds"], 6),
                    "maxSeconds": round(value["max_seconds"], 6),
                    "latencyBuckets": {
                        **{str(bound): value["buckets"][index]
                           for index, bound in enumerate(LATENCY_BUCKETS_SECONDS)},
                        "+Inf": value["buckets"][-1],
                    },
                })
            return {
                "uptimeSeconds": round(perf_counter() - self._started_at, 3),
                "requests": requests,
                "concurrency": {"active": self._active, "maximum": self._max_active},
                "catalogSnapshots": {
                    result: {
                        "count": value["count"],
                        "totalSeconds": round(value["total_seconds"], 6),
                        "maxSeconds": round(value["max_seconds"], 6),
                    }
                    for result, value in self._snapshots.items()
                },
            }
