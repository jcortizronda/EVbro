"""The threads that talk to the Ebro cloud.

Every conversation with the cloud is blocking (`core/` uses `requests` and `urllib`) and runs in a thread. They used to share the
event loop's default executor with everything else that runs in a thread (password hashing at sign-in, Web Push, file work): with
the cloud slow (a read can take ~140 s in the worst case, with its retries), the cars of a busy server could take every thread and
a sign-in waited behind them. The cloud now has threads of its own; what is left of the default executor is for the rest.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

# One conversation at a time per account (`VehiclePoller.cloud_lock`), so this bounds how many accounts talk at once, not how many
# cars there are. Plenty for the servers there are today (about 40 accounts read every few minutes).
CLOUD_THREADS = 24

CLOUD_EXECUTOR = ThreadPoolExecutor(max_workers=CLOUD_THREADS, thread_name_prefix="ebro-cloud")
