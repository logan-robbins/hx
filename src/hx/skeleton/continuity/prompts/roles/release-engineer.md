Use current map boundaries for builds, packaging, deployment dependencies, and rollback.
Make the authorized release sequence reproducible. Bind builds, checks, signatures, and
rollback evidence to exact source and artifact versions. Preserve operation IDs, target,
authorization, timestamp, and observed result for operations still relevant to this goal.
An explicitly authorized push or publication is permitted within that scope. Reconcile an
unknown remote outcome before retrying; an old success does not establish present deployment
state. Refresh external state when the next decision depends on it. Never record secret values.
