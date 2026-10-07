"""A photo whose page edges cannot be found is read and marked "needs check" (never refused), and a
job that really did stop says why instead of a generic message."""
from __future__ import annotations

from cdi_adapter.output.json_connector import _capture_flagged
from cdi_adapter.webapp.jobs import DocProg, Job, why_stopped


def _page(codes):
    return {"page_no": 1, "preproc": {"quality": {"passed": True, "reasons": [], "warning_codes": codes}}}


def test_a_page_read_without_found_edges_makes_the_result_need_a_check():
    assert _capture_flagged([_page(["page_edges_not_found"])]) is True
    assert _capture_flagged([_page(["blank_page"])]) is False
    assert _capture_flagged([_page([]), {"page_no": 2, "preproc": None}]) is False


def test_why_stopped_names_each_failed_file_and_drops_the_internal_prefix():
    job = Job(id="j1", abha=None)
    job.docs = [DocProg(filename="a.jpg", error="rescan: page 1: image too blurred - hold the camera steady"),
                DocProg(filename="b.jpg"),
                DocProg(filename="c.jpg", error="could not decode page image")]
    msg = why_stopped(job)
    assert msg == "a.jpg: page 1: image too blurred - hold the camera steady c.jpg: could not decode page image"
    assert "rescan:" not in msg


def test_why_stopped_is_none_when_no_document_recorded_a_reason():
    job = Job(id="j2", abha=None)
    job.docs = [DocProg(filename="a.jpg")]
    assert why_stopped(job) is None
