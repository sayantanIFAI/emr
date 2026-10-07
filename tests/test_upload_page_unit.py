"""The admin upload page: four summary tables sit above the JSON, several batches can run at once, and the
camera button has both paths (phone camera app / live view on a computer)."""
from __future__ import annotations

from cdi_adapter.webapp.upload_page import ADMIN_PAGE


def test_the_four_tables_are_defined_and_sit_above_the_json():
    for ident in ("tbl-patient", "tbl-doctor", "tbl-booking", "tbl-labs"):
        assert f'"{ident}"' in ADMIN_PAGE                       # the page script draws each table under this id
    # the container the tables are drawn into comes before the JSON block
    assert ADMIN_PAGE.index('id="summary"') < ADMIN_PAGE.index('id="resjson"')
    # and they are drawn in this order
    order = [ADMIN_PAGE.index(f'"{i}"') for i in ("tbl-patient", "tbl-doctor", "tbl-booking", "tbl-labs")]
    assert order == sorted(order)


def test_the_tables_are_drawn_from_the_result_and_every_value_is_escaped():
    assert "summaryHtml(r)" in ADMIN_PAGE and 'innerHTML=summaryHtml' in ADMIN_PAGE
    # the vcell() path never puts a raw value into the page
    vcell = ADMIN_PAGE[ADMIN_PAGE.index("function vcell"):ADMIN_PAGE.index("function rowsTable")]
    assert "esc(" in vcell


def test_several_uploads_can_run_at_once_the_form_is_not_locked():
    assert "LOCKED" not in ADMIN_PAGE
    assert "JOBS.unshift" in ADMIN_PAGE and 'id="jobs"' in ADMIN_PAGE


def test_the_camera_button_has_a_phone_path_and_a_computer_path():
    assert 'capture="environment"' in ADMIN_PAGE                       # phone / tablet: the device's camera app
    assert "getUserMedia" in ADMIN_PAGE and 'id="camdlg"' in ADMIN_PAGE  # computer: live camera view
    assert "pointer: coarse" in ADMIN_PAGE
