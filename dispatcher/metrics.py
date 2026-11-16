"""In-process counters, exported on :9100/metrics. A non-zero sweeper count means an earlier cleanup
layer failed (that is what the alert watches)."""
from collections import Counter
from collections.abc import Iterator

from prometheus_client import CollectorRegistry
from prometheus_client.core import CounterMetricFamily
from prometheus_client.registry import Collector

sweeper_deletions: Counter[str] = Counter()          # by kind: jobs | pods | configmaps
reconcile_corrections: Counter[str] = Counter()      # by reason
k8s_api_errors: Counter[str] = Counter()             # by operation


class _Collector(Collector):
    def collect(self) -> Iterator[CounterMetricFamily]:
        for name, doc, label, data in (
            ("ksc_sweeper_deletions", "Objects the sweeper had to delete", "kind", sweeper_deletions),
            ("ksc_reconcile_corrections", "Divergences reconcile corrected", "reason", reconcile_corrections),
            ("ksc_dispatcher_k8s_api_errors", "Kubernetes API errors seen by the dispatcher", "operation", k8s_api_errors),
        ):
            fam = CounterMetricFamily(name, doc, labels=[label])
            for key, n in sorted(data.items()):
                fam.add_metric([key], n)
            if not data:
                fam.add_metric(["none"], 0)  # a series exists from the start, so rate() and alerts work
            yield fam


registry = CollectorRegistry()
registry.register(_Collector())
