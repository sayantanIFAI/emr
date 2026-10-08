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
    assert 'id="existing"' in ADMIN_PAGE and 'id="proceed"' in ADMIN_PAGE and "api/intake/existing" in ADMIN_PAGE
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
    assert 'id="psearch"' in ADMIN_PAGE and 'role="combobox"' in ADMIN_PAGE and "api/intake/search" in ADMIN_PAGE
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


def test_a_name_read_slightly_differently_joins_the_same_patient_on_the_same_number():
    assert "function sameName" in ADMIN_PAGE and "find(h=>h.phone===phone&&sameName(h.name,name))" in ADMIN_PAGE


def test_prescriptions_and_patients_are_listed_newest_first():
    assert 'localeCompare(a.uploaded||"")' in ADMIN_PAGE and "newest(b).localeCompare(newest(a))" in ADMIN_PAGE


# ---- a read patient name is never final: a person confirms or corrects it
def test_the_name_is_to_confirm_until_a_person_confirms_and_the_other_readings_are_offered():
    assert "function nameCell" in ADMIN_PAGE and "nm-ok" in ADMIN_PAGE
    assert ">to confirm<" in ADMIN_PAGE and "name not read: please type it" in ADMIN_PAGE and "Other readings of the name" in ADMIN_PAGE
    assert 'fetch("api/intake/name"' in ADMIN_PAGE and "function applyName" in ADMIN_PAGE
    assert '["Name",nameCell(r)]' in ADMIN_PAGE                                         # the patient table's Name row is the confirm box


# ---- the prescription image can be clicked and shown; the name line is shown beside the confirm box
def test_the_prescription_picture_opens_in_a_viewer_with_zoom_pages_and_the_original_photo():
    assert 'id="imgdlg"' in ADMIN_PAGE and "function openImage" in ADMIN_PAGE and "api/intake/page-image" in ADMIN_PAGE
    for control in ("img-in", "img-out", "img-fit", "img-prev", "img-next", "img-view", "img-close"):
        assert f'id="{control}"' in ADMIN_PAGE
    assert 'class="docthumb"' in ADMIN_PAGE and "Click the picture to see the prescription full size." in ADMIN_PAGE


def test_the_name_as_written_is_shown_beside_the_confirm_box_and_opens_the_page():
    assert "api/intake/name-crop" in ADMIN_PAGE and "As written on the paper" in ADMIN_PAGE and 'class="img-open"' in ADMIN_PAGE


# ---- two tabs: capture & upload (the whole flow) and Extracted (collapsed list)
def test_there_are_two_tabs_and_the_upload_flow_and_the_extracted_list_sit_in_their_own_panel():
    assert 'role="tablist"' in ADMIN_PAGE and 'id="tab-up"' in ADMIN_PAGE and 'id="tab-ex"' in ADMIN_PAGE
    up = ADMIN_PAGE[ADMIN_PAGE.index('id="panel-up"'):ADMIN_PAGE.index('id="panel-ex"')]
    ex = ADMIN_PAGE[ADMIN_PAGE.index('id="panel-ex"'):ADMIN_PAGE.index("<dialog")]
    for block in ('id="patient-card"', 'id="form-card"', 'id="emr-card"', 'id="jobs"', 'id="camera"'):
        assert block in up and block not in ex                                              # token, capture, upload and progress: tab 1
    for block in ('id="patients-card"', 'id="psearch"', 'id="groups"', 'id="map-card"'):
        assert block in ex and block not in up                                              # the extracted list (and the mapping table): tab 2
    assert 'id="panel-ex" role="tabpanel" aria-labelledby="tab-ex" hidden' in ADMIN_PAGE     # opens on the upload tab


def test_a_read_prescription_goes_to_the_extracted_tab_collapsed_with_a_badge_and_a_link():
    done = ADMIN_PAGE[ADMIN_PAGE.index("async function loadResultsOf"):ADMIN_PAGE.index("async function loadDoc")]
    assert "OPEN.add" not in done                                                           # nothing is opened: the list stays collapsed
    assert "NEWCOUNT++" in done and 'id="ex-badge"' in ADMIN_PAGE and "Read: open in Extracted" in ADMIN_PAGE
    assert "function showTab" in ADMIN_PAGE and "ArrowRight" in ADMIN_PAGE                  # keyboard: arrow keys move between the tabs
