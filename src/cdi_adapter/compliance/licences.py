"""Licence audit, SBOM and NOTICE (ENT-S1, SW-S7).

Reads every installed Python distribution (what is actually deployed, not what a file says), sorts each
licence into ``allowed`` / ``review`` / ``banned`` / ``unknown`` by ``compliance/licence_policy.json``, and
produces:

* the **SBOM** (a CycloneDX-style JSON list of components with licence and decision);
* the **NOTICE** text (every component whose licence carries a notice / attribution duty is listed);
* a **diff** of two SBOMs (what a swap added, removed or re-licensed);
* a **gate**: exit code 1 when a component is banned, unknown, or in ``review`` without a recorded
  decision (``--strict`` also refuses decisions that are only ``proposed``).

This is a tool for the people who decide, not legal advice: counsel signs off the policy and every
``review`` decision (recorded under ``decisions`` with who/when/why).

    python -m cdi_adapter.compliance.licences --check [--strict] [--sbom sbom.json] [--notice NOTICE]
    python -m cdi_adapter.compliance.licences --diff old-sbom.json new-sbom.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parent / "licence_policy.json"
SELF = {"cdi-adapter"}


@dataclass
class Component:
    name: str
    version: str
    licence: str                 # as the package states it ("" = not stated)
    family: str | None = None    # the policy family it matched (e.g. "MIT")
    state: str = "unknown"       # allowed | review | banned | unknown
    decision: str | None = None  # none | proposed | approved | rejected (for review / unknown)
    notice_duty: bool = False
    reason: str = ""


@dataclass
class Report:
    components: list[Component] = field(default_factory=list)

    def by_state(self, state: str) -> list[Component]:
        return [c for c in self.components if c.state == state]


def load_policy(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))


def _licence_text(md: Any) -> tuple[str, list[str]]:
    expr = (md.get("License-Expression") or "").strip()
    free = (md.get("License") or "").strip()
    first = (expr or free).splitlines()[0][:120] if (expr or free) else ""
    classifiers = [c.split("::")[-1].strip() for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
    return first, classifiers


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def classify(licence: str, classifiers: list[str], policy: dict[str, Any]) -> tuple[str, str | None, str]:
    """``(state, family, reason)``. A dual licence ("A OR B") is allowed if ANY branch is allowed; a
    compound ("A AND B") must have every part allowed. Banned wins over everything else."""
    if licence and _norm(licence) not in ("unknown", ""):
        first = _classify_text(_norm(licence), licence, policy)
        if first[0] != "unknown":                       # a clearly stated licence beats generic classifier text
            return first
    text = _norm(" ; ".join([licence, *classifiers]))
    if not text or text in ("unknown", "other/proprietary license"):
        return "unknown", None, "the package does not state a licence"
    return _classify_text(text, licence or "; ".join(classifiers), policy)


def _classify_text(text: str, shown: str, policy: dict[str, Any]) -> tuple[str, str | None, str]:
    for pat in policy["banned"]:
        if re.search(pat["pattern"], text, re.IGNORECASE):
            # "LGPL" must not be caught by a GPL pattern: patterns are written with word boundaries
            return "banned", pat["name"], pat["why"]
    parts_and = re.split(r"\band\b|;|,|/", text)
    def part_state(part: str) -> tuple[str, str | None]:
        branches = re.split(r"\bor\b", part)
        best: tuple[str, str | None] = ("unknown", None)
        for b in branches:
            b = b.strip(" ()")
            if not b:
                continue
            for fam in policy["allowed"]:
                if re.search(fam["pattern"], b, re.IGNORECASE):
                    return "allowed", fam["name"]
            for fam in policy["review"]:
                if re.search(fam["pattern"], b, re.IGNORECASE):
                    best = ("review", fam["name"])
        return best
    generic = re.compile(r"^\s*(osi approved|freely distributable|dfsg approved|dependency licenses|see license file|license)\s*$")
    states = [part_state(p) for p in parts_and if p.strip() and not generic.match(p)]
    if states and all(s == "allowed" for s, _ in states):
        return "allowed", states[0][1], ""
    if any(s == "unknown" for s, _ in states):
        return "unknown", None, f"licence text not recognised: {shown}"[:160]
    fam = next(f for s, f in states if s == "review")
    return "review", fam, "a licence with conditions: a recorded decision is required"


def product_closure(root: str = "cdi-adapter") -> list[Any]:
    """The distributions the PRODUCT needs: ``root`` and everything it requires (all its runtime extras, not ``dev``),
    followed transitively. A machine also carries operating-system Python packages (apt, PyGObject ...) that are not
    ours and are not shipped; the SBOM and the gate are about what we ship."""
    from packaging.requirements import Requirement

    def norm_name(n: str) -> str:
        return re.sub(r"[-_.]+", "-", n).lower()

    try:
        top = metadata.distribution(root)
    except metadata.PackageNotFoundError:
        return list(metadata.distributions())
    extras = [e for e in (top.metadata.get_all("Provides-Extra") or []) if e != "dev"]
    out: dict[str, Any] = {}
    stack = [(top, [""] + extras)]
    while stack:
        dist, wanted = stack.pop()
        key = norm_name(dist.metadata["Name"] or "")
        if key in out:
            continue
        out[key] = dist
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except Exception:  # noqa: BLE001
                continue
            if req.marker is not None and not any(req.marker.evaluate({"extra": e}) for e in wanted):
                continue
            try:
                dep = metadata.distribution(req.name)
            except metadata.PackageNotFoundError:
                continue                                   # not installed here: nothing to audit
            stack.append((dep, [""] + sorted(req.extras)))
    return list(out.values())


def audit(policy: dict[str, Any] | None = None, distributions: Any = None) -> Report:
    policy = policy or load_policy()
    dists = distributions if distributions is not None else product_closure()
    seen: dict[str, Component] = {}
    for d in dists:
        md = d.metadata
        name = (md["Name"] or "").strip()
        if not name or name.lower() in SELF:
            continue
        key = name.lower().replace("_", "-")
        lic, cls = _licence_text(md)
        state, fam, why = classify(lic, cls, policy)
        decisions = policy.get("decisions", {})
        dec = decisions.get(key) or next((v for k, v in decisions.items() if k.endswith("*") and key.startswith(k[:-1])), None)
        decision = None
        if state in ("review", "unknown"):
            decision = dec["status"] if dec else "none"
            if dec:
                why = dec.get("reason", why)
        c = Component(name, d.version or "", lic or ("; ".join(cls) if cls else ""), fam, state, decision,
                      notice_duty=bool(fam and fam in policy.get("notice_duty", [])) or state == "review", reason=why)
        if state == "banned" and dec and dec["status"] == "approved":
            c.decision = "approved"                       # a banned licence needs an explicit approved exception
        seen[key] = c
    return Report(sorted(seen.values(), key=lambda c: c.name.lower()))


def problems(report: Report, *, strict: bool = False, policy: dict[str, Any] | None = None) -> list[str]:
    """Every reason the gate fails (empty = pass)."""
    out: list[str] = []
    for c in report.components:
        tag = f"{c.name} {c.version} ({c.licence or 'no licence stated'})"
        if c.state == "banned" and c.decision != "approved":
            out.append(f"BANNED: {tag}: {c.reason}")
        elif c.state in ("review", "unknown"):
            if c.decision in (None, "none"):
                out.append(f"NEEDS A DECISION: {tag}: {c.reason}")
            elif c.decision == "rejected":
                out.append(f"REJECTED: {tag}: {c.reason}")
            elif c.decision == "proposed" and strict:
                out.append(f"NOT YET APPROVED (proposed only): {tag}")
    return out


def warnings(report: Report) -> list[str]:
    return [f"proposed, awaiting counsel: {c.name} {c.version} ({c.licence})"
            for c in report.components if c.decision == "proposed"]


def sbom(report: Report) -> dict[str, Any]:
    """A CycloneDX-style document: stable order, no timestamps (the same install gives the same bytes)."""
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"component": {"type": "application", "name": "cdi-adapter"}, "note": "Python distributions installed in this environment"},
        "components": [{"type": "library", "name": c.name, "version": c.version,
                        "licenses": [{"license": {"name": c.licence or "NOT STATED"}}],
                        "properties": [{"name": "cdi:state", "value": c.state}, {"name": "cdi:family", "value": c.family or ""},
                                       {"name": "cdi:decision", "value": c.decision or ""}]}
                       for c in report.components],
    }


def notice(report: Report, policy: dict[str, Any] | None = None) -> str:
    """The NOTICE file: the attribution every notice-carrying licence requires, generated from the install."""
    policy = policy or load_policy()
    lines = ["CDI-Adapter: third-party notices", "=" * 32, "",
             "This product includes the open-source components below, each under its own licence.",
             "The full licence texts ship with each package (its dist-info folder). Generated from the",
             "installed environment; do not edit by hand.", ""]
    for c in report.components:
        if c.state in ("allowed", "review") and (c.notice_duty or c.state == "review"):
            lines.append(f"- {c.name} {c.version}: {c.licence or c.family}")
    lines += ["", "Models", "-" * 6]
    try:
        from .models import load_registry

        for m in load_registry()["models"]:
            if m.get("status") in ("champion", "candidate", "fallback"):
                lines.append(f"- {m['model_id']} @ {m['revision'][:12]}: {m['licence']['name']}"
                             + (f"  ({m['licence']['notice_line']})" if m['licence'].get("notice_line") else ""))
    except Exception as exc:  # noqa: BLE001 - a missing registry must not hide the package notices
        lines.append(f"(model register not readable: {type(exc).__name__})")
    return "\n".join(lines) + "\n"


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    """What a swap changed: added / removed / re-versioned / re-licensed packages (SW-S7 AC2)."""
    def comp(b: dict[str, Any]) -> dict[str, tuple[str, str]]:
        return {c["name"].lower(): (c["version"], c["licenses"][0]["license"]["name"]) for c in b["components"]}
    a, b = comp(old), comp(new)
    return {
        "added": sorted(f"{k} {b[k][0]} ({b[k][1]})" for k in b.keys() - a.keys()),
        "removed": sorted(f"{k} {a[k][0]}" for k in a.keys() - b.keys()),
        "version_changed": sorted(f"{k}: {a[k][0]} -> {b[k][0]}" for k in a.keys() & b.keys() if a[k][0] != b[k][0]),
        "licence_changed": sorted(f"{k}: {a[k][1]} -> {b[k][1]}" for k in a.keys() & b.keys() if a[k][1] != b[k][1]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="licence audit, SBOM and NOTICE for the installed environment")
    ap.add_argument("--check", action="store_true", help="exit 1 if a component is banned / unknown / undecided")
    ap.add_argument("--strict", action="store_true", help="also refuse decisions that are only 'proposed'")
    ap.add_argument("--sbom", help="write the SBOM JSON here")
    ap.add_argument("--notice", help="write the NOTICE file here")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="compare two SBOM files")
    a = ap.parse_args(argv)
    if a.diff:
        d = diff(json.loads(Path(a.diff[0]).read_text("utf-8")), json.loads(Path(a.diff[1]).read_text("utf-8")))
        print(json.dumps(d, indent=2))
        return 1 if (d["added"] or d["licence_changed"]) and a.check else 0
    policy = load_policy()
    rep = audit(policy)
    if a.sbom:
        Path(a.sbom).write_text(json.dumps(sbom(rep), indent=2) + "\n", encoding="utf-8")
    if a.notice:
        Path(a.notice).write_text(notice(rep, policy), encoding="utf-8")
    counts = {s: len(rep.by_state(s)) for s in ("allowed", "review", "banned", "unknown")}
    print(f"{len(rep.components)} components: {counts}")
    for w in warnings(rep):
        print("warning:", w)
    bad = problems(rep, strict=a.strict, policy=policy)
    for p in bad:
        print(p, file=sys.stderr)
    return 1 if (bad and a.check) else 0


if __name__ == "__main__":
    raise SystemExit(main())
