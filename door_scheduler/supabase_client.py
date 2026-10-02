"""Supabase client helper for auth / admin features."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def supabase_configured() -> bool:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    return bool(url and key)


@lru_cache(maxsize=1)
def get_supabase_client() -> Any:
    """
    Return a Supabase client using the service role key.

    Service role is intentional: this Streamlit app authenticates users itself
    (email-only) and must read/write app_users + login_events server-side.
    Never expose SUPABASE_SERVICE_ROLE_KEY to the browser.
    """
    if not supabase_configured():
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY in .env."
        )
    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency: pip install supabase"
        ) from exc

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return create_client(url, key)


def reset_supabase_client_cache() -> None:
    get_supabase_client.cache_clear()
