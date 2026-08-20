"""FastAPI entrypoint for Voyage AI.

The implementation currently lives in ``app.services.chat_service`` so existing
imports such as ``from app.main import app`` and ``uvicorn app.main:app`` keep
working while the backend is organised into a cleaner module structure.
"""

from __future__ import annotations

import sys

from app.services import chat_service as _chat_service


# Keep existing app.main imports working.
sys.modules[__name__] = _chat_service
