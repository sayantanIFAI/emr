"""Cap native thread pools to the container's *real* CPU budget.

RunPod reports the host core count (`os.cpu_count()` == 128 on the pilot pod) but
the cgroup pins the container to a fraction of that (`cpu.max` = "1360000 100000"
=> ~13.6 CPUs). Every native library - OpenMP, OpenBLAS, MKL, NumExpr, the Rust
`rayon` pool behind PyMuPDF, ONNXRuntime - otherwise spins up ~128 threads *each*,
and the web-app's Phase-1 pool runs several of them at once (ingest render +
RapidOCR per document), so hundreds of threads thrash ~13 cores. Observed: ingest
spiking from <1 s to 16 s under a 3-document job.

This module is imported first from ``cdi_adapter/__init__`` so the env vars are in
place before NumPy / OpenCV / ONNXRuntime / torch load. Every value uses
``setdefault`` - an explicit env override always wins.
"""
from __future__ import annotations

import os
from pathlib import Path


def _cgroup_cpu_quota() -> float | None:
    """Effective CPU count from the cgroup, or None if unconstrained."""
    v2 = Path("/sys/fs/cgroup/cpu.max")
    if v2.exists():
        try:
            quota, period = v2.read_text().split()
            if quota != "max":
                return float(quota) / float(period)
        except Exception:
            pass
    try:
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if q > 0 and p > 0:
            return q / p
    except Exception:
        pass
    return None


def _budget() -> int:
    quota = _cgroup_cpu_quota()
    host = os.cpu_count() or 4
    eff = int(quota) if quota else host
    return max(2, min(eff, host))


# How many concurrent CPU-heavy pipeline tasks we expect (Phase-1 pool width).
_CONCURRENCY = int(os.environ.get("CDI_JOB_MAX_WORKERS", "5"))

_EFFECTIVE_CPUS = _budget()
# leave one core for uvicorn / the DB driver / the event loop, then divide the
# rest across the concurrent tasks. Floor of 2 so a single OCR/render call is not
# choked on a small box; the important part is not letting each library see 128.
_PER_TASK = max(2, (_EFFECTIVE_CPUS - 1) // max(1, _CONCURRENCY))

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "RAYON_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
             "ORT_INTRA_OP_NUM_THREADS"):
    os.environ.setdefault(_var, str(_PER_TASK))

# OpenCV keeps its own pool; it honours this only if set before first use.
os.environ.setdefault("OPENCV_FOR_THREADS_NUM", str(_PER_TASK))

CPU_BUDGET = _EFFECTIVE_CPUS
THREADS_PER_TASK = _PER_TASK
