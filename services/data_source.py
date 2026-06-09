"""
services/data_source.py
-----------------------
DLT data source for HubSpot deals.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, date
from decimal import Decimal, InvalidOperation
from typing import Generator, Dict, Any, Optional, Callable

import dlt
from dlt.common import pendulum
from services.api_service import HubSpotAPIService

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _transform_deal(raw: dict, scan_id: str, tenant_id: str) -> dict:
    """Transform raw HubSpot deal into a flat dict for DLT."""
    close_date = _parse_date(raw.get("closedate"))

    return {
        "deal_id": str(raw.get("id") or raw.get("hs_object_id") or ""),
        "deal_name": raw.get("dealname"),
        "amount": _parse_decimal(raw.get("amount")),
        "deal_stage": raw.get("dealstage"),
        # Store as ISO string — DLT will handle the type
        "close_date": close_date.isoformat() if close_date else None,
        "pipeline": raw.get("pipeline"),
        "owner_id": raw.get("hubspot_owner_id"),
        "created_date": _parse_datetime(raw.get("createdate")),
        "last_modified_date": _parse_datetime(raw.get("hs_lastmodifieddate")),
        "extracted_at": datetime.now(tz=timezone.utc),
        "scan_id": scan_id,
        "tenant_id": tenant_id,
    }


# ── DLT resource ──────────────────────────────────────────────────────────────

@dlt.resource(
    name="hubspot_deals",
    write_disposition="replace",
    primary_key="deal_id",
)
def _deals_resource(
    access_token: str,
    scan_id: str,
    tenant_id: str,
    checkpoint_callback: Optional[Callable] = None,
    check_cancel_callback: Optional[Callable] = None,
    check_pause_callback: Optional[Callable] = None,
    resume_from: Optional[Dict] = None,
) -> Generator[dict, None, None]:
    api = HubSpotAPIService(access_token=access_token)
    count = 0

    for raw in api.get_deals():
        if check_cancel_callback and check_cancel_callback(scan_id):
            logger.info("Extraction cancelled at record %d", count)
            break
        if check_pause_callback and check_pause_callback(scan_id):
            logger.info("Extraction paused at record %d", count)
            break

        yield _transform_deal(raw, scan_id, tenant_id)
        count += 1

        if checkpoint_callback and count % 100 == 0:
            checkpoint_callback(scan_id, {
                "recordsProcessed": count,
                "cursor": str(count),
            })

    if checkpoint_callback:
        checkpoint_callback(scan_id, {
            "recordsProcessed": count,
            "cursor": "completed",
        })

    logger.info("Extraction complete — %d deals yielded.", count)


# ── create_data_source (called by extraction_service.py) ─────────────────────

def create_data_source(
    job_config: Dict[str, Any],
    auth_config: Dict[str, Any],
    filters: Dict[str, Any],
    checkpoint_callback: Optional[Callable] = None,
    check_cancel_callback: Optional[Callable] = None,
    check_pause_callback: Optional[Callable] = None,
    resume_from: Optional[Dict] = None,
):
    access_token = (
        auth_config.get("token")
        or auth_config.get("access_token")
        or auth_config.get("apiKey")
        or os.getenv("HUBSPOT_ACCESS_TOKEN")
    )

    if not access_token:
        raise ValueError("No HubSpot access token found.")

    scan_id = job_config.get("scanId") or str(uuid.uuid4())
    tenant_id = job_config.get("organizationId") or os.getenv("DLT_PIPELINE_NAME", "default")

    return _deals_resource(
        access_token=access_token,
        scan_id=scan_id,
        tenant_id=tenant_id,
        checkpoint_callback=checkpoint_callback,
        check_cancel_callback=check_cancel_callback,
        check_pause_callback=check_pause_callback,
        resume_from=resume_from,
    )


# ── Standalone DLT source ────────────────────────────────────────────────────

@dlt.source(name="hubspot")
def hubspot_deals_source(
    access_token: str = dlt.secrets.value,
    tenant_id: str = dlt.config.value,
):
    scan_id = str(uuid.uuid4())
    return _deals_resource(
        access_token=access_token,
        scan_id=scan_id,
        tenant_id=tenant_id,
    )