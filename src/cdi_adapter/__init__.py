"""CDI-Adapter: Clinical Document Intelligence -> EMR/EHR adapter.

An AI side-car on top of a legacy Hospital Management System. Ingests scanned
documents (the hospital's only medical record) and turns them into structured,
provenance-bound clinical facts (rows) plus validated FHIR R4 bundles (JSON),
ABDM/ABHA-ready. The legacy database is never modified.
"""

# Must run before NumPy / OpenCV / ONNXRuntime / torch import anywhere.
from . import _cpu as _cpu  # noqa: F401,E402

__version__ = "0.1.0"
