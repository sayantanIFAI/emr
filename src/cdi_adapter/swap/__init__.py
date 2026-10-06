"""Swap points (SW-S1, SW-S6): every part that could change tomorrow sits behind a small interface, a registry
and ONE setting that picks the implementation.

    from cdi_adapter import swap
    swap.resolve("pdf_renderer")       # the implementation the setting names (built once)
    swap.describe()                    # every swap point: setting, current choice + version, other choices
    swap.check_all()                   # at start-up: a wrong name or a missing library stops the service

Rules this module enforces:

* the **setting** (``CDI_<NAME>``) decides; nothing imports an implementation by name outside its adapter;
* a wrong name stops the service with a message that **names the setting** and lists the choices;
* a missing library stops the service with a message that names the setting and the package to install;
* the **name and version** of what is in use are available for every result (``describe()``/provenance);
* each interface has a **contract test** (tests/test_contracts_unit.py): any implementation must pass it
  before it is registered here.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

from ..config import settings


class SwapConfigError(RuntimeError):
    """A swap-point setting is wrong or its library is missing. The message names the setting."""


@dataclass
class Choice:
    name: str
    factory: Callable[[], Any]
    requires: tuple[str, ...] = ()          # importable modules (the pip package is named in ``install``)
    install: str = ""                       # e.g. 'pip install ".[ocr]"'
    dist: str | None = None                 # distribution whose version is reported
    note: str = ""


@dataclass
class SwapPoint:
    name: str
    setting: str                            # the environment variable that picks the choice, e.g. CDI_PDF_RENDERER
    attr: str                               # the Settings attribute
    doc: str
    custom_ok: bool = False                 # a "package.module:Class" name is accepted (out-of-tree plug-in)
    choices: dict[str, Choice] = field(default_factory=dict)
    _built: dict[str, Any] = field(default_factory=dict, repr=False)

    def current_name(self) -> str:
        v = getattr(settings, self.attr)
        return (v if isinstance(v, str) else str(v)).strip().lower()


POINTS: dict[str, SwapPoint] = {}


def point(name: str, *, setting: str, attr: str, doc: str, custom_ok: bool = False) -> SwapPoint:
    p = POINTS.get(name) or SwapPoint(name, setting, attr, doc, custom_ok)
    POINTS[name] = p
    return p


def choice(point_name: str, name: str, factory: Callable[[], Any], *, requires: tuple[str, ...] = (), install: str = "",
           dist: str | None = None, note: str = "") -> None:
    POINTS[point_name].choices[name.lower()] = Choice(name.lower(), factory, requires, install, dist, note)


def _missing(c: Choice) -> str | None:
    for mod in c.requires:
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001 - ImportError, or a broken native library
            return mod
    return None


def _version(c: Choice) -> str | None:
    if not c.dist:
        return None
    try:
        return metadata.version(c.dist)
    except metadata.PackageNotFoundError:
        return None


def _selected(p: SwapPoint) -> Choice:
    name = p.current_name()
    c = p.choices.get(name)
    if c is None and p.custom_ok and ":" in name:
        return Choice(name, lambda: None, note="custom class")
    if c is None:
        raise SwapConfigError(f"{p.setting}={name!r} is not a known {p.name}; choose one of: "
                              f"{', '.join(sorted(p.choices)) or '(none registered)'}")
    gone = _missing(c)
    if gone:
        raise SwapConfigError(f"{p.setting}={name!r} needs the library {gone!r}, which is not installed"
                              + (f" ({c.install})" if c.install else ""))
    return c


def resolve(point_name: str) -> Any:
    """The implementation the setting names, built once per process and per choice."""
    ensure_loaded()
    p = POINTS[point_name]
    c = _selected(p)
    if c.name not in p._built:
        p._built[c.name] = c.factory()
    return p._built[c.name]


def reset(point_name: str | None = None) -> None:
    """Forget built implementations (tests, or after a setting changes)."""
    for p in ([POINTS[point_name]] if point_name else POINTS.values()):
        p._built.clear()


def check_all() -> list[str]:
    """Start-up: every swap point must resolve. Raises :class:`SwapConfigError` on the first problem."""
    ensure_loaded()
    ok = []
    for p in POINTS.values():
        _selected(p)
        ok.append(f"{p.name}={p.current_name()}")
    return ok


def describe() -> list[dict[str, Any]]:
    """For the health page: each swap point, the setting, the current choice and version, the other choices."""
    ensure_loaded()
    out = []
    for p in POINTS.values():
        cur = p.choices.get(p.current_name())
        out.append({
            "swap_point": p.name, "setting": p.setting, "about": p.doc,
            "current": p.current_name(), "version": _version(cur) if cur else None,
            "usable": bool(cur) and _missing(cur) is None,
            "available": sorted(p.choices),
        })
    return out


def current_version(point_name: str) -> str:
    """``"<name> <version>"`` of the choice in use, for provenance."""
    ensure_loaded()
    p = POINTS[point_name]
    c = p.choices.get(p.current_name())
    v = _version(c) if c else None
    return f"{p.current_name()} {v}" if v else p.current_name()


_loaded = False


def ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        from . import points  # noqa: F401  (registers every swap point)
