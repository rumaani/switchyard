"""Persistent model.

    User ──(IdP groups)──> GroupMapping ──> Role ──> permissions
                                              └──> EndpointGrant (tool allow-list per endpoint)
    Endpoint (vendor MCP server, named) ── tools cache, encrypted auth config
    Workflow (definition JSON) ──> Run ──> RunStep
    AuditEvent (append-only)
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True)  # IdP "sub" (or dev:<email>)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    groups: Mapped[list] = mapped_column(JSON, default=list)  # groups claim at last sign-in
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)

    grants: Mapped[list[EndpointGrant]] = relationship(back_populates="role", cascade="all, delete-orphan")


class GroupMapping(Base):
    """An IdP group (name or object id) that confers a role."""

    __tablename__ = "group_mappings"
    __table_args__ = (UniqueConstraint("idp_group", "role_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    idp_group: Mapped[str] = mapped_column(String(255), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    role: Mapped[Role] = relationship()


class Endpoint(Base):
    __tablename__ = "endpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # slug used in workflows, e.g. "essbase"
    display_name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(2000))
    transport: Mapped[str] = mapped_column(String(32), default="streamable_http")
    auth_type: Mapped[str] = mapped_column(String(32), default="none")  # none|bearer|api_key|oauth_client_credentials
    auth_config_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet-encrypted JSON
    extra_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tools_cache: Mapped[list] = mapped_column(JSON, default=list)
    server_info: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="unknown")  # unknown|ok|error
    status_detail: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EndpointGrant(Base):
    """Which tools a role may call on an endpoint. tools: list of glob patterns ("*" = all)."""

    __tablename__ = "endpoint_grants"
    __table_args__ = (UniqueConstraint("role_id", "endpoint_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    tools: Mapped[list] = mapped_column(JSON, default=lambda: ["*"])
    role: Mapped[Role] = relationship(back_populates="grants")
    endpoint: Mapped[Endpoint] = relationship()


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    # role names that may run it ([] = anyone with workflows.run)
    allowed_roles: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(320), default="")
    updated_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    workflow_name: Mapped[str] = mapped_column(String(255))
    workflow_version: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict] = mapped_column(JSON)  # snapshot at start, so later edits never change history
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    # queued | running | waiting_approval | succeeded | failed | rejected | cancelled
    started_by: Mapped[str] = mapped_column(String(320))
    started_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunStep.position"
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    step_id: Mapped[str] = mapped_column(String(64))
    step_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | running | waiting_approval | succeeded | failed | skipped | rejected
    endpoint: Mapped[str] = mapped_column(String(64), default="")
    tool: Mapped[str] = mapped_column(String(255), default="")
    request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[object | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[str] = mapped_column(String(320), default="")
    decision_note: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run: Mapped[Run] = relationship(back_populates="steps")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(320), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(500), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="ok")  # ok | denied | error
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
