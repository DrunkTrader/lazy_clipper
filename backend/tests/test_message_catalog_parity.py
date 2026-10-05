import json
from pathlib import Path

from backend.app.errors import (
    CAPTION_MESSAGES,
    CLIENT_MESSAGES,
    INTERRUPTED_MESSAGES,
    LIMIT_MESSAGES,
    PUBLIC_MESSAGES,
    TIMEOUT_MESSAGES,
)


def test_frontend_error_catalog_matches_backend_public_messages():
    catalog_path = Path(__file__).parents[2] / "frontend" / "src" / "error-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["stageMessages"] == PUBLIC_MESSAGES
    assert catalog["interruptedMessages"] == INTERRUPTED_MESSAGES
    assert catalog["captionMessages"] == CAPTION_MESSAGES
    assert catalog["timeoutMessages"] == TIMEOUT_MESSAGES
    assert catalog["limitMessages"] == LIMIT_MESSAGES
    assert catalog["clientMessages"] == CLIENT_MESSAGES
