from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TravelIntentType = Literal[
    "flight_search",
    "hotel_search",
    "package_search",
    "package_option_view",
    "package_option_selection",
    "booking_retrieval",
    "booking_confirmation",
    "booking_cancellation",
    "general_enquiry",
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    session_id: int | None = Field(default=None, alias="sessionId")


class TravelIntent(BaseModel):
    intent_type: TravelIntentType
    destination: str | None = None
    booking_reference: str | None = None
    departure_city: str | None = None
    travelers: int = 1
    budget_level: str | None = None
    message_summary: str


class TravelResults(BaseModel):
    flights: list[dict[str, Any]] = Field(default_factory=list)
    hotels: list[dict[str, Any]] = Field(default_factory=list)
    destinations: list[dict[str, Any]] = Field(default_factory=list)
    packages: list[dict[str, Any]] = Field(default_factory=list)
    cart: dict[str, Any] | None = None
    checkout: dict[str, Any] | None = None
    flight_ticket: dict[str, Any] | None = Field(default=None, alias="flightTicket")
    hotel_voucher: dict[str, Any] | None = Field(default=None, alias="hotelVoucher")
    booking: dict[str, Any] | None = None
    suggestions: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reply: str
    intent: TravelIntent
    results: TravelResults
    requires_confirmation: bool = False
    pending_action: str | None = None
    session_id: int | None = Field(default=None, alias="sessionId")
    next_action: str | None = Field(default=None, alias="nextAction")
    next_path: str | None = Field(default=None, alias="nextPath")
    redirect_to: str | None = None


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resolved: bool
    departure_date: str | None = None
    return_date: str | None = None
    nights: int | None = None
    origin_text: str | None = None
    origin_code: str | None = None


class BookingCreateRequest(BaseModel):
    destination: str
    summary: str | None = None
    flight_id: str | None = None
    hotel_id: str | None = None
    estimated_price: int | None = None
    currency: str = "USD"


class BookingDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    reference: str
    status: str
    destination: str
    summary: str
    flight_id: str | None = None
    hotel_id: str | None = None
    estimated_price: int | None = None
    currency: str
    booking_type: str = "manual"
    created_at: datetime
    total_price: float | None = Field(default=None, alias="totalPrice")
    package_option: dict[str, Any] | None = Field(default=None, alias="packageOption")
    package_summary: dict[str, Any] | None = Field(default=None, alias="packageSummary")
    flight_ticket: dict[str, Any] | None = Field(default=None, alias="flightTicket")
    hotel_voucher: dict[str, Any] | None = Field(default=None, alias="hotelVoucher")
    checkout_details: dict[str, Any] | None = Field(default=None, alias="checkoutDetails")
    details_available: bool = Field(default=False, alias="detailsAvailable")


class BookingListResponse(BaseModel):
    bookings: list[BookingDetails] = Field(default_factory=list)


CheckoutItemType = Literal["flight", "hotel", "package"]


class CheckoutItem(BaseModel):
    """A public, normalised cart item submitted for checkout."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: CheckoutItemType
    title: str = Field(min_length=1, max_length=180)
    subtitle: str | None = Field(default=None, max_length=300)
    provider: str | None = Field(default=None, max_length=80)
    price: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    item_data: dict[str, Any] = Field(default_factory=dict)


class CheckoutCustomer(BaseModel):
    """Contact details used only to validate a checkout request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=40)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CheckoutItem] = Field(min_length=1, max_length=20)
    customer: CheckoutCustomer
    notes: str | None = Field(default=None, max_length=1000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class CheckoutResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    booking_reference: str
    booking_type: Literal["cart_checkout"] = "cart_checkout"
    summary: str
    total_price: float | None = None
    currency: str | None = None
    totals_by_currency: dict[str, float] = Field(default_factory=dict)
    item_count: int
    message: str
    package_option: dict[str, Any] | None = Field(default=None, alias="packageOption")
    package_summary: dict[str, Any] | None = Field(default=None, alias="packageSummary")
    flight_ticket: dict[str, Any] | None = Field(default=None, alias="flightTicket")
    hotel_voucher: dict[str, Any] | None = Field(default=None, alias="hotelVoucher")
    cart_cleared: bool = Field(default=True, alias="cartCleared")


CabinClass = Literal["economy", "premium_economy", "business", "first"]
TripType = Literal["one_way", "round_trip"]


class FlightAirport(BaseModel):
    city: str | None = None
    code: str = Field(min_length=3, max_length=3)


class FlightDates(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    departure: date
    return_date: date | None = Field(default=None, alias="return")


class FlightItinerary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    trip_type: TripType = Field(alias="tripType")
    origin: FlightAirport
    destination: FlightAirport
    dates: FlightDates

    @model_validator(mode="after")
    def require_return_for_round_trip(self) -> "FlightItinerary":
        if self.trip_type == "round_trip" and self.dates.return_date is None:
            raise ValueError("round_trip searches require dates.return")
        return self


class FlightPreferences(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cabin_class: Literal["economy", "premium_economy", "business", "first"] = Field(
        default="economy",
        alias="cabinClass",
    )
    max_stops: int | None = Field(default=None, alias="maxStops", ge=0)


class FlightSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    search_type: Literal["flight"] = Field(default="flight", alias="searchType")
    passenger_count: int = Field(default=1, alias="passengerCount", ge=1)
    itinerary: FlightItinerary
    preferences: FlightPreferences = Field(default_factory=FlightPreferences)


class NormalisedFlightResult(BaseModel):
    id: str
    offer_id: str | None = None
    display_id: str | None = None
    option_number: int | None = Field(default=None, ge=1)
    origin: str
    destination: str
    airline: str | None = None
    outbound_departure_date: str | None = None
    outbound_departure_time: str | None = None
    outbound_arrival_date: str | None = None
    outbound_arrival_time: str | None = None
    return_departure_date: str | None = None
    return_departure_time: str | None = None
    return_arrival_date: str | None = None
    return_arrival_time: str | None = None
    # Legacy ISO timestamp fields remain for API compatibility. New clients
    # should use the split display fields above.
    departure_date: str | None = None
    return_date: str | None = None
    stops: int | None = None
    stops_label: str | None = None
    duration_minutes: int | None = None
    duration_label: str | None = None
    cabin_class: CabinClass = "economy"
    cabin_class_label: str | None = None
    passenger_count: int = Field(default=1, ge=1)
    price: str | None = None
    currency: str | None = None
    provider: str


class FlightSearchResponse(BaseModel):
    provider_used: Literal["duffel", "mock_fallback", "unavailable"]
    flights: list[NormalisedFlightResult] = Field(default_factory=list)
    fallback_reason: str | None = None


class HotelDestination(BaseModel):
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class HotelPreferences(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    min_rating: float | None = Field(default=None, alias="minRating", ge=0, le=10)
    max_price: float | None = Field(default=None, alias="maxPrice", ge=0)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    board_type: str | None = Field(default=None, alias="boardType", max_length=40)
    amenities: list[str] = Field(default_factory=list, max_length=20)


class HotelSearchRequest(BaseModel):
    """Validated hotel search contract shared by hotel providers."""

    model_config = ConfigDict(populate_by_name=True)

    search_type: Literal["hotel"] = Field(default="hotel", alias="searchType")
    destination: HotelDestination
    check_in_date: date = Field(alias="checkInDate")
    check_out_date: date = Field(alias="checkOutDate")
    guest_count: int = Field(default=1, alias="guestCount", ge=1, le=9)
    room_count: int = Field(default=1, alias="roomCount", ge=1, le=5)
    preferences: HotelPreferences = Field(default_factory=HotelPreferences)

    @model_validator(mode="after")
    def validate_hotel_search(self) -> "HotelSearchRequest":
        has_city = bool(self.destination.city and self.destination.city.strip())
        has_coordinates = (
            self.destination.latitude is not None
            and self.destination.longitude is not None
        )
        if not has_city and not has_coordinates:
            raise ValueError("Hotel searches require destination.city or coordinates.")
        if self.check_out_date <= self.check_in_date:
            raise ValueError("checkOutDate must be after checkInDate.")
        return self


class NormalisedHotelResult(BaseModel):
    id: str
    option_number: int = Field(ge=1)
    hotel_name: str
    location: str
    check_in_date: str
    check_out_date: str
    nights: int = Field(ge=1)
    guest_count: int = Field(ge=1)
    room_count: int = Field(ge=1)
    rating: float | None = Field(default=None, ge=0, le=10)
    board_type: str | None = None
    room_name: str | None = None
    refundable: bool | None = None
    price: float = Field(ge=0)
    currency: str
    provider: str
    short_id: str | None = None
    description: str | None = None
    amenities: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None


class HotelSearchResponse(BaseModel):
    provider_used: Literal["liteapi", "mock_hotel_fallback", "unavailable"]
    results: list[NormalisedHotelResult] = Field(default_factory=list)
    message: str | None = None


class PackageBuildRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    origin_airport_code: str = Field(alias="originAirportCode", min_length=3, max_length=3)
    destination_airport_code: str = Field(alias="destinationAirportCode", min_length=3, max_length=3)
    destination_city: str = Field(alias="destinationCity", min_length=1, max_length=120)
    destination_country: str | None = Field(default=None, alias="destinationCountry", max_length=120)
    departure_date: date = Field(alias="departureDate")
    return_date: date = Field(alias="returnDate")
    passenger_count: int = Field(default=1, alias="passengerCount", ge=1)
    rooms: int = Field(default=1, ge=1)
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    cabin: CabinClass = "economy"
    hotel_star_rating: int | None = Field(default=None, alias="hotelStarRating", ge=1, le=5)
    hotel_preference: str | None = Field(default=None, alias="hotelPreference", max_length=120)
    max_budget: float | None = Field(default=None, alias="maxBudget", ge=0)
    max_packages: int = Field(default=5, alias="maxPackages", ge=1, le=10)

    @model_validator(mode="after")
    def validate_package_dates(self) -> "PackageBuildRequest":
        if self.return_date <= self.departure_date:
            raise ValueError("returnDate must be after departureDate.")
        return self


class PackageFlightSummary(BaseModel):
    offerId: str
    airline: str | None = None
    origin: str
    destination: str
    departureDate: str
    returnDate: str | None = None
    departureTime: str | None = None
    returnTime: str | None = None
    cabin: str
    stops: int | None = None
    duration: str | None = None
    price: float
    currency: str
    provider: str


class PackageHotelSummary(BaseModel):
    hotelId: str | None = None
    hotelName: str
    city: str
    checkInDate: str
    checkOutDate: str
    nights: int = Field(ge=1)
    rating: float | None = None
    address: str | None = None
    boardType: str | None = None
    roomName: str | None = None
    price: float
    currency: str
    provider: str


class PackageOption(BaseModel):
    packageId: str
    optionNumber: int = Field(ge=1)
    destinationCity: str
    destinationAirportCode: str
    flight: PackageFlightSummary
    hotel: PackageHotelSummary
    totalPrice: float
    currency: str
    nights: int = Field(ge=1)
    passengers: int = Field(ge=1)
    rooms: int = Field(ge=1)
    recommendationScore: int | None = Field(default=None, ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    providerSummary: str
    activeTripRevision: int | None = None


class PackageBuildResponse(BaseModel):
    message: str
    packages: list[PackageOption] = Field(default_factory=list)
    requiresCartAction: bool = True


class PackageCartItem(BaseModel):
    cartItemId: str
    itemType: Literal["package"] = "package"
    packageOption: PackageOption
    quantity: int = Field(default=1, ge=1)
    addedAt: datetime
    status: Literal["in_cart"] = "in_cart"


class PackageCartSummary(BaseModel):
    cartId: str
    items: list[PackageCartItem] = Field(default_factory=list)
    totalPrice: float = 0
    currency: str = "GBP"
    status: Literal["active", "checked_out"] = "active"


class FlightTicket(BaseModel):
    ticketNumber: str
    passengerName: str | None = None
    airline: str | None = None
    origin: str
    destination: str
    departureDate: str
    returnDate: str | None = None
    departureTime: str | None = None
    returnTime: str | None = None
    cabin: str
    bookingReference: str
    status: Literal["confirmed"] = "confirmed"


class HotelVoucher(BaseModel):
    voucherNumber: str
    hotelName: str
    city: str
    checkInDate: str
    checkOutDate: str
    nights: int = Field(ge=1)
    roomName: str | None = None
    boardType: str | None = None
    guests: int = Field(ge=1)
    bookingReference: str
    status: Literal["confirmed"] = "confirmed"


class PackageCheckoutRequest(BaseModel):
    cartId: str
    confirmCheckout: bool = True


class PackageCheckoutResponse(BaseModel):
    message: str
    bookingReference: str
    cart: PackageCartSummary
    flightTicket: FlightTicket
    hotelVoucher: HotelVoucher
    totalPrice: float
    currency: str


class CurrentLocationRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DefaultOrigin(BaseModel):
    city: str
    country: str | None = None
    airportCode: str = Field(min_length=3, max_length=3)
    airportName: str | None = None
    source: str


class ActiveTripOrigin(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    city: str | None = None
    airport_code: str | None = Field(default=None, alias="airportCode", min_length=3, max_length=3)
    airport_name: str | None = Field(default=None, alias="airportName", max_length=160)
    country: str | None = None
    source: str | None = None


class ActiveTripDestination(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    city: str | None = None
    country: str | None = None
    airport_code: str | None = Field(default=None, alias="airportCode", min_length=3, max_length=3)
    airport_name: str | None = Field(default=None, alias="airportName", max_length=160)
    source: str | None = None
    recommendation_id: str | None = Field(default=None, alias="recommendationId")


class ActiveTripDates(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    departure_date: date | None = Field(default=None, alias="departureDate")
    return_date: date | None = Field(default=None, alias="returnDate")
    source: str | None = None


class ActiveTripState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    revision: int = Field(default=0, ge=0)
    origin: ActiveTripOrigin | None = None
    selected_destination: ActiveTripDestination | None = Field(
        default=None,
        alias="selectedDestination",
    )
    destination_region: dict[str, Any] | None = Field(
        default=None,
        alias="destinationRegion",
    )
    destination_type: str | None = Field(default=None, alias="destinationType")
    destination_candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="destinationCandidates",
    )
    trip_frame: str | None = Field(default=None, alias="tripFrame")
    dates: ActiveTripDates = Field(default_factory=ActiveTripDates)
    trip_duration_days: int | None = Field(default=None, alias="tripDurationDays", ge=1)
    trip_duration_nights: int | None = Field(default=None, alias="tripDurationNights", ge=1)
    budget: float | None = Field(default=None, ge=0)
    budget_currency: str | None = Field(default=None, alias="budgetCurrency", min_length=3, max_length=3)
    required_activities: list[str] = Field(default_factory=list, alias="requiredActivities")
    activity_preferences: list[str] = Field(default_factory=list, alias="activityPreferences")
    environment_preferences: list[str] = Field(default_factory=list, alias="environmentPreferences")
    region_preferences: list[str] = Field(default_factory=list, alias="regionPreferences")
    excluded_regions: list[str] = Field(default_factory=list, alias="excludedRegions")
    requested_products: list[str] = Field(default_factory=list, alias="requestedProducts")
    status: str = "idle"
    passenger_count: int = Field(default=1, alias="passengerCount", ge=1)
    cabin_class: CabinClass = Field(default="economy", alias="cabinClass")
    latest_date_price_comparison: dict[str, Any] | None = Field(
        default=None,
        alias="latestDatePriceComparison",
    )
    selected_flight_candidate: dict[str, Any] | None = Field(
        default=None,
        alias="selectedFlightCandidate",
    )
    pending_flight_request: dict[str, Any] | None = Field(
        default=None,
        alias="pendingFlightRequest",
    )
    pending_trip_request: dict[str, Any] | None = Field(
        default=None,
        alias="pendingTripRequest",
    )
    pending_alternative_origin_search: dict[str, Any] | None = Field(
        default=None,
        alias="pendingAlternativeOriginSearch",
    )
    pending_action: dict[str, Any] | None = Field(
        default=None,
        alias="pendingAction",
    )
    date_text: str | None = Field(default=None, alias="dateText")
    month: str | None = None
    multi_city_draft: dict[str, Any] | None = Field(
        default=None,
        alias="multiCityDraft",
    )
    latest_package_request: dict[str, Any] | None = Field(
        default=None,
        alias="latestPackageRequest",
    )
    pending_package_request: dict[str, Any] | None = Field(
        default=None,
        alias="pendingPackageRequest",
    )
    pending_hotel_request: dict[str, Any] | None = Field(
        default=None,
        alias="pendingHotelRequest",
    )
    selected_flight_option: dict[str, Any] | None = Field(
        default=None,
        alias="selectedFlightOption",
    )
    selected_hotel_option: dict[str, Any] | None = Field(
        default=None,
        alias="selectedHotelOption",
    )
    latest_package_options: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="latestPackageOptions",
    )
    current_package_option: dict[str, Any] | None = Field(
        default=None,
        alias="currentPackageOption",
    )
    selected_package_option: dict[str, Any] | None = Field(
        default=None,
        alias="selectedPackageOption",
    )
    cart: dict[str, Any] | None = None
    cart_status: str | None = Field(default=None, alias="cartStatus")
    latest_cart_id: str | None = Field(default=None, alias="latestCartId")
    checkout: dict[str, Any] | None = None
    flight_ticket: dict[str, Any] | None = Field(default=None, alias="flightTicket")
    hotel_voucher: dict[str, Any] | None = Field(default=None, alias="hotelVoucher")
    latest_visible_result_type: str | None = Field(default=None, alias="latestVisibleResultType")
    last_clarification: dict[str, Any] | None = Field(default=None, alias="lastClarification")


class CurrentLocationResponse(BaseModel):
    defaultOrigin: DefaultOrigin
    message: str


class AirportSuggestion(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    name: str
    city: str
    country: str
    display_name: str = Field(alias="displayName")
    provider: str
    source: Literal["provider_api", "provider_cache"]


class HotelLocationSuggestion(BaseModel):
    city: str
    country: str
    display_name: str = Field(alias="displayName")
    provider_location_id: str | None = Field(default=None, alias="providerLocationId")
    latitude: float | None = None
    longitude: float | None = None
    provider: str
    source: Literal["provider_api", "provider_cache"]


class HomepageFlightDeal(BaseModel):
    id: str
    origin_airport_code: str = Field(alias="originAirportCode")
    origin_city: str = Field(alias="originCity")
    origin_display_name: str = Field(alias="originDisplayName")
    destination_airport_code: str = Field(alias="destinationAirportCode")
    destination_city: str = Field(alias="destinationCity")
    destination_display_name: str = Field(alias="destinationDisplayName")
    departure_date: str = Field(alias="departureDate")
    return_date: str | None = Field(default=None, alias="returnDate")
    airline: str | None = None
    price: float | None = None
    currency: str | None = None
    duration: str | None = None
    stops: str | None = None
    provider: str
    source: Literal["provider_api", "provider_cache"]
    search_url: str = Field(alias="searchUrl")


class HomepagePackageDeal(BaseModel):
    id: str
    origin_airport_code: str = Field(alias="originAirportCode")
    origin_city: str = Field(alias="originCity")
    destination_airport_code: str = Field(alias="destinationAirportCode")
    destination_city: str = Field(alias="destinationCity")
    destination_country: str = Field(alias="destinationCountry")
    departure_date: str = Field(alias="departureDate")
    return_date: str = Field(alias="returnDate")
    nights: int
    flight_price: float = Field(alias="flightPrice")
    hotel_price: float = Field(alias="hotelPrice")
    total_price: float = Field(alias="totalPrice")
    currency: str
    hotel_name: str = Field(alias="hotelName")
    hotel_rating: float | None = Field(default=None, alias="hotelRating")
    provider_summary: str = Field(alias="providerSummary")
    source: Literal["provider_api", "provider_cache"]
    search_url: str = Field(alias="searchUrl")


class HomepageDestination(BaseModel):
    id: str
    city: str
    country: str
    display_name: str = Field(alias="displayName")
    airport_code: str | None = Field(default=None, alias="airportCode")
    provider_location_id: str | None = Field(default=None, alias="providerLocationId")
    tags: list[str] = Field(default_factory=list)
    source: Literal["provider_api", "provider_cache"]
    search_url: str = Field(alias="searchUrl")


class HomepageTripIdea(BaseModel):
    id: str
    theme: str
    city: str
    country: str
    display_name: str = Field(alias="displayName")
    airport_code: str | None = Field(default=None, alias="airportCode")
    estimated_flight_price: float | None = Field(default=None, alias="estimatedFlightPrice")
    estimated_package_price: float | None = Field(default=None, alias="estimatedPackagePrice")
    tags: list[str] = Field(default_factory=list)
    source: Literal["provider_api", "provider_cache"]
    voyager_prompt: str = Field(alias="voyagerPrompt")
    search_url: str = Field(alias="searchUrl")


class HomepagePropertyType(BaseModel):
    id: str
    label: str
    description: str
    search_url: str = Field(alias="searchUrl")


class TravellerGroup(BaseModel):
    adults: int = Field(default=1, ge=0, le=20)
    children: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def require_at_least_one_traveller(self) -> "TravellerGroup":
        if self.adults + self.children < 1:
            raise ValueError("At least one traveller is required.")
        return self


class DestinationOrigin(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    city: str | None = None
    code: str | None = Field(default=None, min_length=3, max_length=3)
    airport_name: str | None = Field(default=None, alias="airportName", max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class DestinationDiscoveryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    origin: DestinationOrigin | None = None
    trip_purpose: str | None = Field(default=None, alias="tripPurpose", max_length=80)
    travellers: TravellerGroup = Field(default_factory=TravellerGroup)
    environment_preferences: list[str] = Field(
        default_factory=list,
        alias="environmentPreferences",
        max_length=10,
    )
    activity_preferences: list[str] = Field(
        default_factory=list,
        alias="activityPreferences",
        max_length=10,
    )
    required_activities: list[str] = Field(
        default_factory=list,
        alias="requiredActivities",
        max_length=10,
    )
    region_preferences: list[str] = Field(
        default_factory=list,
        alias="regionPreferences",
        max_length=10,
    )
    excluded_regions: list[str] = Field(
        default_factory=list,
        alias="excludedRegions",
        max_length=10,
    )
    max_flight_duration_minutes: int | None = Field(
        default=None,
        alias="maxFlightDurationMinutes",
        ge=30,
        le=1440,
    )
    short_flight_preference: bool = Field(
        default=False,
        alias="shortFlightPreference",
    )
    soft_preferences: list[str] = Field(
        default_factory=list,
        alias="softPreferences",
        max_length=20,
    )
    hard_filters: list[str] = Field(
        default_factory=list,
        alias="hardFilters",
        max_length=20,
    )
    budget: float | None = Field(default=None, ge=0)
    trip_duration_nights: int | None = Field(
        default=None,
        alias="tripDurationNights",
        ge=1,
        le=365,
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        alias="missingFields",
        max_length=10,
    )
    created_at: str | None = Field(default=None, alias="createdAt", max_length=40)
    dates: dict[str, Any] | None = None

    @model_validator(mode="after")
    def default_explicit_activities_to_required(self) -> "DestinationDiscoveryRequest":
        if self.activity_preferences and not self.required_activities:
            self.required_activities = [
                value
                for value in self.activity_preferences
                if str(value).lower().replace(" ", "_") in {"ski", "skiing"}
            ]
        return self


class DestinationRecommendation(BaseModel):
    city: str
    country: str
    airportCode: str = Field(min_length=3, max_length=3)
    airportName: str | None = None
    region: str | None = None
    score: int = Field(ge=0, le=100)
    matchReasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidenceSummary: str
    estimatedFlightMinutes: int | None = None


class DestinationDiscoveryResponse(BaseModel):
    provider_used: Literal["opentripmap", "fallback_tags"]
    recommendations: list[DestinationRecommendation] = Field(default_factory=list)
    message: str | None = None


ChatAction = Literal[
    "flight_search",
    "hotel_search",
    "package_search",
    "show_cheapest_flight",
    "show_fastest_flight",
    "show_flight_option",
    "select_flight_option",
    "select_current_flight",
    "book_cheapest_flight",
    "confirm_booking",
    "cancel_booking",
    "booking_lookup",
    "general_help",
]


class LlamaChatActionRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ChatAction
    option_number: int | None = Field(default=None, ge=1)
    booking_reference: str | None = None


ToolName = Literal[
    "search_flights",
    "rank_flights",
    "show_flight_option",
    "select_flight",
    "check_flight_availability",
    "update_passenger_count",
    "describe_current_flight",
    "search_hotels",
    "rank_hotels",
    "show_hotel_option",
    "select_hotel",
    "confirm_hotel_booking",
    "discover_destinations",
    "select_destination",
    "search_trip_options",
    "prepare_trip_request",
    "handle_visible_results",
    "search_alternative_origins",
    "add_to_cart",
    "checkout",
    "cart_view",
    "confirm_booking",
    "cancel_booking",
    "booking_lookup",
    "general_help",
]


PlannerToolName = Literal[
    "rank_flights",
    "show_flight_option",
    "select_flight",
    "check_flight_availability",
    "update_passenger_count",
    "describe_current_flight",
    "rank_hotels",
    "show_hotel_option",
    "select_hotel",
    "confirm_hotel_booking",
    "discover_destinations",
    "select_destination",
    "search_trip_options",
    "prepare_trip_request",
    "handle_visible_results",
    "search_alternative_origins",
    "add_to_cart",
    "checkout",
    "cart_view",
    "confirm_booking",
    "cancel_booking",
    "booking_lookup",
    "general_help",
]


class TravelToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: ToolName = Field(
        description=(
            "Backend tool name. Use prepare_trip_request for conversational "
            "trip/package/holiday planning, incomplete travel requests, broad "
            "destinations, and active-trip follow-up details. Use "
            "handle_visible_results for questions or actions about currently "
            "visible flight, hotel, or package results. Use search_flights "
            "only for explicit flight-only searches. Use search_hotels only for "
            "explicit hotel-only searches. Use confirm_booking when a pendingAction "
            "exists and the user confirms."
        )
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Tool arguments. For prepare_trip_request, include intent, "
            "use_active_trip_state, origin_text/origin_code, destination_text, "
            "destination_type, destination_code, destination_candidates, month, "
            "departure_day, return_day, departure_date, return_date, nights, "
            "budget_amount, budget_currency, activityPreferences, "
            "requiredActivities, environmentPreferences, and missing_fields where relevant. "
            "Month-only requests must keep departure_date and return_date null. "
            "Day-only follow-ups inside active_trip.month should use departure_day "
            "and month rather than inventing a current-month date. For "
            "handle_visible_results, include action, result_type, option_number, "
            "use_visible_results, use_current_selection, and confidence."
        ),
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Optional router confidence from 0 to 1.",
    )
    explanation: str | None = Field(
        default=None,
        description="Optional short explanation of why this tool was selected.",
    )


class LlamaPlannerToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: PlannerToolName = Field(
        description=(
            "Planner-facing backend tool. Use prepare_trip_request for new or "
            "in-progress travel planning, including flight-only, hotel-only, "
            "package, holiday, broad destination, date, duration, budget, and "
            "passenger updates. Use handle_visible_results for follow-up "
            "questions or actions about visible flight, hotel, or package result "
            "cards. Direct search_flights/search_hotels are not "
            "available to the Llama router; the backend executes those only "
            "after validating prepare_trip_request state."
        )
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "For prepare_trip_request include requested_product "
            "(flight|hotel|package|unknown), intent, use_active_trip_state, "
            "origin_text/origin_code, destination_text/destination_code/"
            "destination_region/destination_candidates, month, departure_date, "
            "return_date, departure_day, return_day, nights, budget_amount, "
            "budget_currency, passenger_count, activityPreferences, "
            "requiredActivities, environmentPreferences, and missing_fields. For "
            "handle_visible_results include action "
            "(select_cheapest|select_option|compare_results|show_option|"
            "add_selected_to_cart|add_option_to_cart|ask_result_question), "
            "result_type (package|flight|hotel|unknown), option_number, "
            "use_visible_results, and use_current_selection."
        ),
    )
    confidence: float | None = Field(default=None, ge=0, le=1)
    explanation: str | None = None


class UserCreate(BaseModel):
    """Local account registration payload for Voyage AI."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 UTF-8 bytes or fewer.")
        return value


class UserLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 UTF-8 bytes or fewer.")
        return value


class UserSummary(BaseModel):
    id: int
    email: str
    full_name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserSummary


class UserProfileResponse(UserSummary):
    created_at: datetime
    preferences: dict[str, Any] | None = None


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(default=None, max_length=120)


class PreferenceUpdate(BaseModel):
    """Stored user preferences; ranking use is a later project stage."""

    model_config = ConfigDict(extra="forbid")

    preferred_cabin_class: CabinClass | None = None
    max_stops: int | None = Field(default=None, ge=0)
    preferred_departure_time: str | None = Field(default=None, max_length=40)
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    preferred_hotel_rating: int | None = Field(default=None, ge=1, le=5)
    hotel_location_preference: str | None = Field(default=None, max_length=120)
    accessibility_needs: Any | None = None
    sustainability_preference: str | None = Field(default=None, max_length=80)


# Future schemas can describe structured flights, hotels, users, bookings,
# Expedia responses, voice transcripts, and Llama-generated travel plans.
