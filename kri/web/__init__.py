"""KRI web app (FastAPI) — Sprint-1 ingest API. See :mod:`kri.web.app`."""

from __future__ import annotations

from .app import SubmitRequest, create_app

__all__ = ["create_app", "SubmitRequest"]
