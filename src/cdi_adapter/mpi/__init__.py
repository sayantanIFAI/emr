"""Master Patient Index — identity fetched from the documents themselves.

The uploader supplies only the files (and optionally an ABHA number). Each
document's extracted ``patient`` block yields a name / sex / age candidate;
``resolve_identity`` de-duplicates against existing patients (ABHA first, then
fuzzy name + birth-year) or mints a new CareFlow patient id ``CFP-<YYYY>-<seq>``.
After all of a job's documents are processed, ``merge_identity_evidence`` picks
the best-supported name / sex / DOB and finalises the record.
"""
from .service import (
    IdentityCandidate,
    IdentityResult,
    merge_identity_evidence,
    parse_name,
    parse_sex,
    resolve_identity,
)

__all__ = [
    "IdentityCandidate", "IdentityResult", "resolve_identity",
    "merge_identity_evidence", "parse_name", "parse_sex",
]
