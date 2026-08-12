from api.metrics import ApiMetrics


def test_metrics_are_aggregated_by_normalized_route():
    metrics = ApiMetrics()
    metrics.request_started()
    metrics.request_finished("GET", "/api/items/{item_id}", 200, 0.012)
    metrics.snapshot_finished("miss", 0.25)

    exported = metrics.export()
    assert exported["concurrency"] == {"active": 0, "maximum": 1}
    assert exported["requests"][0]["route"] == "/api/items/{item_id}"
    assert exported["requests"][0]["count"] == 1
    assert exported["requests"][0]["latencyBuckets"]["0.025"] == 1
    assert exported["catalogSnapshots"]["miss"]["count"] == 1


def test_metrics_do_not_export_request_values():
    metrics = ApiMetrics()
    metrics.request_started()
    metrics.request_finished("GET", "/api/trips/{trip_id}", 404, 0.001)
    exported = str(metrics.export())
    assert "private-trip" not in exported
