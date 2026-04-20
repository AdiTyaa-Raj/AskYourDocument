"""CORS middleware configuration.

The frontend (dev server) typically runs on a different origin (e.g. localhost:3000),
so the API must emit the appropriate CORS headers. This module centralizes CORS
configuration and keeps it adjustable via env/config.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def _normalize_origin(origin: str) -> str:
    value = origin.strip()
    if not value:
        return ""
    if value == "*":
        return value
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    return value


def _parse_allow_origins(value: str) -> List[str]:
    origins = [part.strip() for part in value.split(",")]
    normalized = [_normalize_origin(origin) for origin in origins]
    return [origin for origin in normalized if origin]


def _load_allow_origins_from_env() -> List[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return []
    return _parse_allow_origins(raw)


def _load_allow_origins_from_file(cors_json_path: str) -> List[str]:
    try:
        with open(cors_json_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []

    allow_origins = payload.get("allow_origins")
    if isinstance(allow_origins, list) and all(isinstance(x, str) for x in allow_origins):
        normalized = [_normalize_origin(x) for x in allow_origins]
        return [x for x in normalized if x]
    return []


def _load_allow_origin_regex_from_env() -> Optional[str]:
    value = os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    return value or None


def _load_allow_origin_regex_from_file(cors_json_path: str) -> Optional[str]:
    try:
        with open(cors_json_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

    value = payload.get("allow_origin_regex")
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def apply_cors_middleware(app: FastAPI) -> None:
    """Apply CORS middleware to the app.

    Precedence:
    1) `CORS_ALLOW_ORIGINS` env var (comma-separated)
    2) `cors.json` in the backend root (AskYourDocument/cors.json)
    3) Safe dev default (`http://localhost:3000`, `http://127.0.0.1:3000`)
    """

    cors_json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cors.json")

    allow_origins = _load_allow_origins_from_env()
    if not allow_origins:
        allow_origins = _load_allow_origins_from_file(cors_json_path)
    if not allow_origins:
        allow_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

    allow_origin_regex = _load_allow_origin_regex_from_env()
    if not allow_origin_regex:
        allow_origin_regex = _load_allow_origin_regex_from_file(cors_json_path)
    if not allow_origin_regex:
        # Support common local dev hosts on any port/protocol (Chrome/Brave/Dia/etc).
        allow_origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    allow_all = len(allow_origins) == 1 and allow_origins[0] == "*"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )
