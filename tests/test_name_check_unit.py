"""The patient's name is read again from its own line and the readings are compared; it stays 'to confirm' (extract/service.py)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cdi_adapter.config import settings
from cdi_adapter.extract import resolve_llm as R
from cdi_adapter.extract import service as X


def _png(w=900, h=1200):
    img = np.full((h, w, 3), 245, np.uint8)
    ok, enc = cv2.imencode(".png", img)
    return enc.tobytes()


class Seq:
    """A model that answers the name question with these readings, in order."""
    def __init__(self, *names): self.names, self.calls = list(names), 0
    def vlm_json_ex(self, image, prompt, schema, **kw):
        n = self.names[self.calls % len(self.names)]
        self.calls += 1
        return {"name": n}, "m"


BLOCKS = [{"id": "b1", "text": "For Mr. Oukar Broadway, 67yrs", "bbox": [120, 300, 800, 360], "page_id": "p1"},
          {"id": "b2", "text": "T2DM 19y", "bbox": [120, 400, 500, 450], "page_id": "p1"}]


def test_the_name_line_is_cut_out_and_enlarged_three_times():
    crops = R.name_crops(_png(), BLOCKS, "Mr. Oukar Broadway")
    sizes = [cv2.imdecode(np.frombuffer(c, np.uint8), cv2.IMREAD_COLOR).shape[:2] for c in crops]
    assert len(sizes) == 3 and sizes[0][0] < 200 and sizes[2][1] > sizes[0][1] * 2          # the one line, at 1x / 1.6x / 2.4x


def test_readings_that_agree_on_another_name_replace_the_first_reading_and_all_readings_are_kept():
    payload = {"patient": {"name": "Mr. Oukar Broadway"}}
    X._check_the_name(Seq("Oukar Chowdhury", "Oukar Chowdury", "Oukar Chowdhury"), _png(), BLOCKS, payload)
    assert payload["patient"]["name"].startswith("Oukar Chowd")
    assert payload["_name_reads"][0] == "Mr. Oukar Broadway" and len(payload["_name_reads"]) == 4
    assert payload["_name_agreement"] == [3, 4]


def test_readings_that_disagree_leave_the_first_reading_and_say_so():
    payload = {"patient": {"name": "Mr. Oukar Broadway"}}
    X._check_the_name(Seq("Ravi Das", "Kiran Paul", "Asha Rao"), _png(), BLOCKS, payload)
    assert payload["patient"]["name"] == "Mr. Oukar Broadway" and payload["_name_agreement"][0] == 1


def test_a_failed_or_switched_off_reread_changes_nothing(monkeypatch):
    class Boom:
        def vlm_json_ex(self, *a, **k): raise RuntimeError("down")
    p = {"patient": {"name": "Asha Rao"}}
    X._check_the_name(Boom(), _png(), BLOCKS, p)
    assert p["patient"]["name"] == "Asha Rao" and p["_name_reads"] == ["Asha Rao"]
    monkeypatch.setattr(settings, "name_reread", False)
    q = {"patient": {"name": "Asha Rao"}}
    X._check_the_name(Seq("Other Name"), _png(), BLOCKS, q)
    assert q["patient"]["name"] == "Asha Rao"


def test_the_model_is_given_the_picture_the_setting_names(monkeypatch):
    asked = []
    monkeypatch.setattr(X.storage, "key_from_uri", lambda u: u)
    monkeypatch.setattr(X.storage, "get_bytes", lambda k: asked.append(k) or b"img")
    pg = {"image_uri": "s3://p/norm.png", "preproc": {"src_uri": "s3://p/src.png"}}
    monkeypatch.setattr(settings, "model_image", "normalized")
    X._page_image(pg)
    monkeypatch.setattr(settings, "model_image", "source")
    X._page_image(pg)
    X._page_image({"image_uri": "s3://p/norm.png", "preproc": None})              # no source copy kept: the normalized one
    assert asked == ["s3://p/norm.png", "s3://p/src.png", "s3://p/norm.png"]


def test_the_name_prompt_says_indian_name_not_english_word_and_gives_no_example_names():
    p = R.name_prompt()
    assert "Indian personal name" in p and "not an English word" in p and "space between the first name and the surname" in p
    for leaked in ("Onkar", "Chowdhury", "Subrata", "Rajesh", "Banerjee", "Mukherjee", "Das", "Sen,"):
        assert leaked not in p                          # an example in the prompt leaked into the answer when it was tried


def test_the_same_bytes_for_another_patient_are_another_document_and_the_same_patient_is_the_same_document():
    from cdi_adapter.ingest.service import document_hash
    raw = b"\x89PNG-fake-bytes"
    import hashlib
    assert document_hash(raw) == hashlib.sha256(raw).hexdigest()                          # no scope: the plain file hash (all other channels)
    assert document_hash(raw, "9830011234|T-1") == document_hash(raw, "9830011234|T-1")   # same patient + token: the same document
    assert document_hash(raw, "9830011234|T-1") != document_hash(raw, "9830011234|T-2")   # another token: another document
    assert document_hash(raw, "9830011234|T-1") != document_hash(raw, "9831122334|T-1")   # another mobile: another document
    assert document_hash(raw, "9830011234|T-1") != document_hash(raw)


# ---- the first name put to the model as a choice among spellings (suggestions only)
def test_the_options_are_the_readings_then_their_one_letter_confusions_and_a_spelling_several_readings_lead_to_ranks_higher():
    opts = R.first_name_options(["Omkar", "Oukan", "Oukar", "Oscar"])
    assert {"Omkar", "Oukan", "Oukar", "Oscar"} <= set(opts) and "Onkar" in opts          # Omkar (m->n) and Oukar (u->n) both lead to Onkar
    assert len(opts) <= 7 and R.first_name_options(["Al", "x1"]) == []                    # too short / not letters: nothing to vary


def test_the_votes_count_each_picked_spelling_over_several_orders_and_crops():
    # the real check: every call gets numbered options and the answer maps back to the option at that place
    seen = []

    class Rec:
        def vlm_json_ex(self, image, prompt, schema, **kw):
            seen.append(prompt)
            return {"choice": 1}, "m"
    got = R.first_name_votes(Rec(), [b"a", b"b"], ["Omkar", "Onkar", "Oukan"], shuffles=3)
    assert sum(got.values()) == 6 and set(got) <= {"Omkar", "Onkar", "Oukan"}
    assert len(seen) == 6 and all("1) " in p and "2) " in p and "3) " in p for p in seen)
    assert R.first_name_votes(Rec(), [], ["a", "b"]) == {} and R.first_name_votes(Rec(), [b"x"], ["only"]) == {}


def test_the_suggested_spellings_join_the_offered_readings_and_the_shown_name_does_not_change(monkeypatch):
    monkeypatch.setattr(R, "first_name_votes", lambda client, crops, options, shuffles=3: {"Onkar": 5, "Omkar": 3, "Oukan": 1})
    monkeypatch.setattr(R, "name_crops", lambda img, blocks, name: [b"c1", b"c2", b"c3"])
    payload = {"patient": {"name": "Mr. Omkar Chowdhury"}, "_name_reads": ["Mr. Omkar Chowdhury", "Omkar Chaudhury", "Oukan Chowdhury"]}
    X._suggest_first_names(object(), [b"img"], BLOCKS, payload)
    assert payload["patient"]["name"] == "Mr. Omkar Chowdhury"                      # not changed here
    assert "Onkar Chowdhury" in payload["_name_reads"] and payload["_name_votes"]["Onkar"] == 5
    assert payload["_name_reads"].count("Omkar Chowdhury") == 1


def test_a_title_is_not_part_of_the_name_words():
    assert X._name_tokens("Mr. Onkar Chowdhury") == ["Onkar", "Chowdhury"] and X._name_tokens("Smt Asha Rao") == ["Asha", "Rao"]
    assert X._name_tokens("Asha Rao") == ["Asha", "Rao"] and X._name_tokens("") == []


def test_a_loop_that_wrote_the_same_entry_again_and_again_keeps_one_copy():
    loop = [{"text": "As Pantocid-DIR (40)-1 tab OD", "evidence": [f"b{i}"]} for i in range(31, 73)]
    got = X._collapse_repeats({"investigations": loop + [{"text": "HbA1c", "evidence": ["b2"]}, {"text": "hba1c ", "evidence": ["b9"]}],
                               "advice": ["rest", "Rest", "diet"], "patient": {"name": "Asha Rao"}, "n": 3})
    assert [i["text"] for i in got["investigations"]] == ["As Pantocid-DIR (40)-1 tab OD", "HbA1c"]
    assert got["advice"] == ["rest", "diet"] and got["patient"] == {"name": "Asha Rao"} and got["n"] == 3
    assert X._collapse_repeats([{"a": 1}, {"a": 1}]) == [{"a": 1}, {"a": 1}]                  # entries without a text are left alone


def test_the_extraction_has_a_lower_token_ceiling_and_a_stronger_retry_penalty():
    assert settings.extract_max_tokens_mlp1 == 800 and settings.vllm_retry_repetition_penalty >= 1.15
