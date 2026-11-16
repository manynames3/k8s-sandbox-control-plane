"""In-process counters. Exposed through Prometheus in phase 6; until then they are logged and
asserted in tests. A non-zero sweeper count means an earlier layer failed."""
from collections import Counter

sweeper_deletions: Counter[str] = Counter()          # by kind: jobs | pods | configmaps
reconcile_corrections: Counter[str] = Counter()      # by reason
k8s_api_errors = Counter[str]()                      # by operation
