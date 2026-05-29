"""Process-wide lock that serializes HDBSCAN/UMAP calls.

Why this exists
---------------
HDBSCAN and UMAP use Numba's `workqueue` threading layer under the hood
(the default on most macOS / Linux installs without `tbb`). The
workqueue layer is NOT thread-safe — if two Python threads enter Numba
parallel regions concurrently, Numba detects the race and TERMINATES
THE PROCESS with:

    "Numba workqueue threading layer is terminating:
     Concurrent access has been detected."

That happens in our app any time the scheduler's daily clustering job
fires while a user is hitting `/api/narrative-frames/landscape-established`
(or any of the other endpoints that recompute UMAP/HDBSCAN). Both threads
enter Numba simultaneously → backend dies.

Fix (without adding the `tbb` package as a dep): wrap every UMAP /
HDBSCAN call site with this lock. Only one thread can be inside the
critical section at a time; the others wait. With our typical N (~50–
500 points) each compute takes well under a second, so the wait is
imperceptible.

Usage
-----
    from app.services._numba_serialize import numba_lock

    with numba_lock:
        reducer = UMAP(...)
        coords = reducer.fit_transform(X)
        labels = HDBSCAN(...).fit_predict(coords)

If a single call site uses BOTH UMAP and HDBSCAN, put both inside one
`with` block — there's no benefit to releasing the lock between them
since the same thread will reacquire it immediately.

Alternative considered: switch the Numba threading layer to `tbb` via
`NUMBA_THREADING_LAYER=tbb`. That's the docs-recommended fix but
requires the `tbb` Python package + Intel's TBB native library.
Adding the lock keeps the dep tree the same while still being
correct.
"""
from __future__ import annotations

import threading

# Module-level singleton. Re-entrant so a function holding the lock can
# call into another function that also acquires it (e.g. a service that
# runs UMAP and calls another service that runs HDBSCAN). Without RLock
# the same thread would deadlock itself in that pattern.
numba_lock = threading.RLock()
