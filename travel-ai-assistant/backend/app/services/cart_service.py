"""Cart summary helpers for selected flights, hotels, and packages."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from app.schemas import PackageCartItem, PackageCartSummary, PackageOption


def _package_cart_summary(package: dict[str, Any], session_id: int) -> dict[str, Any]:
    cart_item = PackageCartItem(
        cartItemId=f"CART-{session_id}-PKG-{package.get('optionNumber')}",
        packageOption=PackageOption.model_validate(package),
        addedAt=datetime.now(timezone.utc),
    )
    cart = PackageCartSummary(
        cartId=f"CART-{session_id}",
        items=[cart_item],
        totalPrice=float(package.get("totalPrice") or 0),
        currency=str(package.get("currency") or "GBP").upper(),
        status="active",
    )
    return cart.model_dump(mode="json")


def _hotel_cart_summary(
    hotel: dict[str, Any],
    option_number: int,
    session_id: int,
    price_getter: Callable[[dict[str, Any]], float | None],
) -> dict[str, Any]:
    price = float(price_getter(hotel) or 0)
    currency = str(hotel.get("currency") or "GBP").upper()
    hotel_id = str(hotel.get("id") or f"hotel-option-{option_number}")
    title = str(hotel.get("hotel_name") or "Selected hotel")
    subtitle = (
        f"{hotel.get('location') or 'Selected city'}, "
        f"{hotel.get('check_in_date') or 'check-in date'} to "
        f"{hotel.get('check_out_date') or 'check-out date'}"
    )
    item = {
        "cartItemId": f"CART-{session_id}-HTL-{hotel_id}",
        "itemType": "hotel",
        "type": "hotel",
        "title": title,
        "subtitle": subtitle,
        "provider": str(hotel.get("provider") or "Voyage AI"),
        "price": price,
        "currency": currency,
        "hotelOption": deepcopy(hotel),
        "itemData": deepcopy(hotel),
        "quantity": 1,
        "addedAt": datetime.now(timezone.utc).isoformat(),
        "status": "in_cart",
    }
    return {
        "cartId": f"CART-{session_id}",
        "items": [item],
        "totalPrice": price,
        "currency": currency,
        "status": "active",
    }


def _flight_cart_summary(
    flight: dict[str, Any],
    option_number: int,
    session_id: int,
    price_getter: Callable[[dict[str, Any]], float | None],
) -> dict[str, Any]:
    price = float(price_getter(flight) or 0)
    currency = str(flight.get("currency") or "GBP").upper()
    offer_id = str(flight.get("offer_id") or flight.get("id") or f"flight-option-{option_number}")
    origin = str(flight.get("origin") or "Origin")
    destination = str(flight.get("destination") or "Destination")
    airline = str(flight.get("airline") or "Selected flight")
    departure = str(
        flight.get("outbound_departure_date")
        or flight.get("departure_date")
        or "departure date"
    )
    return_date = flight.get("return_departure_date") or flight.get("return_date")
    subtitle = f"{departure}"
    if return_date:
        subtitle += f" to {return_date}"
    item = {
        "cartItemId": f"CART-{session_id}-FLT-{offer_id}",
        "itemType": "flight",
        "type": "flight",
        "title": f"{origin} to {destination} - {airline}",
        "subtitle": subtitle,
        "provider": str(flight.get("provider") or "Voyage AI"),
        "price": price,
        "currency": currency,
        "flightOption": deepcopy(flight),
        "itemData": deepcopy(flight),
        "quantity": 1,
        "addedAt": datetime.now(timezone.utc).isoformat(),
        "status": "in_cart",
    }
    return {
        "cartId": f"CART-{session_id}",
        "items": [item],
        "totalPrice": price,
        "currency": currency,
        "status": "active",
    }


def _cart_contains_hotel(cart: dict[str, Any] | None, hotel: dict[str, Any] | None) -> bool:
    if not isinstance(cart, dict) or not isinstance(hotel, dict):
        return False
    hotel_id = str(hotel.get("id") or "")
    items = cart.get("items")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        item_data = item.get("hotelOption") or item.get("itemData")
        if item.get("itemType") == "hotel" or item.get("type") == "hotel":
            if not hotel_id:
                return True
            if isinstance(item_data, dict) and str(item_data.get("id") or "") == hotel_id:
                return True
    return False


def _cart_contains_flight(cart: dict[str, Any] | None, flight: dict[str, Any] | None) -> bool:
    if not isinstance(cart, dict) or not isinstance(flight, dict):
        return False
    flight_id = str(flight.get("offer_id") or flight.get("id") or "")
    items = cart.get("items")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        item_data = item.get("flightOption") or item.get("itemData")
        if item.get("itemType") == "flight" or item.get("type") == "flight":
            if not flight_id:
                return True
            if isinstance(item_data, dict):
                item_id = str(item_data.get("offer_id") or item_data.get("id") or "")
                if item_id == flight_id:
                    return True
    return False
