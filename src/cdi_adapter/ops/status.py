"""The health page (ENT-S4) and the swap-point list (SW-S1): one plain HTML page for the admin, built on the
server from live probes. Nothing on it is typed in by hand."""
from __future__ import annotations

import html
from typing import Any

from .. import swap
from ..config import settings


def collect(sess: Any) -> dict[str, Any]:
    from .. import storage
    from ..compliance import models
    from ..db import ping as db_ping
    from ..listener.health import health

    def probe(fn: Any) -> bool:
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def gateway() -> bool:
        from ..ml.client import get_client

        return get_client().healthz().get("status") == "ok"

    def ocrhost() -> bool:
        import httpx

        return bool(settings.ocrhost_url) and httpx.get(settings.ocrhost_url.rstrip("/") + "/healthz", timeout=3).status_code == 200

    out: dict[str, Any] = {
        "components": {"database": probe(db_ping), "object_store": probe(storage.ping),
                       "job_queue": probe(lambda: swap.resolve("job_queue").ping()),
                       "model_gateway": probe(gateway), "ocr_host": probe(ocrhost)},
        "swap_points": swap.describe(),
        "models": [{"role": m["role"], "model_id": m["model_id"], "revision": m["revision"], "status": m["status"],
                    "licence": m["licence"]["name"]} for m in models.load_registry()["models"]],
    }
    try:
        out["listener"] = health(sess)
    except Exception:  # noqa: BLE001
        out["listener"] = None
    return out


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def page(data: dict[str, Any]) -> str:
    comp = "".join(f"<tr><td>{_e(k.replace('_', ' '))}</td><td class={'ok' if v else 'bad'}>{'working' if v else 'NOT answering'}</td></tr>"
                   for k, v in data["components"].items())
    swaps = "".join(
        f"<tr><td>{_e(s['swap_point'])}</td><td><code>{_e(s['setting'])}</code></td><td><b>{_e(s['current'])}</b> {_e(s['version'] or '')}"
        f"{'' if s['usable'] else ' <span class=bad>(library missing)</span>'}</td><td>{_e(', '.join(s['available']))}</td></tr>"
        for s in data["swap_points"])
    models = "".join(f"<tr><td>{_e(m['role'])}</td><td>{_e(m['model_id'])}</td><td><code>{_e(m['revision'][:12])}</code></td>"
                     f"<td>{_e(m['status'])}</td><td>{_e(m['licence'])}</td></tr>" for m in data["models"])
    lst = data.get("listener")
    if lst:
        lis = (f"<p class={'bad' if lst['stalled'] else 'ok'}>{'STALLED or stopped' if lst['stalled'] else 'polling'}"
               f"{' - ' + _e(lst['stopped_reason']) if lst['stopped_reason'] else ''}</p><ul>"
               f"<li>last good poll: {_e(lst['last_good_poll_at'] or 'never')}</li><li>waiting: {lst['files_waiting']}</li>"
               f"<li>in error: {lst['files_in_error']}, in quarantine: {lst['files_in_quarantine']}</li>"
               f"<li>oldest waiting: {_e(lst['oldest_waiting_age_seconds'])} s</li></ul>")
    else:
        lis = "<p>The listener is not set up on this system.</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>System status</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f4f7fc;color:#0f172a}}main{{max-width:960px;margin:0 auto;padding:16px}}
h1{{font-size:22px}}h2{{font-size:17px;margin-top:24px}}table{{border-collapse:collapse;width:100%;background:#fff}}
td,th{{border:1px solid #d6deec;padding:6px 10px;text-align:left;vertical-align:top}}th{{background:#eef4ff}}
.ok{{color:#0a7a3d;font-weight:600}}.bad{{color:#b42318;font-weight:600}}code{{background:#eef4ff;padding:1px 4px;border-radius:4px}}
@media(max-width:600px){{td,th{{padding:4px 6px;font-size:13px}}}}</style></head><body><main>
<h1>System status</h1><p><a href="/">Back to upload</a> &middot; <a href="/metrics">Metrics (Prometheus text)</a></p>
<h2>Components</h2><table><tr><th>Part</th><th>State</th></tr>{comp}</table>
<h2>File listener</h2>{lis}
<h2>Models in use (registry)</h2><table><tr><th>Role</th><th>Model</th><th>Revision</th><th>Status</th><th>Licence</th></tr>{models}</table>
<h2>Swap points: what can be changed by a setting</h2>
<table><tr><th>Part</th><th>Setting</th><th>In use</th><th>Other choices</th></tr>{swaps}</table>
</main></body></html>"""
