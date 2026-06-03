import secrets
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Enum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .util import now_utc


class DestinationType(str, PyEnum):
    telegram = "telegram"
    ntfy = "ntfy"
    mattermost = "mattermost"
    slack = "slack"
    discord = "discord"
    email = "email"

    def __str__(self) -> str:
        return self.value


class FilterMode(str, PyEnum):
    all = "all"
    errors_only = "errors_only"
    off = "off"

    def __str__(self) -> str:
        return self.value


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class User(Base):
    """An OAuth-authenticated user. Login alone does not grant access:
    `status` must be `approved` (or `role` == `admin`)."""
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("provider", "provider_sub", name="uq_provider_sub"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)       # github | microsoft
    provider_sub: Mapped[str] = mapped_column(String(255), nullable=False)  # stable id from provider
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")      # user | admin
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending") # pending | approved | blocked
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_approved(self) -> bool:
        return self.role == "admin" or self.status == "approved"

    @property
    def display(self) -> str:
        return self.name or self.email or f"{self.provider}:{self.provider_sub}"


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    type: Mapped[DestinationType] = mapped_column(Enum(DestinationType), nullable=False)

    # Telegram
    telegram_bot_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ntfy
    ntfy_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ntfy_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Mattermost (REST API v4 — personal access or bot-account token)
    mattermost_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mattermost_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    mattermost_team: Mapped[str | None] = mapped_column(Text, nullable=True)  # team slug for channel lookups

    # Slack / Discord (incoming webhook URL — channel fixed by the webhook)
    slack_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    discord_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Email (SMTP)
    smtp_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True)

    # Ownership / visibility (used when OAuth auth is enabled; ignored under Basic auth)
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")  # private | global

    destinations: Mapped[list["Destination"]] = relationship(back_populates="bot")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    coolify_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    coolify_project_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_mode: Mapped[FilterMode] = mapped_column(Enum(FilterMode), default=FilterMode.all)
    coolify_server_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    destinations: Mapped[list["Destination"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Destination(Base):
    __tablename__ = "destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    bot_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("bots.id"), nullable=True)
    type: Mapped[DestinationType] = mapped_column(Enum(DestinationType), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    filter_mode: Mapped[FilterMode | None] = mapped_column(Enum(FilterMode), nullable=True)  # None = inherit from project
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = never tested

    # Per-destination target (credentials live on Bot)
    telegram_chat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_chat_label: Mapped[str | None] = mapped_column(Text, nullable=True)  # original @username input
    ntfy_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    mattermost_target: Mapped[str | None] = mapped_column(Text, nullable=True)  # @user / channel / raw id input
    mattermost_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # resolved & cached channel_id
    email_to: Mapped[str | None] = mapped_column(Text, nullable=True)  # recipient address (email destinations)

    # Ownership / visibility (used when OAuth auth is enabled; ignored under Basic auth)
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="private")  # private | global

    # Legacy inline credentials (kept for backwards compat, not used by new UI)
    telegram_bot_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    ntfy_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ntfy_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    bot: Mapped["Bot | None"] = relationship(back_populates="destinations")
    project: Mapped["Project"] = relationship(back_populates="destinations")
