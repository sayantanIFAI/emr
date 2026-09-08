# ADR 0001 — CDI is an adapter, not an EMR replacement

**Status:** accepted · **Date:** 2026-09-07

## Context

The hospital runs a legacy HMS with no discrete clinical data — only scanned documents.
Options considered: (a) replace the HMS with an open-source EMR (OpenMRS/Bahmni), (b) build
a greenfield EMR, (c) build an AI adapter that derives structured data from the scans and
exposes it in standard form.

## Decision

Build **(c)**. The adapter:

- treats the legacy database as **read-only** — never alters its schema or rows;
- keeps its **own** PostgreSQL store (the schema in `db/schema.sql`);
- ingests scans via a connector (folder-watch first; CDC later);
- produces two synchronized outputs per care context: **relational rows**
  (`clinical_fact`, `fhir_resource`, …) and **FHIR R4 JSON** (ABDM `Composition` bundles);
- can optionally write back a generated summary document and expose a FHIR façade, but the
  hospital keeps using its legacy system as the system of record for workflow.

## Consequences

- Low disruption; the hospital's staff workflow is unchanged.
- The adapter must solve identity resolution (MPI) itself since the legacy MRN is the only
  link and it appears only as unverified text on some documents.
- Every derived fact needs provenance back to the pixel — this is a first-class part of the
  schema, not an add-on.
- Portability to other countries is achieved by swapping two packages: the **Terminology
  Package** and the **Profile/IG + document-format Package**.
