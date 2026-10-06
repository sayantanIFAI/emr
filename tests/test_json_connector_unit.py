"""JSON placeholder connector (UP-S3): the rules the JSON keeps. No database."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from cdi_adapter.output import json_connector as jc
from cdi_adapter.webapp import app as webapp
from cdi_adapter.webapp import jobs

DOC = str(uuid.uuid4())


def _fact(ftype, text, state="auto_accepted", **kw):
    return {"id": uuid.uuid4(), "fact_type": ftype, "local_text": text, "review_state": state,
            "review_note": kw.pop("note", None), "confidence_overall": kw.pop("conf", 0.99), **kw}


def _inputs(facts=(), status="validated", payload=None, pages=(), doc_extra=None):
    doc = {"id": DOC, "status": status, "page_count": 2, "original_filename": "rx (2 pages).pdf",
           **(doc_extra or {})}
    return jc.ResultInputs(doc, {"doc_type": "prescription", "is_handwritten": True},
                           list(pages), payload if payload is not None else {}, list(facts))


PAYLOAD = {"patient": {"name": "Anil Mehra", "age_text": "54 y", "sex": "M", "mrn": None},
           "prescriber": {"name": "Dr A Sen", "reg_no": "12345", "department": "Medicine"},
           "follow_up": "review after 2 weeks"}
MED = _fact("medication", "Tab Metformin 500 mg BD", medication={
    "drug_text": "Metformin", "strength_num": 500, "strength_unit": "mg", "frequency_code": "BD",
    "duration_days": 30, "route": "oral"})
LAB = _fact("lab_result", "HbA1c", state="in_review", note="two readers disagreed", conf=0.4,
            value_num=7.8, value_unit_ucum="%", ref_range_low=4.0, ref_range_high=5.6, abnormal_flag="H")


def test_every_value_carries_a_status_and_nothing_is_left_without_one():
    r = jc.build_result(_inputs([MED, LAB], payload=PAYLOAD))
    assert r["schema_version"] == "result.placeholder.v0" and r["document_id"] == DOC
    for section in ("patient", "doctor"):
        for k, v in r[section].items():
            assert set(v) == {"value", "status", "reason", "confidence"}, (section, k)
    assert r["follow_up"]["status"] == "not_gated"
    for item in (*r["medications"], *r["lab_tests"]):
        assert item["status"] in {"accepted", "needs_check", "rejected"}


def test_identity_read_from_the_page_is_labelled_not_gated_and_empty_is_null():
    r = jc.build_result(_inputs(payload=PAYLOAD))
    assert r["patient"]["name"] == {"value": "Anil Mehra", "status": "not_gated",
                                    "reason": "read from the page; not checked by the confidence gate",
                                    "confidence": None}
    assert r["patient"]["mrn"]["value"] is None and r["patient"]["mrn"]["reason"] is None   # unknown = null


def test_accepted_and_needs_check_come_from_the_review_state_with_the_reason():
    r = jc.build_result(_inputs([MED, LAB], payload=PAYLOAD))
    (med,), (lab,) = r["medications"], r["lab_tests"]
    assert med["status"] == "accepted" and med["reason"] is None
    assert med["drug"] == "Metformin" and med["strength"] == 500 and med["duration_days"] == 30
    assert lab["status"] == "needs_check" and lab["reason"] == "two readers disagreed"
    assert lab["value"] == 7.8 and lab["unit"] == "%" and lab["flag"] == "H" and lab["confidence"] == 0.4
    assert r["needs_check_count"] == 1


@pytest.mark.parametrize("state,expected", [("auto_accepted", "accepted"), ("clinician_confirmed", "accepted"),
                                            ("corrected", "accepted"), ("pending", "needs_check"),
                                            ("in_review", "needs_check"), ("rejected", "rejected")])
def test_status_mapping(state, expected):
    r = jc.build_result(_inputs([_fact("condition", "T2DM", state=state)]))
    assert r["diagnoses"][0]["status"] == expected


def test_a_doubtful_value_never_lets_the_document_read_as_complete():
    assert jc.build_result(_inputs([MED]))["status"] == "complete"
    assert jc.build_result(_inputs([MED, LAB]))["status"] == "needs_check"
    assert jc.build_result(_inputs([MED, _fact("condition", "x", state="rejected")]))["status"] == "needs_check"


def test_document_status_cases():
    assert jc.build_result(_inputs([], status="validated"))["status"] == "incomplete"
    assert jc.build_result(_inputs([MED], status="ocr_done"))["status"] == "processing"
    assert jc.build_result(_inputs([], status="error"))["status"] == "error"
    held = jc.build_result(_inputs([], status="quality_hold", doc_extra={"error_detail": "rescan: too blurred"}))
    assert held["status"] == "held_for_rescan" and held["quality"]["passed"] is False
    assert held["quality"]["reasons"] == ["rescan: too blurred"]


def test_quality_comes_from_every_page_with_the_page_number():
    pages = [{"page_no": 1, "preproc": {"quality": {"passed": True, "reasons": [], "warnings": []}}},
             {"page_no": 2, "preproc": {"quality": {"passed": False, "reasons": ["glare covers 40%"],
                                                    "warnings": ["page may be rotated"]}}}]
    q = jc.build_result(_inputs([MED], pages=pages))["quality"]
    assert q == {"checked": True, "passed": False, "reasons": ["page 2: glare covers 40%"],
                 "warnings": ["page 2: page may be rotated"]}


def test_what_is_not_extracted_yet_is_listed_not_hidden():
    r = jc.build_result(_inputs())
    for gap in ("doctor.designation", "organisation.name", "organisation.address", "lab_preparation",
                "follow_up.interval", "patient.address"):
        assert gap in r["not_extracted"]


def test_the_notice_is_always_present():
    assert jc.build_result(_inputs())["notice"].endswith("Not for diagnosis.")
    assert "needs a check" in jc.NOTICE


def test_the_same_document_gives_the_same_bytes():
    a = jc.to_bytes(jc.build_result(_inputs([MED, LAB], payload=PAYLOAD)))
    b = jc.to_bytes(jc.build_result(_inputs([MED, LAB], payload=PAYLOAD)))
    assert a == b and a.endswith(b"\n")
    json.loads(a)                                 # valid JSON
    assert "generated_at" not in a.decode()      # no timestamp


def test_unknown_fact_types_are_kept_under_other_not_dropped():
    r = jc.build_result(_inputs([_fact("allergy", "penicillin")]))
    assert [i["text"] for i in r["other"]] == ["penicillin"]


def test_a_wrong_connector_name_is_refused():
    with pytest.raises(ValueError):
        jc.get_connector("nope")
    assert jc.get_connector().name == "json_placeholder"


# ------------------------------------------------------------------ gather() reads what it should


def test_gather_reads_the_document_facts_medication_detail_and_payload(monkeypatch):
    class Scope:
        def __enter__(self):
            return "sess"

        def __exit__(self, *a):
            return False

    class Res:
        def scalar_one_or_none(self):
            return PAYLOAD

    class Sess:
        def execute(self, *a, **k):
            return Res()

    monkeypatch.setattr(jc, "session_scope", lambda: Scope())
    monkeypatch.setattr(jc.repo, "get_document", lambda s, d: {"id": d, "status": "validated", "page_count": 1})
    monkeypatch.setattr(jc.repo, "list_clinical_facts", lambda s, document_id: [dict(MED, medication=None), dict(LAB)])
    monkeypatch.setattr(jc.repo, "get_medication_detail", lambda s, fid: {"drug_text": "Metformin"})
    monkeypatch.setattr(jc.repo, "get_doc_classification", lambda s, d: {"doc_type": "prescription"})
    monkeypatch.setattr(jc.repo, "list_document_pages", lambda s, d: [])
    inp = jc.gather(Sess(), DOC)
    assert inp.payload == PAYLOAD
    assert inp.facts[0]["medication"] == {"drug_text": "Metformin"} and "medication" not in inp.facts[1]
    monkeypatch.setattr(jc.repo, "get_document", lambda s, d: None)
    assert jc.gather(Sess(), DOC) is None


# ------------------------------------------------------------------ endpoints


@pytest.fixture
def client(monkeypatch):
    class Fake:
        name = "json_placeholder"

        def render(self, document_id):
            return jc.build_result(_inputs([MED, LAB], payload=PAYLOAD)) if document_id == DOC else None

    monkeypatch.setattr(webapp, "get_connector", lambda: Fake())
    return TestClient(webapp.app)


def test_the_screen_json_and_the_download_are_the_same_bytes(client):
    shown = client.get(f"/api/documents/{DOC}/result.json")
    file = client.get(f"/api/documents/{DOC}/result.json?download=true")
    assert shown.status_code == 200 and shown.headers["content-type"] == "application/json"
    assert "content-disposition" not in shown.headers
    assert file.headers["content-disposition"] == f'attachment; filename="result_{DOC}.json"'
    assert shown.content == file.content == jc.to_bytes(jc.build_result(_inputs([MED, LAB], payload=PAYLOAD)))


def test_an_unknown_or_malformed_document_id_is_a_plain_404(client):
    assert client.get(f"/api/documents/{uuid.uuid4()}/result.json").status_code == 404
    assert client.get("/api/documents/not-a-uuid/result.json").status_code == 404


def test_the_job_view_lists_every_document_and_does_not_drop_a_failed_one(client, monkeypatch):
    job = jobs.Job(id="jobx", abha=None)
    ok = jobs.DocProg(filename="a.pdf", document_id=DOC, status="done")
    bad = jobs.DocProg(filename="b.png", document_id=None, status="error", error="rescan: too blurred")
    job.docs = [ok, bad]
    monkeypatch.setattr(webapp, "get_job", lambda jid: job if jid == "jobx" else None)
    body = client.get("/api/jobs/jobx/result.json").json()
    assert body["job_id"] == "jobx" and len(body["results"]) == 2
    assert body["results"][0]["document_id"] == DOC and body["results"][0]["status"] == "needs_check"
    assert body["results"][1] == {"filename": "b.png", "document_id": None, "status": "error",
                                  "reason": "rescan: too blurred"}
    assert client.get("/api/jobs/nope/result.json").status_code == 404
