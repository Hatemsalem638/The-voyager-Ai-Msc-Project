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
    packages = _dedupe_package_options(packages)
    if max_budget is None:
        return _renumber_package_options(sorted(packages, key=lambda item: item["totalPrice"]))
    sorted_packages = sorted(packages, key=lambda item: float(item.get("totalPrice") or 0))
    in_budget = [
        item
        for item in sorted_packages
        if float(item.get("totalPrice") or 0) <= max_budget
    ]
    if in_budget:
        return _renumber_package_options(in_budget)
    return _renumber_package_options(sorted_packages[:1])


def _dedupe_package_options(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for package in packages:
        flight = package.get("flight") if isinstance(package.get("flight"), dict) else {}
        hotel = package.get("hotel") if isinstance(package.get("hotel"), dict) else {}
        key = (
            flight.get("offerId"),
            hotel.get("hotelId"),
            flight.get("departureDate"),
            flight.get("returnDate"),
            hotel.get("checkInDate"),
            hotel.get("checkOutDate"),
            float(package.get("totalPrice") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(package)
    return unique


def _renumber_package_options(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    renumbered: list[dict[str, Any]] = []
    for index, package in enumerate(packages, start=1):
        item = dict(package)
        item["optionNumber"] = index
        renumbered.append(item)
    return renumbered


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
