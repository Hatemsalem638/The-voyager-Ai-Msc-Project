import { type FormEvent, useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import { HotelCard } from "../components/HotelCard";
import {
  type HotelSearchResponse,
  searchHotels,
} from "../api/hotelApi";
import { DestinationAutocomplete } from "../components/search/DestinationAutocomplete";
import { PropertyTypeFilter } from "../components/search/PropertyTypeFilter";
import { useCart } from "../context/CartContext";
import type { HotelLocationSuggestion } from "../api/suggestionApi";
import {
  type PropertyType,
  inferPropertyType,
} from "../data/travelSuggestions";

type HotelsPageState = {
  destination?: HotelLocationSuggestion | null;
  propertyType?: PropertyType;
};

function destinationFromQuery(searchParams: URLSearchParams): HotelLocationSuggestion | null {
  const city = searchParams.get("city");
  const country = searchParams.get("country");
  const displayName = searchParams.get("destination");
  if (!city || !country) {
    return null;
  }
  const latitude = Number(searchParams.get("latitude"));
  const longitude = Number(searchParams.get("longitude"));
  return {
    city,
    country,
    displayName: displayName ?? `${city}, ${country}`,
    providerLocationId: searchParams.get("providerLocationId"),
    latitude: Number.isFinite(latitude) ? latitude : null,
    longitude: Number.isFinite(longitude) ? longitude : null,
    provider: "Voyage AI",
    source: "provider_cache",
  };
}

function propertyTypeFromQuery(value: string | null): PropertyType {
  const allowed: PropertyType[] = [
    "any",
    "hotel",
    "apartment",
    "resort",
    "villa",
    "cabin",
    "cottage",
    "aparthotel",
    "hostel",
  ];
  const normalised = (value ?? "any").toLowerCase() as PropertyType;
  return allowed.includes(normalised) ? normalised : "any";
}

function optionalNumber(value: string): number | undefined {
  const parsed = Number(value);
  return value.trim() && Number.isFinite(parsed) ? parsed : undefined;
}

export function HotelsPage() {
  const { addHotel } = useCart();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const routeState = location.state as HotelsPageState | null;
  const [destination, setDestination] = useState<HotelLocationSuggestion | null>(
    routeState?.destination ?? destinationFromQuery(searchParams),
  );
  const [checkInDate, setCheckInDate] = useState(searchParams.get("checkIn") ?? "2026-09-12");
  const [checkOutDate, setCheckOutDate] = useState(searchParams.get("checkOut") ?? "2026-09-16");
  const [guestCount, setGuestCount] = useState(Number(searchParams.get("guests") ?? 2));
  const [roomCount, setRoomCount] = useState(Number(searchParams.get("rooms") ?? 1));
  const [propertyType, setPropertyType] = useState<PropertyType>(
    routeState?.propertyType ?? propertyTypeFromQuery(searchParams.get("propertyType")),
  );
  const [minRating, setMinRating] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [currency, setCurrency] = useState("GBP");
  const [response, setResponse] = useState<HotelSearchResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sortBy, setSortBy] = useState<"cheapest" | "rated" | "best">("best");
  const [hotelNameFilter, setHotelNameFilter] = useState("");
  const [refundableOnly, setRefundableOnly] = useState(false);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const autoSearched = useRef(false);

  const displayedHotels = [...(response?.results ?? [])]
    .filter((hotel) => {
      const name = hotel.hotel_name.toLowerCase();
      const typeText = `${hotel.hotel_name} ${hotel.room_name ?? ""} ${hotel.description ?? ""}`;
      const inferredType = inferPropertyType(typeText);
      return (
        (!hotelNameFilter.trim() || name.includes(hotelNameFilter.trim().toLowerCase())) &&
        (!refundableOnly || hotel.refundable === true) &&
        (propertyType === "any" || inferredType === propertyType)
      );
    })
    .sort((a, b) => {
      if (sortBy === "cheapest") {
        return a.price - b.price;
      }
      if (sortBy === "rated") {
        return (b.rating ?? 0) - (a.rating ?? 0);
      }
      return (a.price / Math.max(a.rating ?? 1, 1)) - (b.price / Math.max(b.rating ?? 1, 1));
    });

  async function runSearch(selectedDestination: HotelLocationSuggestion) {
    setIsLoading(true);
    setError("");
    setNotice("");
    setResponse(null);

    try {
      const result = await searchHotels({
        searchType: "hotel",
        destination: {
          city: selectedDestination.city,
          country: selectedDestination.country,
          latitude: selectedDestination.latitude ?? undefined,
          longitude: selectedDestination.longitude ?? undefined,
        },
        checkInDate,
        checkOutDate,
        guestCount,
        roomCount,
        preferences: {
          minRating: optionalNumber(minRating),
          maxPrice: optionalNumber(maxPrice),
          currency: currency.trim().toUpperCase(),
        },
      });
      setResponse(result);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Could not search hotels. Check that FastAPI is running.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  function validateForm(): boolean {
    const nextErrors: Record<string, string> = {};
    if (!destination) {
      nextErrors.destination = "Choose a destination.";
    }
    if (!checkInDate) {
      nextErrors.checkInDate = "Choose a check-in date.";
    }
    if (!checkOutDate) {
      nextErrors.checkOutDate = "Choose a check-out date.";
    }
    if (checkInDate && checkOutDate && checkOutDate <= checkInDate) {
      nextErrors.checkOutDate = "Check-out must be after check-in.";
    }
    if (guestCount < 1) {
      nextErrors.guestCount = "Guests must be at least 1.";
    }
    if (roomCount < 1) {
      nextErrors.roomCount = "Rooms must be at least 1.";
    }
    setFormErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || !destination) {
      return false;
    }
    return true;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (validateForm() && destination) {
      await runSearch(destination);
    }
  }

  useEffect(() => {
    if (
      !autoSearched.current &&
      destination &&
      searchParams.get("checkIn") &&
      searchParams.get("checkOut")
    ) {
      autoSearched.current = true;
      void runSearch(destination);
    }
  });

  return (
    <section className="page-container">
      <div className="page-heading">
        <p className="eyebrow">Stays</p>
        <h1>Hotels</h1>
        <p>Search hotel stays, compare ratings and prices, and add the right stay to your cart.</p>
      </div>

      <form className="travel-search-form elevated-search-form" onSubmit={handleSubmit}>
        <DestinationAutocomplete
          error={formErrors.destination}
          onChange={setDestination}
          value={destination}
        />
        <label>
          <span>Check-in date</span>
          <input
            onChange={(event) => setCheckInDate(event.target.value)}
            required
            type="date"
            value={checkInDate}
          />
          {formErrors.checkInDate && <small className="field-error">{formErrors.checkInDate}</small>}
        </label>
        <label>
          <span>Check-out date</span>
          <input
            onChange={(event) => setCheckOutDate(event.target.value)}
            required
            type="date"
            value={checkOutDate}
          />
          {formErrors.checkOutDate && <small className="field-error">{formErrors.checkOutDate}</small>}
        </label>
        <label>
          <span>Guests</span>
          <input
            max={9}
            min={1}
            onChange={(event) => setGuestCount(Number(event.target.value))}
            required
            type="number"
            value={guestCount}
          />
          {formErrors.guestCount && <small className="field-error">{formErrors.guestCount}</small>}
        </label>
        <label>
          <span>Rooms</span>
          <input
            max={5}
            min={1}
            onChange={(event) => setRoomCount(Number(event.target.value))}
            required
            type="number"
            value={roomCount}
          />
          {formErrors.roomCount && <small className="field-error">{formErrors.roomCount}</small>}
        </label>
        <PropertyTypeFilter onChange={setPropertyType} value={propertyType} />
        <label>
          <span>Minimum rating</span>
          <input
            max={10}
            min={0}
            onChange={(event) => setMinRating(event.target.value)}
            placeholder="Optional"
            step="0.5"
            type="number"
            value={minRating}
          />
        </label>
        <label>
          <span>Maximum price</span>
          <input
            min={0}
            onChange={(event) => setMaxPrice(event.target.value)}
            placeholder="Optional"
            type="number"
            value={maxPrice}
          />
        </label>
        <label>
          <span>Currency</span>
          <select onChange={(event) => setCurrency(event.target.value)} value={currency}>
            <option value="GBP">GBP</option>
            <option value="EUR">EUR</option>
            <option value="USD">USD</option>
          </select>
        </label>
        <button className="button button-primary search-submit" disabled={isLoading} type="submit">
          {isLoading ? "Searching..." : "Search hotels"}
        </button>
      </form>

      {error && <p className="form-message form-message-error">{error}</p>}
      {notice && <p className="form-message form-message-success">{notice}</p>}
      {response?.message && <p className="provider-note">{response.message}</p>}
      {propertyType !== "any" && response && (
        <p className="provider-note">
          Property type filtering is applied where provider data is available.
        </p>
      )}

      {response && (
        <section className="search-results-section">
          <div className="results-heading">
            <h2>Hotel options</h2>
            <span className="provider-badge">
              {response.provider_used === "liteapi" ? "LiteAPI" : "Unavailable"}
            </span>
          </div>
          {response.results.length === 0 && (
            <p className="provider-note">No provider-backed hotel options are available for this search.</p>
          )}
          <div className="hotel-results-layout">
            <aside className="map-placeholder">
              <strong>Map preview</strong>
              <span>Coming soon</span>
            </aside>
            <div>
              <div className="result-controls">
                <label>
                  <span>Sort</span>
                  <select onChange={(event) => setSortBy(event.target.value as typeof sortBy)} value={sortBy}>
                    <option value="best">Best value</option>
                    <option value="cheapest">Cheapest</option>
                    <option value="rated">Highest rated</option>
                  </select>
                </label>
                <label>
                  <span>Hotel name</span>
                  <input onChange={(event) => setHotelNameFilter(event.target.value)} placeholder="Search results" value={hotelNameFilter} />
                </label>
                <label className="checkbox-control">
                  <input checked={refundableOnly} onChange={(event) => setRefundableOnly(event.target.checked)} type="checkbox" />
                  <span>Refundable only</span>
                </label>
              </div>
          {displayedHotels.length === 0 ? (
            <p>No hotel options were returned for this search.</p>
          ) : (
            <div className="manual-result-grid">
              {displayedHotels.map((hotel) => (
                <HotelCard
                  hotel={hotel}
                  key={hotel.id}
                  onAddToCart={(selectedHotel) => {
                    addHotel(selectedHotel);
                    setNotice("Hotel added to your trip cart.");
                  }}
                />
              ))}
            </div>
          )}
            </div>
          </div>
        </section>
      )}
    </section>
  );
}
