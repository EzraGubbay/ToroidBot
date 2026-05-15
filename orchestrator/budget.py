"""OpenRouter budget guard and per-run usage logging.

Activated automatically when OPENROUTER_API_KEY is present in the environment.
Set OPENROUTER_MIN_BALANCE_USD to control the hard-stop threshold (default $0.50).
Usage is appended as JSONL to ~/.toroidbot/usage.log.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
_LOG_PATH = Path.home() / ".toroidbot" / "usage.log"
_DEFAULT_THRESHOLD_USD = 20.00


class BudgetExhaustedError(RuntimeError):
    """Raised when OpenRouter remaining credits are below the configured minimum."""


def _threshold() -> float:
    raw = os.environ.get("OPENROUTER_MIN_BALANCE_USD", str(_DEFAULT_THRESHOLD_USD))
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_THRESHOLD_USD


async def fetch_balance() -> tuple[float, float | None]:
    """Return (used_usd, limit_usd) from the OpenRouter /auth/key endpoint.

    limit_usd is None when the key has no hard credit limit set.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()

    data = resp.json()["data"]
    used = float(data.get("usage", 0))
    limit = float(data["limit"]) if data.get("limit") is not None else None
    return used, limit


async def guard_budget() -> tuple[float, float | None]:
    """Fetch balance and raise BudgetExhaustedError if remaining credits are too low.

    Returns (used_usd, limit_usd) so the caller can compute the post-run delta.
    """
    used, limit = await fetch_balance()
    if limit is not None:
        remaining = limit - used
        threshold = _threshold()
        if remaining < threshold:
            raise BudgetExhaustedError(
                f"OpenRouter remaining balance (${remaining:.4f}) is below the "
                f"minimum threshold (${threshold:.2f}). "
                "Top up your account or lower OPENROUTER_MIN_BALANCE_USD to continue."
            )
    return used, limit


def log_run(
    *,
    prompt: str,
    event_name: str | None,
    used_before: float,
    used_after: float,
    limit: float | None,
) -> Path:
    """Append one JSONL record to ~/.toroidbot/usage.log and return the log path."""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cost = used_after - used_before
    remaining = (limit - used_after) if limit is not None else None
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_name,
        "prompt": prompt,
        "cost_usd": round(cost, 6),
        "used_before_usd": round(used_before, 6),
        "used_after_usd": round(used_after, 6),
        "limit_usd": limit,
        "remaining_usd": round(remaining, 6) if remaining is not None else None,
    }
    with _LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return _LOG_PATH
