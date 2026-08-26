import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from app.database import Base
from app.main import confirm_checkout, get_booking, list_bookings
from app.models import Booking, ChatSession, User
from app.schemas import CheckoutRequest


class CheckoutTests(unittest.TestCase):
    """Direct endpoint tests for the local Voyage AI cart checkout."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.session_factory()
        self.user = User(
            display_name="Checkout Tester",
            email="checkout-user@example.com",
            password_hash="",
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(ChatSession(user_id=self.user.id, title="Checkout session"))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _payload(items: list[dict] | None = None) -> CheckoutRequest:
        return CheckoutRequest.model_validate(
            {
                "items": items
                if items is not None
                else [
                    {
                        "type": "flight",
                        "title": "London to New York - British Airways",
                        "subtitle": "LHR to JFK",
                        "provider": "duffel",
                        "price": 408.33,
                        "currency": "GBP",
                        "item_data": {
                            "offer_id": "off_safe",
                            "airline": "British Airways",
                            "origin": "LHR",
                            "destination": "JFK",
                            "outbound_departure_date": "2026-09-12",
                            "return_departure_date": "2026-09-19",
                            "cabin_class": "economy",
                        },
                    }
                ],
                "customer": {
                    "full_name": "Checkout Tester",
                    "email": "checkout@example.com",
                },
            }
        )

    def test_checkout_requires_authenticated_user(self) -> None:
        with self.assertRaises(HTTPException) as context:
            confirm_checkout(self._payload(), db=self.db, current_user=None)
        self.assertEqual(context.exception.status_code, 401)

    def test_checkout_confirm_with_authenticated_user_creates_booking(self) -> None:
        response = confirm_checkout(self._payload(), db=self.db, current_user=self.user)

        booking = self.db.query(Booking).filter_by(
            booking_reference=response.booking_reference
        ).one()
        self.assertTrue(response.booking_reference.startswith("TA"))
        self.assertEqual(booking.booking_type, "cart_checkout")
        self.assertEqual(booking.currency, "GBP")
        self.assertEqual(response.total_price, 408.33)
        self.assertEqual(response.totals_by_currency, {"GBP": 408.33})
        self.assertTrue(response.cart_cleared)

    def test_checkout_confirm_with_authenticated_user_links_booking_to_user(self) -> None:
        user = User(
            display_name="Authenticated Tester",
            email="authenticated-checkout@example.com",
            password_hash="",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(ChatSession(user_id=user.id, title="Account session"))
        self.db.commit()

        response = confirm_checkout(self._payload(), db=self.db, current_user=user)
        booking = self.db.query(Booking).filter_by(
            booking_reference=response.booking_reference
        ).one()

        self.assertEqual(booking.user_id, user.id)
        self.assertIsNotNone(booking.session_id)

    def test_checkout_rejects_empty_cart(self) -> None:
        with self.assertRaises(ValueError):
            CheckoutRequest.model_validate(
                {
                    "items": [],
                    "customer": {
                        "full_name": "Checkout Tester",
                        "email": "checkout@example.com",
                    },
                }
            )

    def test_checkout_sums_single_currency_items(self) -> None:
        response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "flight",
                        "title": "Flight",
                        "provider": "duffel",
                        "price": 400,
                        "currency": "GBP",
                    },
                    {
                        "type": "hotel",
                        "title": "Hotel",
                        "provider": "liteapi",
                        "price": 300.5,
                        "currency": "GBP",
                    },
                ]
            ),
            db=self.db,
            current_user=self.user,
        )

        self.assertEqual(response.total_price, 700.5)
        self.assertEqual(response.currency, "GBP")
        self.assertEqual(response.totals_by_currency, {"GBP": 700.5})

    def test_checkout_response_includes_travel_documents(self) -> None:
        response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "flight",
                        "title": "Flight",
                        "provider": "duffel",
                        "price": 100,
                        "currency": "GBP",
                        "item_data": {
                            "airline": "Package Airways",
                            "origin": "EMA",
                            "destination": "LYS",
                            "outbound_departure_date": "2026-09-01",
                            "return_departure_date": "2026-09-04",
                            "cabin_class": "economy",
                        },
                    },
                    {
                        "type": "hotel",
                        "title": "Lyon Central Hotel",
                        "provider": "liteapi",
                        "price": 300,
                        "currency": "GBP",
                        "item_data": {
                            "hotel_name": "Lyon Central Hotel",
                            "location": "Lyon, France",
                            "check_in_date": "2026-09-01",
                            "check_out_date": "2026-09-04",
                            "nights": 3,
                            "guest_count": 1,
                        },
                    },
                ]
            ),
            db=self.db,
            current_user=self.user,
        )

        self.assertIsNotNone(response.flight_ticket)
        self.assertIsNotNone(response.hotel_voucher)
        self.assertEqual(response.flight_ticket["bookingReference"], response.booking_reference)
        self.assertEqual(response.hotel_voucher["bookingReference"], response.booking_reference)
        self.assertEqual(response.flight_ticket["status"], "confirmed")
        self.assertEqual(response.hotel_voucher["status"], "confirmed")
        self.assertNotIn("disclaimer", response.flight_ticket)
        self.assertNotIn("disclaimer", response.hotel_voucher)

    def test_checkout_accepts_package_cart_item_and_creates_documents(self) -> None:
        response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "package",
                        "title": "Lyon travel package",
                        "subtitle": "2026-09-01 to 2026-09-04",
                        "provider": "Voyage AI",
                        "price": 391,
                        "currency": "GBP",
                        "item_data": {
                            "packageId": "PKG-LYS-1",
                            "destinationCity": "Lyon",
                            "destinationAirportCode": "LYS",
                            "nights": 3,
                            "passengers": 1,
                            "flight": {
                                "offerId": "offer-package-1",
                                "airline": "Package Airways",
                                "origin": "EMA",
                                "destination": "LYS",
                                "departureDate": "2026-09-01",
                                "returnDate": "2026-09-04",
                                "departureTime": "08:30",
                                "returnTime": "14:20",
                                "cabin": "economy",
                                "price": 91,
                                "currency": "GBP",
                                "provider": "duffel",
                            },
                            "hotel": {
                                "hotelId": "hotel-package-1",
                                "hotelName": "Lyon Central Hotel",
                                "city": "Lyon",
                                "checkInDate": "2026-09-01",
                                "checkOutDate": "2026-09-04",
                                "nights": 3,
                                "roomName": "Standard room",
                                "boardType": "Room only",
                                "price": 300,
                                "currency": "GBP",
                                "provider": "liteapi",
                            },
                        },
                    }
                ]
            ),
            db=self.db,
            current_user=self.user,
        )

        self.assertEqual(response.total_price, 391)
        self.assertIsNotNone(response.flight_ticket)
        self.assertIsNotNone(response.hotel_voucher)
        self.assertEqual(response.flight_ticket["origin"], "EMA")
        self.assertEqual(response.flight_ticket["destination"], "LYS")
        self.assertEqual(response.hotel_voucher["hotelName"], "Lyon Central Hotel")
        self.assertEqual(response.package_summary["destination"], "Lyon")

    def test_checkout_rejects_fallback_package_item(self) -> None:
        payload = self._payload(
            [
                {
                    "type": "package",
                    "title": "Fallback package",
                    "price": 391,
                    "currency": "GBP",
                    "item_data": {
                        "packageId": "PKG-FALLBACK-1",
                        "destinationCity": "Lyon",
                        "destinationAirportCode": "LYS",
                        "flight": {
                            "origin": "EMA",
                            "destination": "LYS",
                            "departureDate": "2026-09-01",
                            "returnDate": "2026-09-04",
                            "price": 91,
                            "currency": "GBP",
                            "provider": "mock_fallback",
                        },
                        "hotel": {
                            "hotelName": "Fallback Hotel",
                            "city": "Lyon",
                            "checkInDate": "2026-09-01",
                            "checkOutDate": "2026-09-04",
                            "nights": 3,
                            "price": 300,
                            "currency": "GBP",
                            "provider": "mock_hotel_fallback",
                        },
                    },
                }
            ]
        )

        with self.assertRaises(HTTPException) as context:
            confirm_checkout(payload, db=self.db, current_user=self.user)

        self.assertEqual(context.exception.status_code, 400)

    def test_booking_details_persists_travel_documents(self) -> None:
        response = confirm_checkout(self._payload(), db=self.db, current_user=self.user)

        details = get_booking(response.booking_reference, db=self.db, current_user=self.user)

        self.assertEqual(details.reference, response.booking_reference)
        self.assertIsNotNone(details.flight_ticket)
        self.assertEqual(details.flight_ticket["bookingReference"], response.booking_reference)
        self.assertTrue(details.details_available)

    def test_old_booking_without_ticket_json_does_not_crash(self) -> None:
        booking = Booking(
            booking_reference="TA123456",
            user_id=self.user.id,
            destination="Old trip",
            status="Confirmed",
            summary="Older summary-only booking.",
            currency="GBP",
            booking_type="manual",
        )
        self.db.add(booking)
        self.db.commit()

        details = get_booking("TA123456", db=self.db, current_user=self.user)

        self.assertEqual(details.summary, "Older summary-only booking.")
        self.assertIsNone(details.flight_ticket)
        self.assertIsNone(details.hotel_voucher)
        self.assertFalse(details.details_available)

    def test_checkout_handles_mixed_currency_items_safely(self) -> None:
        response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "flight",
                        "title": "Flight",
                        "provider": "duffel",
                        "price": 400,
                        "currency": "GBP",
                    },
                    {
                        "type": "hotel",
                        "title": "Hotel",
                        "provider": "liteapi",
                        "price": 500,
                        "currency": "USD",
                    },
                ]
            ),
            db=self.db,
            current_user=self.user,
        )

        self.assertIsNone(response.total_price)
        self.assertIsNone(response.currency)
        self.assertEqual(response.totals_by_currency, {"GBP": 400.0, "USD": 500.0})

    def test_checkout_does_not_persist_or_return_arbitrary_provider_data(self) -> None:
        response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "flight",
                        "title": "Flight",
                        "provider": "duffel",
                        "price": 400,
                        "currency": "GBP",
                        "item_data": {
                            "provider_token": "duffel_test_not_for_storage",
                            "raw_payload": {"unsafe": "discarded"},
                        },
                    }
                ]
            ),
            db=self.db,
            current_user=self.user,
        )
        booking = self.db.query(Booking).filter_by(
            booking_reference=response.booking_reference
        ).one()

        response_text = response.model_dump_json()
        self.assertNotIn("duffel_test_", response_text)
        self.assertNotIn("duffel_test_", booking.details_json or "")
        self.assertNotIn("raw_payload", booking.details_json or "")
        self.assertEqual(
            json.loads(booking.details_json or "{}") ["items"][0]["title"],
            "Flight",
        )

    def test_authenticated_user_can_only_list_own_bookings(self) -> None:
        other_user = User(
            display_name="Other User",
            email="other-checkout@example.com",
            password_hash="",
        )
        self.db.add(other_user)
        self.db.flush()
        self.db.add(ChatSession(user_id=other_user.id, title="Other session"))
        self.db.commit()

        own_response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "flight",
                        "title": "Own Flight",
                        "provider": "duffel",
                        "price": 400,
                        "currency": "GBP",
                    }
                ]
            ),
            db=self.db,
            current_user=self.user,
        )
        other_response = confirm_checkout(
            self._payload(
                [
                    {
                        "type": "hotel",
                        "title": "Other Hotel",
                        "provider": "liteapi",
                        "price": 250,
                        "currency": "GBP",
                    }
                ]
            ),
            db=self.db,
            current_user=other_user,
        )

        bookings = list_bookings(db=self.db, current_user=self.user)

        self.assertEqual([booking.reference for booking in bookings.bookings], [own_response.booking_reference])
        self.assertEqual(
            get_booking(own_response.booking_reference, db=self.db, current_user=self.user).reference,
            own_response.booking_reference,
        )
        with self.assertRaises(HTTPException) as context:
            get_booking(other_response.booking_reference, db=self.db, current_user=self.user)
        self.assertEqual(context.exception.status_code, 404)
