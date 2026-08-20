"""Checkout helper functions for cart totals and travel documents."""

from __future__ import annotations

from typing import Any

from app.schemas import FlightTicket, HotelVoucher


def _checkout_totals(items: list[Any]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in items:
        currency = item.currency.upper()
        totals[currency] = round(totals.get(currency, 0.0) + item.price, 2)
    return totals


def _safe_checkout_item_details(item: Any) -> dict[str, Any]:
    """Persist only compact public cart data, never arbitrary client payloads."""

    safe_item_data = _safe_public_item_data(item.item_data)
    return {
        "type": item.type,
        "title": item.title,
        "subtitle": item.subtitle,
        "provider": item.provider,
        "price": item.price,
        "currency": item.currency.upper(),
        "itemData": safe_item_data,
    }


def _safe_public_item_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    blocked_fragments = ("token", "secret", "api_key", "apikey", "password", "raw_payload")
    blocked_value_fragments = ("duffel_test_", "liteapi_key", "api_key", "bearer ")
    safe: dict[str, Any] = {}
    for key, item_value in value.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(fragment in lowered for fragment in blocked_fragments):
            continue
        if isinstance(item_value, str):
            if any(fragment in item_value.lower() for fragment in blocked_value_fragments):
                continue
            safe[key_text] = item_value
        elif isinstance(item_value, (int, float, bool)) or item_value is None:
            safe[key_text] = item_value
        elif isinstance(item_value, list):
            safe[key_text] = [
                entry
                for entry in item_value
                if (
                    isinstance(entry, (int, float, bool))
                    or entry is None
                    or (
                        isinstance(entry, str)
                        and not any(fragment in entry.lower() for fragment in blocked_value_fragments)
                    )
                )
            ][:20]
        elif isinstance(item_value, dict):
            nested = _safe_public_item_data(item_value)
            if nested:
                safe[key_text] = nested
    return safe


def _checkout_package_summary(
    safe_items: list[dict[str, Any]],
    totals: dict[str, float],
) -> dict[str, Any]:
    package_item = next((item for item in safe_items if item["type"] == "package"), None)
    package_data = package_item.get("itemData", {}) if package_item else {}
    if package_item:
        return {
            "destination": package_data.get("destinationCity")
            or package_data.get("destination")
            or package_item.get("title")
            or "Selected package",
            "package": package_item,
            "flight": package_data.get("flight"),
            "hotel": package_data.get("hotel"),
            "items": safe_items,
            "totalsByCurrency": totals,
        }
    flight_item = next((item for item in safe_items if item["type"] == "flight"), None)
    hotel_item = next((item for item in safe_items if item["type"] == "hotel"), None)
    flight_data = flight_item.get("itemData", {}) if flight_item else {}
    hotel_data = hotel_item.get("itemData", {}) if hotel_item else {}
    destination = (
        flight_data.get("destination")
        or hotel_data.get("location")
        or (hotel_item or flight_item or {}).get("title")
        or "Selected trip"
    )
    return {
        "destination": destination,
        "flight": flight_item,
        "hotel": hotel_item,
        "items": safe_items,
        "totalsByCurrency": totals,
    }


def _travel_documents_for_checkout_items(
    safe_items: list[dict[str, Any]],
    booking_reference: str,
    passenger_name: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    suffix = booking_reference[-6:]
    package_item = next((item for item in safe_items if item["type"] == "package"), None)
    package_data = package_item.get("itemData", {}) if package_item else {}
    flight_item = next((item for item in safe_items if item["type"] == "flight"), None)
    hotel_item = next((item for item in safe_items if item["type"] == "hotel"), None)
    ticket: dict[str, Any] | None = None
    voucher: dict[str, Any] | None = None

    if package_item and isinstance(package_data.get("flight"), dict):
        flight = package_data["flight"]
        ticket = FlightTicket(
            ticketNumber=f"VAI-FLT-{suffix}",
            passengerName=passenger_name or "Demo Passenger",
            airline=flight.get("airline") or package_item.get("title"),
            origin=str(flight.get("origin") or ""),
            destination=str(flight.get("destination") or ""),
            departureDate=str(flight.get("departureDate") or ""),
            returnDate=flight.get("returnDate"),
            departureTime=flight.get("departureTime"),
            returnTime=flight.get("returnTime"),
            cabin=str(flight.get("cabin") or "economy"),
            bookingReference=booking_reference,
            status="confirmed",
        ).model_dump(mode="json")
    elif flight_item:
        flight = flight_item.get("itemData", {})
        ticket = FlightTicket(
            ticketNumber=f"VAI-FLT-{suffix}",
            passengerName=passenger_name or "Demo Passenger",
            airline=flight.get("airline") or flight_item.get("title"),
            origin=str(flight.get("origin") or ""),
            destination=str(flight.get("destination") or ""),
            departureDate=str(
                flight.get("outbound_departure_date")
                or flight.get("departure_date")
                or ""
            ),
            returnDate=flight.get("return_departure_date") or flight.get("return_date"),
            departureTime=flight.get("outbound_departure_time"),
            returnTime=flight.get("return_departure_time"),
            cabin=str(flight.get("cabin_class_label") or flight.get("cabin_class") or "economy"),
            bookingReference=booking_reference,
            status="confirmed",
        ).model_dump(mode="json")

    if package_item and isinstance(package_data.get("hotel"), dict):
        hotel = package_data["hotel"]
        voucher = HotelVoucher(
            voucherNumber=f"VAI-HTL-{suffix}",
            hotelName=str(hotel.get("hotelName") or package_item.get("title") or "Selected hotel"),
            city=str(hotel.get("city") or package_data.get("destinationCity") or ""),
            checkInDate=str(hotel.get("checkInDate") or ""),
            checkOutDate=str(hotel.get("checkOutDate") or ""),
            nights=int(hotel.get("nights") or package_data.get("nights") or 1),
            roomName=hotel.get("roomName"),
            boardType=hotel.get("boardType"),
            guests=int(package_data.get("passengers") or 1),
            bookingReference=booking_reference,
            status="confirmed",
        ).model_dump(mode="json")
    elif hotel_item:
        hotel = hotel_item.get("itemData", {})
        voucher = HotelVoucher(
            voucherNumber=f"VAI-HTL-{suffix}",
            hotelName=str(hotel.get("hotel_name") or hotel_item.get("title") or "Selected hotel"),
            city=str(hotel.get("location") or ""),
            checkInDate=str(hotel.get("check_in_date") or ""),
            checkOutDate=str(hotel.get("check_out_date") or ""),
            nights=int(hotel.get("nights") or 1),
            roomName=hotel.get("room_name"),
            boardType=hotel.get("board_type"),
            guests=int(hotel.get("guest_count") or 1),
            bookingReference=booking_reference,
            status="confirmed",
        ).model_dump(mode="json")

    return ticket, voucher
