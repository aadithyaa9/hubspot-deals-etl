# API Integration: HubSpot CRM v3

## Overview
The extractor integrates with the [HubSpot CRM v3 REST API](https://developers.hubspot.com/docs/api/crm/deals) to pull deal records into PostgreSQL.

---

## Endpoint

| Property | Value |
|---|---|
| Base URL | `https://api.hubapi.com` |
| Deals endpoint | `GET /crm/v3/objects/deals` |
| API version | v3 |

### Key query parameters

| Parameter | Description |
|---|---|
| `limit` | Records per page (max 100, we use 100) |
| `after` | Cursor string for the next page |
| `properties` | Comma-separated list of property names to return |

### Properties requested

```
dealname, amount, dealstage, closedate, pipeline,
hubspot_owner_id, createdate, hs_lastmodifieddate
```

---

## Authentication

HubSpot Private Apps use **Bearer token** authentication.

1. Go to **HubSpot → Settings → Integrations → Private Apps**.
2. Create an app named `DLT Deals Extractor`.
3. Grant the scope `crm.objects.deals.read`.
4. Copy the generated token.
5. Store it in `.env` as `HUBSPOT_ACCESS_TOKEN=<token>`.

Every request includes:
```
Authorization: Bearer <token>
Content-Type: application/json
```

---

## Pagination

HubSpot uses **cursor-based pagination** via the `after` parameter.

```
GET /crm/v3/objects/deals?limit=100
→ { results: [...], paging: { next: { after: "cursor-abc" } } }

GET /crm/v3/objects/deals?limit=100&after=cursor-abc
→ { results: [...] }   ← no paging.next = last page
```

The extractor follows the cursor until `paging.next` is absent.

---

## Rate Limits

| Tier | Limit |
|---|---|
| Default (Private App) | 150 requests / 10 seconds |
| Daily | 250,000 requests / day |

Our implementation uses a **sliding-window rate limiter** in `services/hubspot_api_service.py`:
- Tracks timestamps of the last N requests.
- Sleeps dynamically when the window is full.
- Stays at 140 req/10 s to leave a 10-request safety margin.

On `429 Too Many Requests`, the extractor reads the `Retry-After` header and sleeps accordingly before retrying (up to 5 attempts with exponential back-off).

---

## Error Handling

| HTTP Status | Behaviour |
|---|---|
| 200 | Success — parse and yield records |
| 401 | Raise `HubSpotAPIError` immediately (bad token) |
| 429 | Sleep `Retry-After` seconds, then retry |
| 5xx | Exponential back-off, up to 5 retries |
| Other 4xx | Raise `HubSpotAPIError` immediately |
