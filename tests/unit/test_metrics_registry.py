from spillover.metrics.registry import REGISTRY, requests_total


def test_counter_increments_and_is_in_registry():
    requests_total.labels(project="p", provider="anthropic", status="200").inc()
    families = list(REGISTRY.collect())
    names = {f.name for f in families}
    assert "spillover_requests" in names or any(
        n.startswith("spillover_requests") for n in names
    )
