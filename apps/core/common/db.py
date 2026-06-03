"""Shared database backend constraints.

Relational backends cap the number of host parameters (bind values) in a
single statement, so a query with a huge `id__in=[...]` / bulk list fails at
execution once it exceeds that ceiling. The limit is BACKEND-SPECIFIC — our
SQLite is the tightest at 999 (its SQLITE_MAX_VARIABLE_NUMBER, "too many SQL
variables") ; PostgreSQL allows 65535. So id lists are chunked with
`itertools.batched(ids, ID_IN_CHUNK, strict=False)` to stay safely under any
of them. This is about the per-STATEMENT parameter count, NOT peak memory:
the caller still materializes the full list, so bound that separately (e.g.
DIGEST_MAX_BATCH_ITEMS).
"""

# Small enough to clear the tightest backend ceiling (SQLite's 999) with
# headroom for the statement's other binds, large enough that chunking is
# rare. watches.runs._ENQUEUE_CHUNK predates this with the same value + its
# own commentary ; kept separate so that module stays self-contained, but any
# new chunking should import this.
ID_IN_CHUNK = 500
