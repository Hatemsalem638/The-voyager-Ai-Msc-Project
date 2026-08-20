import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import (
    decode_access_token,
    get_current_user,
    get_optional_current_user,
    verify_password,
)
from app.database import Base
from app.main import (
    chat,
    create_booking,
    get_auth_me,
    login_user,
    register_user,
    update_auth_preferences,
)
from app.memory_store import load_user_preferences
from app.models import Booking, ChatSession, User
from app.schemas import (
    BookingCreateRequest,
    ChatRequest,
    PreferenceUpdate,
    TravelToolCall,
    UserCreate,
    UserLogin,
)


class AuthenticationTests(unittest.TestCase):
    """Stage 5H direct endpoint tests using a fresh local SQLite database."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.session_factory()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _register(self, email: str = "traveller@example.com"):
        return register_user(
            UserCreate(
                email=email,
                password="correct-horse-battery",
                full_name="Travel Tester",
            ),
            db=self.db,
        )

    def _registered_user(self) -> User:
        self._register()
        return self.db.query(User).filter(User.email == "traveller@example.com").one()

    def test_registering_user_creates_hashed_password_and_default_records(self) -> None:
        response = self._register()
        user = self.db.query(User).filter(User.id == response.user.id).one()

        self.assertEqual(response.token_type, "bearer")
        self.assertEqual(user.email, "traveller@example.com")
        self.assertTrue(verify_password("correct-horse-battery", user.password_hash))
        self.assertIsNotNone(user.preferences)
        self.assertEqual(len(user.sessions), 1)

    def test_password_hash_is_not_the_plain_password(self) -> None:
        user = self._registered_user()

        self.assertNotEqual(user.password_hash, "correct-horse-battery")

    def test_duplicate_email_registration_fails(self) -> None:
        self._register()

        with self.assertRaises(HTTPException) as raised:
            self._register()

        self.assertEqual(raised.exception.status_code, 409)

    def test_login_with_correct_password_returns_bearer_token(self) -> None:
        self._register()

        response = login_user(
            UserLogin(email="traveller@example.com", password="correct-horse-battery"),
            db=self.db,
        )

        self.assertEqual(response.token_type, "bearer")
        self.assertEqual(decode_access_token(response.access_token)["sub"], str(response.user.id))

    def test_login_with_wrong_password_fails(self) -> None:
        self._register()

        with self.assertRaises(HTTPException) as raised:
            login_user(
                UserLogin(email="traveller@example.com", password="incorrect-password"),
                db=self.db,
            )

        self.assertEqual(raised.exception.status_code, 401)

    def test_auth_me_returns_profile_for_valid_token(self) -> None:
        registration = self._register()
        optional_user = get_optional_current_user(
            credentials=HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials=registration.access_token,
            ),
            db=self.db,
        )
        current_user = get_current_user(current_user=optional_user)

        profile = get_auth_me(current_user=current_user, db=self.db)

        self.assertEqual(profile.email, "traveller@example.com")
        self.assertEqual(profile.full_name, "Travel Tester")
        self.assertIsNotNone(profile.preferences)

    def test_authenticated_preferences_can_be_updated_and_loaded(self) -> None:
        user = self._registered_user()

        result = update_auth_preferences(
            PreferenceUpdate(
                preferred_cabin_class="economy",
                max_stops=0,
                budget_max=800,
                accessibility_needs="Lift access",
            ),
            current_user=user,
            db=self.db,
        )
        stored = load_user_preferences(self.db, user.id)

        self.assertEqual(result["preferred_cabin_class"], "economy")
        self.assertEqual(stored["max_stops"], 0)
        self.assertEqual(stored["budget_max"], 800)
        self.assertEqual(stored["accessibility_needs"], "Lift access")

    @patch("app.main.route_tool_with_llama")
    def test_chat_with_token_user_uses_authenticated_session(
        self,
        mock_route_tool_with_llama,
    ) -> None:
        user = self._registered_user()
        mock_route_tool_with_llama.return_value = TravelToolCall(tool="general_help")

        response = chat(
            ChatRequest(message="hello"),
            db=self.db,
            current_user=user,
        )

        session = self.db.query(ChatSession).filter(ChatSession.user_id == user.id).one()
        self.assertEqual(response.intent.intent_type, "general_enquiry")
        self.assertEqual(session.user_id, user.id)
        self.assertIsNone(self.db.query(User).filter(User.email == "demo@example.com").first())

    @patch("app.main.route_tool_with_llama")
    def test_chat_without_token_keeps_demo_fallback(
        self,
        mock_route_tool_with_llama,
    ) -> None:
        mock_route_tool_with_llama.return_value = TravelToolCall(tool="general_help")

        response = chat(
            ChatRequest(message="hello"),
            db=self.db,
            current_user=None,
        )

        demo_user = self.db.query(User).filter(User.email == "demo@example.com").one()
        self.assertEqual(response.intent.intent_type, "general_enquiry")
        self.assertEqual(len(demo_user.sessions), 1)

    def test_optional_auth_invalid_token_returns_guest_none(self) -> None:
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="not-a-valid-token",
        )

        user = get_optional_current_user(credentials=credentials, db=self.db)

        self.assertIsNone(user)

    def test_authenticated_booking_is_owned_by_authenticated_user(self) -> None:
        user = self._registered_user()

        booking = create_booking(
            BookingCreateRequest(destination="JFK", summary="Authenticated test booking"),
            db=self.db,
            current_user=user,
        )
        stored = self.db.query(Booking).filter(Booking.id == booking.id).one()

        self.assertEqual(stored.user_id, user.id)
        self.assertEqual(stored.session.user_id, user.id)


if __name__ == "__main__":
    unittest.main()
