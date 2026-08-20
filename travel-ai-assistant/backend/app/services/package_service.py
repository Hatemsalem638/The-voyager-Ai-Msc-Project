"""Package-option building helpers."""

from __future__ import annotations

from typing import Any, Callable

from app.schemas import PackageFlightSummary, PackageHotelSummary


def _flight_summary_for_package(
    flight: dict[str, Any],
    price_getter: Callable[[dict[str, Any]], float | None],
) -> PackageFlightSummary:
    return PackageFlightSummary(
        offerId=str(flight.get("offer_id") or flight.get("id") or ""),
        airline=flight.get("airline"),
        origin=str(flight.get("origin") or ""),
        destination=str(flight.get("destination") or ""),
        departureDate=str(flight.get("outbound_departure_date") or flight.get("departure_date") or ""),
        returnDate=flight.get("return_departure_date") or flight.get("return_date"),
        departureTime=flight.get("outbound_departure_time"),
        returnTime=flight.get("return_departure_time"),
        cabin=str(flight.get("cabin_class") or "economy"),
        stops=flight.get("stops"),
        duration=flight.get("duration_label"),
        price=float(price_getter(flight) or 0),
        currency=str(flight.get("currency") or "GBP").upper(),
        provider=str(flight.get("provider") or "unknown"),
    )


def _hotel_summary_for_package(
    hotel: dict[str, Any],
    price_getter: Callable[[dict[str, Any]], float | None],
) -> PackageHotelSummary:
    return PackageHotelSummary(
        hotelId=str(hotel.get("id") or "") or None,
        hotelName=str(hotel.get("hotel_name") or "Selected hotel"),
        city=str(hotel.get("location") or ""),
        checkInDate=str(hotel.get("check_in_date") or ""),
        checkOutDate=str(hotel.get("check_out_date") or ""),
        nights=int(hotel.get("nights") or 1),
        rating=hotel.get("rating"),
        address=hotel.get("location"),
        boardType=hotel.get("board_type"),
        roomName=hotel.get("room_name"),
        price=float(price_getter(hotel) or 0),
        currency=str(hotel.get("currency") or "GBP").upper(),
        provider=str(hotel.get("provider") or "unknown"),
    )


def _sort_package_options_for_budget(
    packages: list[dict[str, Any]],
    max_budget: float | None,
) -> list[dict[str, Any]]:
    if max_budget is None:
        return sorted(packages, key=lambda item: item["totalPrice"])
    return sorted(
        packages,
        key=lambda item: (
            float(item.get("totalPrice") or 0) > max_budget,
            abs(float(item.get("totalPrice") or 0) - max_budget),
            float(item.get("totalPrice") or 0),
        ),
    )


def _package_results_for_chat(packages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "flights": [],
        "hotels": [],
        "destinations": [],
        "packages": packages,
        "cart": None,
        "checkout": None,
        "flightTicket": None,
        "hotelVoucher": None,
    }
