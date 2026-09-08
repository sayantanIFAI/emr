from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..logging import get_logger
from .backends import make_backend

log = get_logger(__name__)
app = FastAPI(title="CDI-Adapter model gateway", version="0.1.0")
_backend = make_backend()


class GenRequest(BaseModel):
    image_b64: str
    prompt: str
    max_tokens: int = 512
    json_schema: dict[str, Any] | None = None


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", **_backend.info(), "configured_backend": settings.mlserve_backend}


@app.post("/vlm/generate")
def vlm_generate(req: GenRequest) -> dict[str, Any]:
    try:
        return _backend.generate(
            req.image_b64, req.prompt, max_tokens=req.max_tokens, json_schema=req.json_schema
        )
    except Exception as exc:  # noqa: BLE001
        log.error("vlm_generate_failed", error=str(exc)[:300])
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc


def main() -> None:
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=settings.mlserve_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
