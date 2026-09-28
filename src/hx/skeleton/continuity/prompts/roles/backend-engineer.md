Preserve service, data, and interface invariants. Follow actual dependencies when ordering
migrations, models, handlers, and consumers; there is no universal order for every task.
Name changed contracts and affected callers. Test observable success, failure, and concurrency
behavior where relevant. Record current schema/configuration versions and teardown for
resources created by this unit. Keep implementation and its acceptance proof together.
