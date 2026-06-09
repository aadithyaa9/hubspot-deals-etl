# API Documentation: HubSpot Deals REST API

Base URL: `http://localhost:8000` (or your configured `API_PORT`)

Interactive documentation is available at:
- **Swagger UI** → `http://localhost:8000/swagger/`
- **ReDoc** → `http://localhost:8000/redoc/`
- **Raw OpenAPI schema** → `http://localhost:8000/api/schema/`

---

## Endpoints

### `GET /api/deals/`
Returns a paginated list of all extracted HubSpot deals.

**Query parameters**

| Parameter | Type | Description |
|---|---|---|
| `page` | integer | Page number (default: 1) |
| `page_size` | integer | Results per page (default: 25) |
| `deal_stage` | string | Filter by exact stage (case-insensitive) |
| `deal_stage__in` | string | Comma-separated stages, e.g. `closedwon,qualifiedtobuy` |
| `pipeline` | string | Filter by pipeline identifier |
| `close_date_after` | date | `close_date >= YYYY-MM-DD` |
| `close_date_before` | date | `close_date <= YYYY-MM-DD` |
| `amount_min` | number | `amount >= value` |
| `amount_max` | number | `amount <= value` |
| `tenant_id` | string | Filter by tenant/portal |
| `search` | string | Full-text search on `deal_name` or `deal_id` |
| `ordering` | string | Sort field; prefix `-` for descending, e.g. `-close_date` |

**Response 200**
```json
{
  "count": 5,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "deal_id": "12345678",
      "deal_name": "Enterprise License — Acme Corp",
      "amount": "100000.00",
      "deal_stage": "closedwon",
      "close_date": "2024-12-31",
      "pipeline": "default",
      "owner_id": "42",
      "created_date": "2024-01-15T10:00:00Z",
      "last_modified_date": "2024-11-01T14:30:00Z",
      "extracted_at": "2024-11-15T08:00:00Z",
      "scan_id": "550e8400-e29b-41d4-a716-446655440000",
      "tenant_id": "portal-12345"
    }
  ]
}
```

---

### `GET /api/deals/{id}/`
Returns a single deal by its database primary key.

**Response 200** — same shape as a single item in the list above.

**Response 404**
```json
{ "detail": "Not found." }
```

---

### `GET /api/deals/summary/`
Returns aggregated statistics across all deals.  No filters or pagination applied.

**Response 200**
```json
{
  "total_deals": 5,
  "total_amount": "281000.00",
  "average_amount": "56200.00",
  "stages": {
    "closedwon": 2,
    "qualifiedtobuy": 1,
    "appointmentscheduled": 1,
    "presentationscheduled": 1
  },
  "last_extracted_at": "2024-11-15T08:00:00Z"
}
```

---

## Example cURL calls

```bash
# List all deals
curl http://localhost:8000/api/deals/

# Filter closed-won deals over $50k, sorted by amount descending
curl "http://localhost:8000/api/deals/?deal_stage=closedwon&amount_min=50000&ordering=-amount"

# Search by deal name
curl "http://localhost:8000/api/deals/?search=enterprise"

# Deals closing in Q4 2024
curl "http://localhost:8000/api/deals/?close_date_after=2024-10-01&close_date_before=2024-12-31"

# Summary statistics
curl http://localhost:8000/api/deals/summary/
```
