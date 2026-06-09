"""
models/models.py
-----------------
All SQLAlchemy models required by the scaffold services + the Deal model
for HubSpot deals written by DLT.
"""

import enum
from sqlalchemy import (
    Column, BigInteger, String, Numeric,
    Date, DateTime, Text, Integer, JSON, Enum, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()


# ── Enums ─────────────────────────────────────────────────────────────────────

class JobStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    RESUMING = "resuming"
    CRASHED = "crashed"


# ── Job model ─────────────────────────────────────────────────────────────────

class Job(Base):
    """Tracks extraction scan jobs."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_id = Column(String(256), unique=True, nullable=False, index=True)
    organization_id = Column(String(256), nullable=True, index=True)
    status = Column(
        Enum(JobStatus),
        nullable=False,
        default=JobStatus.PENDING
    )
    job_type = Column(String(64), nullable=True)
    config = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
        onupdate=lambda: datetime.now(tz=timezone.utc)
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationship to checkpoints
    checkpoints = relationship(
        "JobCheckpoint",
        back_populates="job",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "scanId": self.scan_id,
            "organizationId": self.organization_id,
            "status": self.status.value if self.status else None,
            "job_type": self.job_type,
            "config": self.config,
            "result": self.result,
            "error_message": self.error_message,
            "metadata": self.metadata_,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# ── JobCheckpoint model ───────────────────────────────────────────────────────

class JobCheckpoint(Base):
    """Stores pagination cursors so crashed jobs can be resumed."""

    __tablename__ = "job_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(
        String(256),
        ForeignKey("jobs.scan_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    cursor = Column(Text, nullable=True)
    page_number = Column(Integer, nullable=True, default=0)
    records_processed = Column(Integer, nullable=True, default=0)
    checkpoint_data = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc)
    )

    # Relationship back to job
    job = relationship("Job", back_populates="checkpoints")

    def to_dict(self):
        return {
            "id": self.id,
            "job_id": self.job_id,
            "cursor": self.cursor,
            "pageNumber": self.page_number,
            "recordsProcessed": self.records_processed,
            "checkpoint_data": self.checkpoint_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── Deal model ────────────────────────────────────────────────────────────────
class Deal(Base):
    """HubSpot deal record as extracted by the DLT pipeline."""

    __tablename__ = "hubspot_deals"

    _dlt_id = Column("_dlt_id", String(128), primary_key=True)
    _dlt_load_id = Column("_dlt_load_id", String(128), nullable=True)
    deal_id = Column(String(64), nullable=True)
    deal_name = Column(String(512), nullable=True)
    amount = Column(Numeric(18, 2), nullable=True)
    deal_stage = Column(String(128), nullable=True)
    close_date = Column(String(32), nullable=True)
    pipeline = Column(String(128), nullable=True)
    owner_id = Column(String(64), nullable=True)
    created_date = Column(DateTime(timezone=True), nullable=True)
    last_modified_date = Column(DateTime(timezone=True), nullable=True)
    extracted_at = Column(DateTime(timezone=True), nullable=True)
    scan_id = Column(String(128), nullable=True)
    tenant_id = Column(String(128), nullable=True)

    def to_dict(self):
        return {
            "deal_id": self.deal_id,
            "deal_name": self.deal_name,
            "amount": float(self.amount) if self.amount is not None else None,
            "deal_stage": self.deal_stage,
            "close_date": self.close_date,
            "pipeline": self.pipeline,
            "owner_id": self.owner_id,
            "created_date": self.created_date.isoformat() if self.created_date else None,
            "last_modified_date": self.last_modified_date.isoformat() if self.last_modified_date else None,
            "extracted_at": self.extracted_at.isoformat() if self.extracted_at else None,
            "scan_id": self.scan_id,
            "tenant_id": self.tenant_id,
        }

    def __repr__(self):
        return f"<Deal {self.deal_name or '(unnamed)'} [{self.deal_id}]>"