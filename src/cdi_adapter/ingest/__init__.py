"""Stage S1 - ingest & normalize.

Reads scanned documents from the legacy HMS (folder-watch first), stores the
original bytes immutably, splits multi-page PDFs, deskews/denoises each page,
and records ``source_document`` + ``document_page`` rows. Nothing clinical is
interpreted here - this stage only produces faithful, addressable page images.
"""
