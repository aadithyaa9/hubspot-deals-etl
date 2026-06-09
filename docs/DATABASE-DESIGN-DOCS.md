# Database Schema: `hubspot_deals`

## PostgreSQL Table

Table is created by `scripts/init_db.sql` and owned by the DLT pipeline.
Django reads from it using `managed = False`.

```sql
CREATE TABLE hubspot_deals (
    id                  BIGSERIAL PRIMARY KEY,
    deal_id             VARCHAR(64)     NOT NULL UNIQUE,
    deal_name           VARCHAR(512),
    amount              NUMERIC(18, 2),
    deal_stage          VARCHAR(128),
    close_date          DATE,
    pipeline            VARCHAR(128),
    owner_id            VARCHAR(64),
    created_date        TIMESTAMP WITH TIME ZONE,
    last_modified_date  TIMESTAMP WITH TIME ZONE,
    _extracted_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    _scan_id            UUID,
    _tenant_id          VARCHAR(128)
);
```

---

## Column Reference

### Deal data columns

| Column | Type | Source HubSpot property | Notes |
|---|---|---|---|
| `id` | BIGSERIAL | — | Internal surrogate PK |
| `deal_id` | VARCHAR(64) | `hs_object_id` / `id` | HubSpot's stable object ID; unique |
| `deal_name` | VARCHAR(512) | `dealname` | Display name |
| `amount` | NUMERIC(18,2) | `amount` | Portal currency; NULL if not set |
| `deal_stage` | VARCHAR(128) | `dealstage` | Internal stage key (e.g. `closedwon`) |
| `close_date` | DATE | `closedate` | Expected or actual close date |
| `pipeline` | VARCHAR(128) | `pipeline` | Pipeline identifier |
| `owner_id` | VARCHAR(64) | `hubspot_owner_id` | Assigned user ID |
| `created_date` | TIMESTAMPTZ | `createdate` | HubSpot creation timestamp |
| `last_modified_date` | TIMESTAMPTZ | `hs_lastmodifieddate` | Last HubSpot update |

### ETL metadata columns

| Column | Type | Description |
|---|---|---|
| `_extracted_at` | TIMESTAMPTZ | UTC time this row was written by DLT |
| `_scan_id` | UUID | Groups all rows from a single pipeline run |
| `_tenant_id` | VARCHAR(128) | HubSpot portal identifier |

---

## Indexes

```sql
CREATE INDEX idx_deals_stage      ON hubspot_deals (deal_stage);
CREATE INDEX idx_deals_close_date ON hubspot_deals (close_date);
CREATE INDEX idx_deals_scan_id    ON hubspot_deals (_scan_id);
```

---

## DLT Write Disposition

The DLT resource uses `write_disposition="merge"` with `primary_key="deal_id"`.  
This means every pipeline run **upserts** rows — safe to re-run without duplicates.
