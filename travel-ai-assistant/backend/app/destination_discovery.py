"""Deterministic destination recommendation using seeds plus safe evidence."""

from typing import Any

from app.airport_lookup import haversine_km
from app.destination_candidates import DESTINATION_CANDIDATES
from app.providers.opentripmap_provider import get_destination_evidence
from app.schemas import (
    DestinationOrigin,
    DestinationDiscoveryRequest,
    DestinationDiscoveryResponse,
    DestinationRecommendation,
)


def _normalise_tags(values: list[str] | None) -> set[str]:
    return {str(value).lower().replace(" ", "_") for value in values or []}


def _normalise_region(value: str | None) -> str:
    return str(value or "").lower().replace(" ", "_")


def _region_matches(candidate_region: str | None, requested_regions: set[str]) -> bool:
    if not requested_regions:
        return True
    region = _normalise_region(candidate_region)
    for requested in requested_regions:
        if requested == "europe" and region in {"europe", "europe-adjacent"}:
            return True
        if requested == region:
            return True
    return False


def _has_strong_skiing_match(tags: set[str], evidence: dict[str, Any]) -> bool:
    return bool(
        {"skiing", "skiing_nearby", "winter_sports", "ski_resort"} & tags
        or evidence.get("skiEvidenceCount", 0)
    )


def _departure_month(request: DestinationDiscoveryRequest) -> int | None:
    dates = request.dates if isinstance(request.dates, dict) else {}
    departure = dates.get("departure")
    if not departure:
        return None
    try:
        return int(str(departure).split("-")[1])
    except (IndexError, TypeError, ValueError):
        return None


def _fallback_activity_evidence(candidate: dict[str, Any], activity: str) -> str | None:
    values = candidate.get("fallbackActivityEvidence")
    if not isinstance(values, dict):
        return None
    value = values.get(activity)
    return str(value) if value else None


def _origin_coordinates(origin: DestinationOrigin | None) -> tuple[float, float] | None:
    if origin is None:
        return None
    if origin.latitude is not None and origin.longitude is not None:
        return float(origin.latitude), float(origin.longitude)
    return None


def _estimated_flight_minutes(origin: tuple[float, float] | None, destination: dict[str, Any]) -> int | None:
    if origin is None:
        return None
    distance = haversine_km(origin[0], origin[1], float(destination["lat"]), float(destination["lon"]))
    return int(round(distance / 750 * 60 + 30))


def discover_destinations(
    request: DestinationDiscoveryRequest,
    limit: int = 5,
) -> DestinationDiscoveryResponse:
    preferences = {
        "environment": request.environment_preferences,
        "activity": request.activity_preferences,
        "tripPurpose": request.trip_purpose,
        "travellers": request.travellers.model_dump(),
    }
    environment = _normalise_tags(request.environment_preferences)
    activity = _normalise_tags(request.activity_preferences)
    required_activities = _normalise_tags(request.required_activities)
    regions = _normalise_tags(request.region_preferences)
    excluded_regions = _normalise_tags(request.excluded_regions)
    wants_skiing = bool({"skiing", "ski"} & (activity | required_activities))
    requires_skiing = bool({"skiing", "ski"} & required_activities)
    wants_general_enjoyment = bool(
        {"attractions", "sightseeing", "activities", "things_to_do", "fun_places"}
        & activity
    ) or str(request.trip_purpose or "").lower() in {"general_holiday", "leisure"}
    beach_activity_terms = {
        "beach",
        "beach_activities",
        "water_sports",
        "swimming",
        "snorkelling",
        "snorkeling",
        "diving",
    }
    wants_beach = bool(
        {"beach", "sea", "summer"} & environment
        or beach_activity_terms & (activity | required_activities)
    )
    wants_family = request.travellers.children > 0 or bool({"family", "kids", "children"} & (
        environment | activity | required_activities | _normalise_tags(request.soft_preferences)
    ))
    origin_coords = _origin_coordinates(request.origin)
    departure_month = _departure_month(request)

    scored: list[tuple[int, DestinationRecommendation, bool]] = []
    for candidate in DESTINATION_CANDIDATES:
        tags = _normalise_tags(candidate.get("fallbackTags"))
        try:
            evidence = get_destination_evidence(candidate, preferences)
        except Exception:
            evidence = {}
        has_provider_evidence = bool(evidence.get("evidenceNames"))
        score = 35
        reasons: list[str] = []
        warnings: list[str] = []
        candidate_region = str(candidate.get("region") or "")
        if _normalise_region(candidate_region) in excluded_regions:
            continue
        if regions and not _region_matches(candidate_region, regions):
            continue
        strong_skiing_match = _has_strong_skiing_match(tags, evidence)
        live_skiing_match = bool(evidence.get("skiEvidenceCount", 0))
        trusted_skiing_fallback = bool(
            {"skiing", "skiing_nearby", "winter_sports", "ski_resort"} & tags
        )
        if requires_skiing and not strong_skiing_match:
            continue
        beach_only_for_skiing = (
            wants_skiing
            and not wants_beach
            and bool({"beach", "sea", "summer", "relaxing"} & tags)
            and not strong_skiing_match
        )
        if beach_only_for_skiing:
            continue

        if environment & tags:
            score += 30
            reasons.append(f"Matches your {', '.join(sorted(environment & tags))} preference.")
        elif environment:
            score -= 8
            warnings.append("Environmental match is weaker for this destination.")

        beach_activity_matches = beach_activity_terms & tags
        if wants_beach and beach_activity_matches:
            score += 22
            if {"water_sports", "snorkelling", "snorkeling", "diving"} & beach_activity_matches:
                reasons.append("Suitable for beach activities and water activities.")
            else:
                reasons.append("Matches your beach preference.")

        if wants_skiing and strong_skiing_match:
            if live_skiing_match:
                score += 35
                reasons.append("Live skiing or winter-sports evidence matches your request.")
            else:
                score += 26 if "ski_resort" in tags else 22
                reasons.append("Trusted fallback skiing knowledge matches your request.")
                warnings.append("Skiing confidence is fallback-based rather than live place evidence.")
            if (
                departure_month is not None
                and departure_month not in {12, 1, 2, 3}
                and "indoor_skiing" not in tags
            ):
                score -= 15
                warnings.append(
                    "Outdoor skiing may not be available in August. This destination is known for skiing, but your dates may be outside the normal ski season."
                    if departure_month == 8
                    else "Outdoor skiing may not be available for your travel month. This destination is known for skiing, but your dates may be outside the normal ski season."
                )
        elif wants_skiing:
            score -= 45
            warnings.append("No strong skiing evidence was found for this destination.")
        elif wants_general_enjoyment:
            general_tags = {
                "culture",
                "city_break",
                "history",
                "nightlife",
                "beach",
                "sea",
                "family",
                "food",
                "relaxing",
                "summer",
            }
            matching_general_tags = general_tags & tags
            if matching_general_tags:
                score += 28
                reasons.append(
                    "Matches your interest in attractions, sightseeing, or things to do."
                )
            else:
                score -= 6
                warnings.append("General activity evidence is weaker for this destination.")
        elif activity & tags:
            score += 30
            reasons.append(f"Matches your {', '.join(sorted(activity & tags))} activity preference.")
        elif activity:
            score -= 10
            warnings.append("Activity evidence is limited for this destination.")

        if wants_family and ("family" in tags or evidence.get("familyEvidenceCount", 0)):
            score += 15
            reasons.append("Suitable for family travel.")

        estimated_minutes = _estimated_flight_minutes(origin_coords, candidate)
        if regions:
            score += 18
            reasons.append(f"Matches your {candidate_region} region preference.")

        if request.max_flight_duration_minutes and estimated_minutes is not None:
            if estimated_minutes <= request.max_flight_duration_minutes:
                score += 20
                reasons.append("Estimated flight duration is within your limit.")
            else:
                score -= 25
                warnings.append(
                    f"Estimated flight time is about {estimated_minutes} minutes, above your limit."
                )
        elif request.short_flight_preference and estimated_minutes is not None:
            if estimated_minutes < 120:
                score += 45
                reasons.append("This is a shorter-flight option.")
            elif estimated_minutes <= 180:
                score += 35
                reasons.append("Flight duration is moderate.")
            else:
                score -= 35
                warnings.append("This involves a longer flight.")

        if (
            wants_skiing
            and regions
            and request.short_flight_preference
            and strong_skiing_match
            and estimated_minutes is not None
            and estimated_minutes > 180
        ):
            warnings.append(
                "Trade-off: stronger skiing suitability may require a longer flight."
            )

        if has_provider_evidence and not (wants_skiing and not live_skiing_match):
            score += 10
        else:
            score -= 5

        if not reasons:
            reasons.append("Good general travel destination from the current candidate set.")

        if wants_skiing and trusted_skiing_fallback and not live_skiing_match:
            score = min(score, 80)
            if (
                departure_month is not None
                and departure_month not in {12, 1, 2, 3}
                and "indoor_skiing" not in tags
            ):
                score = min(score, 65)
        elif wants_skiing and live_skiing_match and departure_month not in {None, 12, 1, 2, 3}:
            score = min(score, 88)
        score = max(0, min(100, score))
        evidence_names = evidence.get("evidenceNames") or []
        ski_evidence_names = evidence.get("skiEvidenceNames") or []
        if wants_skiing:
            if ski_evidence_names:
                activity_summary = (
                    "Activity evidence: OpenTripMap ski evidence found: "
                    + ", ".join(ski_evidence_names[:3])
                    + "."
                )
            elif trusted_skiing_fallback:
                activity_summary = (
                    "Activity evidence: "
                    + (
                        _fallback_activity_evidence(candidate, "skiing")
                        or "Trusted fallback skiing knowledge for this destination."
                    )
                )
            else:
                activity_summary = "Activity evidence: No strong skiing evidence found."
            general_summary = (
                " General attractions: " + ", ".join(evidence_names[:3]) + "."
                if evidence_names
                else " General attractions: OpenTripMap evidence was unavailable."
            )
            evidence_summary = activity_summary + general_summary
        else:
            evidence_summary = (
                "General attractions: " + ", ".join(evidence_names[:3])
                if evidence_names
                else "OpenTripMap evidence was unavailable, so fallback destination tags were used."
            )

        scored.append(
            (
                score,
                DestinationRecommendation(
                    city=candidate["city"],
                    country=candidate["country"],
                    airportCode=candidate["airportCode"],
                    airportName=candidate["airportName"],
                    region=candidate_region or None,
                    score=score,
                    matchReasons=reasons,
                    warnings=warnings,
                    evidenceSummary=evidence_summary,
                    estimatedFlightMinutes=estimated_minutes,
                ),
                has_provider_evidence,
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    provider_used = "opentripmap" if any(item[2] for item in scored) else "fallback_tags"
    return DestinationDiscoveryResponse(
        provider_used=provider_used,
        recommendations=[item[1] for item in scored[: max(1, min(limit, 5))]],
        message="I found destination ideas based on your preferences.",
    )
