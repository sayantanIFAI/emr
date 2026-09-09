"""ABDM / NRCeS conformance check for a generated document Bundle.

Two validators, best available wins:
  * `hapi`   - the HL7 `org.hl7.fhir` validator CLI (`validator_cli.jar`) with the
               `nrces.fhir.r4.ndhm` IG package, if `java` and the jar are present.
  * `python-conformance` - a structural + ABDM-rule checker implemented here.
                           Not a substitute for the HL7 validator, but it enforces
                           the rules that actually break ABDM ingestion.

Returns {ok: bool, validator: str, issues: [{severity, path, msg}]}.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from ..logging import get_logger
from .resources import NRCES

log = get_logger(__name__)

ABHA_SYS = "https://healthid.ndhm.gov.in"
ABHA_ADDR_SYS = "https://healthid.ndhm.gov.in/address"

# resource types every artifact MUST carry
_REQUIRED = {
    "OPConsultRecord": {"Composition", "Patient", "Encounter"},
    "PrescriptionRecord": {"Composition", "Patient", "MedicationRequest"},
    "DiagnosticReportRecord": {"Composition", "Patient", "DiagnosticReport"},
    "DischargeSummaryRecord": {"Composition", "Patient", "Encounter"},
    "WellnessRecord": {"Composition", "Patient"},
    "HealthDocumentRecord": {"Composition", "Patient"},
    "ImmunizationRecord": {"Composition", "Patient"},
}

_VALIDATOR_JAR = os.environ.get("CDI_FHIR_VALIDATOR_JAR", "/workspace/tools/validator_cli.jar")
_IG = os.environ.get("CDI_IG_PACKAGE", "nrces.fhir.r4.ndhm#7.0.0")


def validate(bundle: dict[str, Any], artifact: str) -> dict[str, Any]:
    if shutil.which("java") and os.path.exists(_VALIDATOR_JAR):
        try:
            return _hapi(bundle)
        except Exception as exc:  # noqa: BLE001
            log.warning("hapi_validate_failed", error=str(exc)[:200])
    return _python_conformance(bundle, artifact)


# --------------------------------------------------------------------------- #
def _hapi(bundle: dict[str, Any]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(bundle, f)
        path = f.name
    try:
        out = subprocess.run(
            ["java", "-jar", _VALIDATOR_JAR, path, "-version", "4.0.1", "-ig", _IG, "-output-style", "json"],
            capture_output=True, text=True, timeout=180)
        try:
            oo = json.loads(out.stdout)
            issues = [{"severity": i.get("severity"), "path": _loc(i), "msg": _txt(i)}
                      for i in oo.get("issue", [])]
        except Exception:  # noqa: BLE001 - fall back to text scan
            issues = [{"severity": "error", "path": "", "msg": line}
                      for line in out.stdout.splitlines() if " error " in line.lower()]
        ok = not any(i["severity"] == "error" for i in issues)
        return {"ok": ok, "validator": "hapi", "issues": issues}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _loc(issue: dict[str, Any]) -> str:
    e = issue.get("expression") or issue.get("location") or []
    return e[0] if isinstance(e, list) and e else ""


def _txt(issue: dict[str, Any]) -> str:
    d = issue.get("details") or {}
    return d.get("text") or issue.get("diagnostics") or ""


# --------------------------------------------------------------------------- #
def _python_conformance(bundle: dict[str, Any], artifact: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def err(path: str, msg: str) -> None:
        issues.append({"severity": "error", "path": path, "msg": msg})

    def warn(path: str, msg: str) -> None:
        issues.append({"severity": "warning", "path": path, "msg": msg})

    if bundle.get("resourceType") != "Bundle":
        err("Bundle", "root is not a Bundle")
        return {"ok": False, "validator": "python-conformance", "issues": issues}
    if bundle.get("type") != "document":
        err("Bundle.type", f"must be 'document', got {bundle.get('type')!r}")
    if not bundle.get("identifier", {}).get("value"):
        err("Bundle.identifier", "document Bundle needs a persistent identifier")
    if not bundle.get("timestamp"):
        err("Bundle.timestamp", "missing")
    if NRCES + "DocumentBundle" not in bundle.get("meta", {}).get("profile", []):
        warn("Bundle.meta.profile", "DocumentBundle profile not asserted")

    entries = bundle.get("entry", [])
    if not entries:
        err("Bundle.entry", "empty")
        return {"ok": False, "validator": "python-conformance", "issues": issues}

    first = entries[0].get("resource", {})
    if first.get("resourceType") != "Composition":
        err("Bundle.entry[0]", "first entry must be a Composition")

    full_urls, res_ids, types = set(), set(), []
    for i, e in enumerate(entries):
        fu = e.get("fullUrl")
        r = e.get("resource", {})
        if not fu:
            err(f"entry[{i}]", "missing fullUrl")
        else:
            full_urls.add(fu)
        rt = r.get("resourceType")
        types.append(rt)
        rid = r.get("id")
        if rid:
            res_ids.add(f"urn:uuid:{rid}")
        if not r.get("meta", {}).get("profile"):
            warn(f"entry[{i}].{rt}", "no meta.profile (NRCeS profile not asserted)")
        elif not any(p.startswith(NRCES) for p in r["meta"]["profile"]):
            warn(f"entry[{i}].{rt}", "meta.profile is not an nrces.in profile")

    typeset = set(t for t in types if t)
    for need in _REQUIRED.get(artifact, {"Composition", "Patient"}):
        if need not in typeset:
            err("Bundle", f"{artifact} requires a {need} resource")

    # every reference must resolve to a fullUrl in the bundle
    resolvable = full_urls | res_ids

    def walk_refs(node: Any, path: str) -> None:
        if isinstance(node, dict):
            ref = node.get("reference")
            if isinstance(ref, str) and ref.startswith("urn:uuid:") and ref not in resolvable:
                err(path, f"dangling reference {ref}")
            for k, v in node.items():
                walk_refs(v, f"{path}.{k}")
        elif isinstance(node, list):
            for j, v in enumerate(node):
                walk_refs(v, f"{path}[{j}]")

    walk_refs(bundle, "Bundle")

    # ---- Patient identifier rules (ABHA + a separate MRN) --------------
    pat = next((e["resource"] for e in entries if e["resource"].get("resourceType") == "Patient"), None)
    if pat:
        ids = pat.get("identifier", [])
        systems = [x.get("system") for x in ids]
        codes = [c.get("code") for x in ids for c in x.get("type", {}).get("coding", [])]
        has_abha = ("ABHA" in codes) or any(s in (ABHA_SYS, ABHA_ADDR_SYS) for s in systems)
        has_other = any(s not in (ABHA_SYS, ABHA_ADDR_SYS) for s in systems if s)
        if not ids:
            err("Patient.identifier", "Patient has no identifier")
        if not has_abha:
            warn("Patient.identifier", "no ABHA identifier present (system healthid.ndhm.gov.in / type ABHA)")
        if not has_other:
            err("Patient.identifier", "no non-ABHA identifier (hospital MRN) - ABHA must not be the only id")
        if not pat.get("name"):
            err("Patient.name", "missing")
        if not pat.get("gender"):
            warn("Patient.gender", "missing")

    # ---- Composition structure --------------------------------------
    if first.get("resourceType") == "Composition":
        if not first.get("type", {}).get("coding") and not first.get("type", {}).get("text"):
            err("Composition.type", "must be a coded CodeableConcept")
        if not first.get("subject", {}).get("reference"):
            err("Composition.subject", "missing Patient reference")
        if not first.get("section"):
            warn("Composition.section", "no sections")
        for si, s in enumerate(first.get("section", [])):
            if not s.get("entry") and not s.get("text"):
                warn(f"Composition.section[{si}]", f"section {s.get('title')!r} is empty")

    # ---- Observation vitals need a value or a dataAbsentReason -----
    for e in entries:
        r = e["resource"]
        if r.get("resourceType") == "Observation":
            if not any(k in r for k in ("valueQuantity", "valueString", "valueCodeableConcept",
                                        "component", "dataAbsentReason")):
                err(f"Observation/{r.get('id')}", "no value and no dataAbsentReason")
        if r.get("resourceType") == "Condition" and not (
                r.get("code", {}).get("coding") or r.get("code", {}).get("text")):
            err(f"Condition/{r.get('id')}", "code missing")

    ok = not any(i["severity"] == "error" for i in issues)
    return {"ok": ok, "validator": "python-conformance", "issues": issues}
