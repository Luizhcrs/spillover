from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

requests_total = Counter(
    "spillover_requests_total",
    "Total proxy requests",
    labelnames=("project", "provider", "status"),
    registry=REGISTRY,
)

request_duration = Histogram(
    "spillover_request_duration_seconds",
    "Total proxy request duration",
    labelnames=("phase",),
    registry=REGISTRY,
)

overflow_triggered_total = Counter(
    "spillover_overflow_triggered_total",
    "Times the eviction selector returned non-empty",
    labelnames=("project",),
    registry=REGISTRY,
)

episodes_archived_total = Counter(
    "spillover_episodes_archived_total",
    "Episodes inserted into the archive",
    labelnames=("project", "type"),
    registry=REGISTRY,
)

retriever_hits_total = Counter(
    "spillover_retriever_hits_total",
    "Retriever hits attributed to each source",
    labelnames=("project", "source"),
    registry=REGISTRY,
)

facet_queue_depth = Gauge(
    "spillover_facet_queue_depth",
    "Current depth of the facet extraction queue",
    registry=REGISTRY,
)

compaction_detected_total = Counter(
    "spillover_compaction_detected_total",
    "Times the proxy detected client-side compaction",
    labelnames=("project",),
    registry=REGISTRY,
)
