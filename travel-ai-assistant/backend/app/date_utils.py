"""Small, dependency-free date helpers for safe travel searches."""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import re


class TravelDateValidationError(ValueError):
    """Raised when a travel date cannot be safely used for a future search."""


MONTH_NAMES = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
MONTH_PATTERN = "|".join(sorted(MONTH_NAMES, key=len, reverse=True))
DAY_FIRST_PATTERN = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>{MONTH_PATTERN})\b",
    re.IGNORECASE,
)
MONTH_FIRST_PATTERN = re.compile(
    rf"\b(?P<month>{MONTH_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
NUMERIC_DAY_MONTH_PATTERN = re.compile(r"\b(?P<day>\d{1,2})[/-](?P<month>\d{1,2})\b")
EXPLICIT_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")


def today_utc_date() -> date:
    """Return the current UTC date, isolated for deterministic tests."""

    return datetime.now(timezone.utc).date()


def resolve_relative_travel_date(value: object, today: date | None = None) -> str | None:
    """Resolve a small, intentional set of relative travel date phrases."""

    normalized = re.sub(r"\s+", " ", str(value or "").lower()).strip()
    reference = today or today_utc_date()
    if (
        "same day next month" in normalized
        or "this day next month" in normalized
        or "next month same day" in normalized
        or "next month on the same day" in normalized
    ):
        next_month = reference.month + 1
        year = reference.year
        if next_month > 12:
            next_month = 1
            year += 1
        day = min(reference.day, monthrange(year, next_month)[1])
        return date(year, next_month, day).isoformat()
    if "today" in normalized:
        return reference.isoformat()
    if "day after tomorrow" in normalized:
        return (reference + timedelta(days=2)).isoformat()
    if "tomorrow" in normalized:
        return (reference + timedelta(days=1)).isoformat()
    if "next week" in normalized:
        return (reference + timedelta(days=7)).isoformat()
    return None


def has_explicit_year(value: object) -> bool:
    return bool(EXPLICIT_YEAR_PATTERN.search(str(value or "")))


def extract_yearless_month_days(value: object) -> list[tuple[int, int]]:
    """Return month/day pairs from natural language that contains no year."""

    text = str(value or "")
    if has_explicit_year(text):
        return []

    matches: list[tuple[int, int, int]] = []
    for pattern in (DAY_FIRST_PATTERN, MONTH_FIRST_PATTERN):
        for match in pattern.finditer(text):
            month = MONTH_NAMES[match.group("month").lower()]
            day = int(match.group("day"))
            matches.append((match.start(), month, day))

    for match in NUMERIC_DAY_MONTH_PATTERN.finditer(text):
        matches.append((match.start(), int(match.group("month")), int(match.group("day"))))

    unique: list[tuple[int, int]] = []
    for _position, month, day in sorted(matches):
        try:
            date(2000, month, day)
        except ValueError:
            continue
        pair = (month, day)
        if pair not in unique:
            unique.append(pair)
    return unique


def next_future_month_day(month: int, day: int, today: date | None = None) -> date:
    """Resolve a month/day to the first occurrence that is today or later."""

    reference = today or today_utc_date()
    candidate = date(reference.year, month, day)
    if candidate < reference:
        candidate = date(reference.year + 1, month, day)
    return candidate


def _parse_date_like(value: object) -> tuple[date | None, bool]:
    """Return a parsed date and whether its source explicitly carried a year."""

    if isinstance(value, datetime):
        return value.date(), True
    if isinstance(value, date):
        return value, True
    if not isinstance(value, str):
        return None, False

    text = value.strip()
    if not text:
        return None, False
    try:
        return date.fromisoformat(text), True
    except ValueError:
        pass

    month_days = extract_yearless_month_days(text)
    if len(month_days) != 1:
        return None, False
    month, day = month_days[0]
    return date(2000, month, day), False


def resolve_future_date(value: object, today: date | None = None) -> str | None:
    """Return an ISO date, resolving an unyearred month/day into the future."""

    parsed, has_year = _parse_date_like(value)
    if parsed is None:
        return None
    if has_year:
        return parsed.isoformat()
    return next_future_month_day(parsed.month, parsed.day, today).isoformat()


def resolve_future_iso_date(
    date_str: object,
    today: date | None = None,
    allow_past: bool = False,
) -> tuple[str | None, bool]:
    """Resolve an ISO or natural date and report whether year inference occurred."""

    reference = today or today_utc_date()
    parsed, has_year = _parse_date_like(date_str)
    if parsed is None:
        return None, False
    if not has_year:
        resolved = next_future_month_day(parsed.month, parsed.day, reference)
        return resolved.isoformat(), True
    if parsed < reference and not allow_past:
        return None, False
    return parsed.isoformat(), False


def validate_not_past(date_str: object, today: date | None = None) -> bool:
    resolved, _corrected = resolve_future_iso_date(date_str, today=today)
    return resolved is not None


def normalise_trip_dates(
    departure: object,
    return_date: object | None = None,
    today: date | None = None,
) -> tuple[str, str | None]:
    """Validate future trip dates and safely roll over an unyearred return date."""

    reference = today or today_utc_date()
    departure_iso, _departure_corrected = resolve_future_iso_date(
        departure,
        today=reference,
    )
    if departure_iso is None:
        raise TravelDateValidationError(
            "Travel dates must be today or in the future. Please choose a future departure date."
        )
    departure_date = date.fromisoformat(departure_iso)
    if return_date is None:
        return departure_iso, None

    return_iso, return_corrected = resolve_future_iso_date(
        return_date,
        today=reference,
    )
    if return_iso is None:
        raise TravelDateValidationError(
            "Travel dates must be today or in the future. Please choose a future return date."
        )
    parsed_return, return_has_year = _parse_date_like(return_date)
    assert parsed_return is not None
    return_value = date.fromisoformat(return_iso)
    if return_value <= departure_date:
        if not return_has_year and return_corrected:
            return_value = date(departure_date.year, parsed_return.month, parsed_return.day)
            if return_value <= departure_date:
                return_value = date(
                    departure_date.year + 1,
                    parsed_return.month,
                    parsed_return.day,
                )
            return_iso = return_value.isoformat()
        else:
            raise TravelDateValidationError(
                "Return date must be after the departure date."
            )
    return departure_iso, return_iso


def normalise_hotel_dates(
    check_in: object,
    check_out: object,
    today: date | None = None,
) -> tuple[str, str]:
    """Validate a future hotel stay with a checkout after check-in."""

    reference = today or today_utc_date()
    check_in_iso, _check_in_corrected = resolve_future_iso_date(
        check_in,
        today=reference,
    )
    if check_in_iso is None:
        raise TravelDateValidationError(
            "Hotel check-in must be today or in the future. Please choose a future check-in date."
        )
    check_out_iso, check_out_corrected = resolve_future_iso_date(
        check_out,
        today=reference,
    )
    if check_out_iso is None:
        raise TravelDateValidationError(
            "Hotel check-out must be after a future check-in date."
        )

    check_in_date = date.fromisoformat(check_in_iso)
    check_out_date = date.fromisoformat(check_out_iso)
    parsed_check_out, check_out_has_year = _parse_date_like(check_out)
    assert parsed_check_out is not None
    if check_out_date <= check_in_date:
        if not check_out_has_year and check_out_corrected:
            check_out_date = date(
                check_in_date.year,
                parsed_check_out.month,
                parsed_check_out.day,
            )
            if check_out_date <= check_in_date:
                check_out_date = date(
                    check_in_date.year + 1,
                    parsed_check_out.month,
                    parsed_check_out.day,
                )
            check_out_iso = check_out_date.isoformat()
        else:
            raise TravelDateValidationError(
                "Hotel check-out date must be after the check-in date."
            )
    return check_in_iso, check_out_iso
