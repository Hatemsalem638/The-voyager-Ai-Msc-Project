"""SQLAlchemy models for Voyage AI."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="Demo User")
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
    )
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )

    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")
    preferences: Mapped["UserPreference | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="Demo chat session")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    bookings: Mapped[list["Booking"]] = relationship(back_populates="session")
    state: Mapped["SessionState | None"] = relationship(
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    sender: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    intent_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    intent_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    booking_reference: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id"),
        nullable=True,
        index=True,
    )
    destination: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="Confirmed")
    summary: Mapped[str] = mapped_column(Text)
    flight_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hotel_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    estimated_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    booking_type: Mapped[str] = mapped_column(String(40), default="manual")
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    user: Mapped[User] = relationship(back_populates="bookings")
    session: Mapped[ChatSession | None] = relationship(back_populates="bookings")


class SessionState(Base):
    """Persistent, safe conversation state for one chat session."""

    __tablename__ = "session_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id"),
        unique=True,
        index=True,
    )
    latest_flight_search_request_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latest_flight_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_flight_option_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_flight_selection_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latest_hotel_search_request_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latest_hotel_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_hotel_option_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_hotel_selection_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latest_destination_discovery_request_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latest_destination_discovery_results_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    pending_destination_discovery_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    active_trip_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_origin_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_tool: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recent_facts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )

    session: Mapped[ChatSession] = relationship(back_populates="state")


class UserPreference(Base):
    """Persistent preference foundation for future travel recommendations."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        unique=True,
        index=True,
    )
    preferred_cabin_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    max_stops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_departure_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    budget_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_hotel_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hotel_location_preference: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )
    accessibility_needs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sustainability_preference: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )

    user: Mapped[User] = relationship(back_populates="preferences")
