"""
services/hubspot_api_service.py
--------------------------------
Thin wrapper around the HubSpot CRM v3 REST API.

Responsibilities:
  - Authenticate every request with a Bearer token.
  - Paginate through all deals using HubSpot's cursor-based `after` parameter.
  - Honour the 150 req / 10 s rate limit with adaptive back-off.
  - Surface actionable errors for upstream callers.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Generator

import requests
from requests import Response

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

HUBSPOT_API_BASE_URL: str = os.getenv(
    "HUBSPOT_API_BASE_URL", "https://api.hubapi.com"
)
DEALS_ENDPOINT: str = f"{HUBSPOT_API_BASE_URL}/crm/v3/objects/deals"

# Properties we want HubSpot to return for every deal.
DEAL_PROPERTIES: list[str] = [
    "dealname",
    "amount",
    "dealstage",
    "closedate",
    "pipeline",
    "hubspot_owner_id",
    "createdate",
    "hs_lastmodifieddate",
]

# Rate-limit budget: 150 requests per 10 seconds (HubSpot default tier).
_RATE_WINDOW_SECONDS: float = 10.0
_RATE_MAX_REQUESTS: int = 140  # stay safely under the hard limit
_PAGE_SIZE: int = 100  # HubSpot maximum per page


class HubSpotAPIError(Exception):
    """Raised when the HubSpot API returns a non-recoverable error."""


class HubSpotRateLimitError(HubSpotAPIError):
    """Raised when the 429 back-off budget is exhausted."""


# ─── Rate-limiter state ───────────────────────────────────────────────────────

_request_timestamps: list[float] = []


def _throttle() -> None:
    """
    Block if we have issued _RATE_MAX_REQUESTS within the last
    _RATE_WINDOW_SECONDS seconds.  Uses a sliding-window algorithm so we
    never need an external library.
    """
    global _request_timestamps

    now = time.monotonic()
    # Drop timestamps older than the window
    _request_timestamps = [
        t for t in _request_timestamps if now - t < _RATE_WINDOW_SECONDS
    ]

    if len(_request_timestamps) >= _RATE_MAX_REQUESTS:
        oldest = _request_timestamps[0]
        sleep_for = _RATE_WINDOW_SECONDS - (now - oldest) + 0.05
        logger.debug("Rate-limit throttle: sleeping %.2fs", sleep_for)
        time.sleep(max(sleep_for, 0))

    _request_timestamps.append(time.monotonic())


# ─── Core API client ──────────────────────────────────────────────────────────


class HubSpotAPIService:
    """
    Stateless client for the HubSpot CRM v3 API.

    Usage::

        service = HubSpotAPIService()
        for deal in service.get_deals():
            process(deal)
    """

    def __init__(self, access_token: str | None = None) -> None:
        token = access_token or os.getenv("HUBSPOT_ACCESS_TOKEN")
        if not token:
            raise ValueError(
                "HubSpot access token not found. "
                "Set HUBSPOT_ACCESS_TOKEN in your environment or .env file."
            )
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get(self, url: str, params: dict | None = None) -> dict:
        """
        Perform a single authenticated GET request with adaptive retry logic
        for transient errors (429, 5xx).
        """
        _throttle()

        max_retries = 5
        backoff = 1.0  # seconds

        for attempt in range(1, max_retries + 1):
            try:
                response: Response = self._session.get(url, params=params, timeout=30)
            except requests.exceptions.ConnectionError as exc:
                logger.warning("Connection error on attempt %d: %s", attempt, exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                retry_after = float(
                    response.headers.get("Retry-After", backoff * 2)
                )
                logger.warning(
                    "429 Too Many Requests — waiting %.1fs (attempt %d/%d)",
                    retry_after,
                    attempt,
                    max_retries,
                )
                time.sleep(retry_after)
                backoff = retry_after
                continue

            if response.status_code == 401:
                raise HubSpotAPIError(
                    "401 Unauthorized: check your HUBSPOT_ACCESS_TOKEN and "
                    "that the private app has crm.objects.deals.read scope."
                )

            if response.status_code >= 500:
                logger.warning(
                    "HubSpot server error %d on attempt %d/%d",
                    response.status_code,
                    attempt,
                    max_retries,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue

            # Any other 4xx — not worth retrying
            raise HubSpotAPIError(
                f"HubSpot API returned {response.status_code}: {response.text}"
            )

        raise HubSpotRateLimitError(
            f"Exhausted {max_retries} retries against {url}"
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def get_deals(self) -> Generator[dict, None, None]:
        """
        Yield every deal from HubSpot using cursor-based pagination.

        Each yielded item is the raw ``properties`` dict from HubSpot,
        augmented with the deal's ``id`` field.
        """
        after: str | None = None
        page = 0

        while True:
            page += 1
            params: dict = {
                "limit": _PAGE_SIZE,
                "properties": ",".join(DEAL_PROPERTIES),
            }
            if after:
                params["after"] = after

            logger.info("Fetching deals page %d (after=%s) …", page, after)
            data = self._get(DEALS_ENDPOINT, params=params)

            results = data.get("results", [])
            logger.info("  → received %d deals", len(results))

            for item in results:
                yield {"id": item["id"], **item.get("properties", {})}

            # Follow the cursor; stop when HubSpot says there are no more pages
            paging = data.get("paging", {})
            next_cursor = paging.get("next", {}).get("after")
            if not next_cursor:
                logger.info("Pagination complete after %d pages.", page)
                break

            after = next_cursor
