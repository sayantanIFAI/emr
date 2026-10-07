"""The admin upload page: token + mobile number first, results grouped by patient, collapsible, autocomplete instead of a
dropdown, the mapping table, the summary tables per prescription, several uploads at once, and the camera paths."""
from __future__ import annotations

import re

from cdi_adapter.webapp.upload_page import ADMIN_PAGE


def test_the_tables_are_defined_and_drawn_in_order():
    for ident in ("tbl-patient", "tbl-organization", "tbl-doctor", "tbl-booking", "tbl-labs", "tbl-visits"):
        assert f'"{ident}"' in ADMIN_PAGE                       # the page script draws each table under this id
    order = [ADMIN_PAGE.index(f'"{i}"') for i in ("tbl-patient", "tbl-organization", "tbl-doctor", "tbl-booking", "tbl-labs", "tbl-visits")]
    assert order == sorted(order)


def test_the_tables_are_drawn_from_the_result_and_every_value_is_escaped():
    assert "summaryHtml(r)" in ADMIN_PAGE
    vcell = ADMIN_PAGE[ADMIN_PAGE.index("function vcell"):ADMIN_PAGE.index("function section")]
    assert "esc(" in vcell


def test_several_uploads_can_run_at_once_the_form_is_not_locked():
    assert "LOCKED" not in ADMIN_PAGE
    assert "JOBS.unshift" in ADMIN_PAGE and 'id="jobs"' in ADMIN_PAGE


def test_the_camera_button_has_a_phone_path_and_a_computer_path():
    assert 'capture="environment"' in ADMIN_PAGE                       # phone / tablet: the device's camera app
    assert "getUserMedia" in ADMIN_PAGE and 'id="camdlg"' in ADMIN_PAGE  # computer: live camera view
    assert "pointer: coarse" in ADMIN_PAGE


# ---- 1. token + mobile number first; the upload appears only when both are filled
def test_the_upload_section_is_hidden_until_the_token_and_the_mobile_number_are_filled():
    assert 'id="token"' in ADMIN_PAGE and 'id="phone"' in ADMIN_PAGE
    assert re.search(r'<div class="card" id="form-card" hidden>', ADMIN_PAGE)             # hidden in the markup
    gate = ADMIN_PAGE[ADMIN_PAGE.index("function gate()"):ADMIN_PAGE.index("let PHONE_SEQ")]
    assert 'form-card").hidden=!open' in gate and "tOk&&pOk&&have" in gate
    assert 'fd.append("token_no",token); fd.append("phone",phone)' in ADMIN_PAGE            # both are sent with the pages


# ---- 2. prescriptions already uploaded for this number: say so, with an option to proceed
def test_an_existing_upload_for_the_number_is_announced_and_waits_for_proceed():
    assert 'id="existing"' in ADMIN_PAGE and 'id="proceed"' in ADMIN_PAGE and "api/patients/existing" in ADMIN_PAGE
    assert "(!dup||ACK===d)" in ADMIN_PAGE                                                   # the upload waits for Proceed


# ---- 3 / 4. by patient (name + mobile number), never by batch; collapsible
def test_results_are_grouped_by_patient_and_never_labelled_as_a_batch():
    assert "Batch" not in ADMIN_PAGE.replace("Batch", "Batch", 0) or "BATCH" not in ADMIN_PAGE
    assert 'label:"Batch' not in ADMIN_PAGE and "Batch " not in ADMIN_PAGE
    assert "gkey(phone,name)" in ADMIN_PAGE and 'class="cf-det grp"' in ADMIN_PAGE


def test_every_group_prescription_and_section_is_collapsible():
    assert ADMIN_PAGE.count("<details") >= 6
    for cls in ('cf-det grp', 'cf-det doc', 'cf-det sec', 'cf-det jobbox'):
        assert cls in ADMIN_PAGE


# ---- 5. autocomplete on the mobile number, no dropdown of patients
def test_patients_are_found_by_typing_not_by_a_dropdown():
    assert 'id="psearch"' in ADMIN_PAGE and 'role="combobox"' in ADMIN_PAGE and "api/patients/search" in ADMIN_PAGE
    assert "<select" not in ADMIN_PAGE                                                        # no dropdown of thousands


# ---- 6 / 7 / 8. organisation section, standard names, the mapping table on the screen
def test_the_organisation_section_and_the_standard_name_are_shown():
    assert "Organisation (hospital / clinic)" in ADMIN_PAGE and "r.organization" in ADMIN_PAGE
    assert "t.standard_name" in ADMIN_PAGE and "<th>Standard name</th>" in ADMIN_PAGE


def test_the_mapping_table_is_on_the_screen_and_can_be_added_to():
    assert 'id="maptbl"' in ADMIN_PAGE and "api/mappings/lab" in ADMIN_PAGE and 'id="m-add"' in ADMIN_PAGE


# ---- 9. the latest dated visit is the one used, the others are listed
def test_the_dated_visits_are_listed_and_the_latest_is_marked():
    assert "latest — used above" in ADMIN_PAGE and "Dated visits found on the pages" in ADMIN_PAGE
