import { type FormEvent, useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import {
  type CabinClass,
  type FlightSearchResponse,
  searchFlights,
} from "../api/flightApi";
import { FlightCard } from "../components/FlightCard";
import { AirportAutocomplete } from "../components/search/AirportAutocomplete";
import { useCart } from "../context/CartContext";
import type { AirportSuggestion } from "../api/suggestionApi";

type FlightPageState = {
  origin?: AirportSuggestion | null;
  destination?: AirportSuggestion | null;
};

function airportFromQuery(
  code: string | null,
  label: string | null,
  city: string | null,
): AirportSuggestion | null {
  if (!code || code.length !== 3) {
    return null;
  }
  const upperCode = code.toUpperCase();
  return {
    code: upperCode,
    name: label ?? `${city ?? upperCode} Airport`,
    city: city ?? label ?? upperCode,
    country: "",
    displayName: label ?? `${city ?? upperCode} (${upperCode})`,
    provider: "Voyage AI",
    source: "provider_cache",
  };
}

export function FlightsPage() {
  const { addFlight } = useCart();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const routeState = location.state as FlightPageState | null;
  const initialOrigin = routeState?.origin ?? airportFromQuery(
    searchParams.get("from"),
    searchParams.get("fromLabel"),
    searchParams.get("fromCity"),
  );
  const initialDestination = routeState?.destination ?? airportFromQuery(
    searchParams.get("to"),
    searchParams.get("toLabel"),
    searchParams.get("toCity"),
  );
  const [origin, setOrigin] = useState<AirportSuggestion | null>(
    initialOrigin,
  );
  const [destination, setDestination] = useState<AirportSuggestion | null>(
    initialDestination,
  );
  const [departureDate, setDepartureDate] = useState(searchParams.get("departureDate") ?? "2026-09-12");
  const [returnDate, setReturnDate] = useState(searchParams.get("returnDate") ?? "2026-09-19");
  const [tripType, setTripType] = useState<"one_way" | "round_trip">(
    "round_trip",
  );
  const [passengerCount, setPassengerCount] = useState(Number(searchParams.get("passengers") ?? 1));
  const [cabinClass, setCabinClass] = useState<CabinClass>((searchParams.get("cabin") as CabinClass) ?? "economy");
  const [maxStops, setMaxStops] = useState("any");
  const [response, setResponse] = useState<FlightSearchResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sortBy, setSortBy] = useState<"cheapest" | "fastest" | "best">("cheapest");
  const [priceLimit, setPriceLimit] = useState("");
  const [airlineFilter, setAirlineFilter] = useState("");
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const autoSearched = useRef(false);

  const displayedFlights = [...(response?.flights ?? [])]
    .filter((flight) => {
      const price = Number(flight.price ?? 0);
      const maxPrice = Number(priceLimit);
      const airline = (flight.airline ?? "").toLowerCase();
      return (
        (!priceLimit || (Number.isFinite(maxPrice) && price <= maxPrice)) &&
        (!airlineFilter.trim() || airline.includes(airlineFilter.trim().toLowerCase()))
      );
    })
    .sort((a, b) => {
      if (sortBy === "fastest") {
        return (a.duration_minutes ?? Number.MAX_SAFE_INTEGER) - (b.duration_minutes ?? Number.MAX_SAFE_INTEGER);
      }
      if (sortBy === "best") {
        return (
          Number(a.price ?? Number.MAX_SAFE_INTEGER) +
          (a.stops ?? 0) * 80 +
          (a.duration_minutes ?? 0) / 20
        ) - (
          Number(b.price ?? Number.MAX_SAFE_INTEGER) +
          (b.stops ?? 0) * 80 +
          (b.duration_minutes ?? 0) / 20
        );
      }
      return Number(a.price ?? Number.MAX_SAFE_INTEGER) - Number(b.price ?? Number.MAX_SAFE_INTEGER);
    });

  async function runSearch(
    selectedOrigin: AirportSuggestion,
    selectedDestination: AirportSuggestion,
    selectedDepartureDate: string,
    selectedReturnDate: string,
  ) {
    setIsLoading(true);
    setError("");
    setNotice("");
    setResponse(null);

    try {
      const dates =
        tripType === "round_trip"
          ? { departure: selectedDepartureDate, return: selectedReturnDate }
          : { departure: selectedDepartureDate };
      const result = await searchFlights({
        searchType: "flight",
        passengerCount,
        itinerary: {
          tripType,
          origin: {
            code: selectedOrigin.code,
            city: selectedOrigin.city,
          },
          destination: {
            code: selectedDestination.code,
            city: selectedDestination.city,
          },
          dates,
        },
        preferences: {
          cabinClass,
          maxStops: maxStops === "non_stop" ? 0 : null,
        },
      });
      setResponse(result);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Could not search flights. Check that FastAPI is running.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  function validateForm(): boolean {
    const nextErrors: Record<string, string> = {};
    if (!origin) {
      nextErrors.origin = "Choose your departure city or airport.";
    }
    if (!destination) {
      nextErrors.destination = "Choose your destination city or airport.";
    }
    if (!departureDate) {
      nextErrors.departureDate = "Choose a departure date.";
    }
    if (tripType === "round_trip" && !returnDate) {
      nextErrors.returnDate = "Choose a return date.";
    }
    if (origin && destination && origin.code === destination.code) {
      nextErrors.destination = "Choose a different destination airport.";
    }
    setFormErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || !origin || !destination) {
      return false;
    }
    return true;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (validateForm() && origin && destination) {
      await runSearch(origin, destination, departureDate, returnDate);
    }
  }

  useEffect(() => {
    if (
      !autoSearched.current &&
      origin &&
      destination &&
      searchParams.get("departureDate")
    ) {
      autoSearched.current = true;
      void runSearch(origin, destination, departureDate, returnDate);
    }
  });

  return (
    <section className="page-container">
      <div className="page-heading">
        <p className="eyebrow">Flights</p>
        <h1>Flights</h1>
        <p>Search routes, compare prices, filter results, and add the best option to your trip cart.</p>
      </div>

      <form className="travel-search-form elevated-search-form" onSubmit={handleSubmit}>
        <AirportAutocomplete
          error={formErrors.origin}
          label="From"
          onChange={setOrigin}
          placeholder="City or airport, e.g. East Midlands"
          value={origin}
        />
        <AirportAutocomplete
          error={formErrors.destination}
          label="To"
          onChange={setDestination}
          placeholder="City or airport, e.g. Paris"
          value={destination}
        />
        <button
          aria-label="Swap departure and destination airports"
          className="swap-route-button"
          onClick={() => {
            setOrigin(destination);
            setDestination(origin);
          }}
          type="button"
        >
          Swap
        </button>
        <label>
          <span>Trip type</span>
          <select
            onChange={(event) =>
              setTripType(event.target.value as "one_way" | "round_trip")
            }
            value={tripType}
          >
            <option value="round_trip">Round trip</option>
            <option value="one_way">One way</option>
          </select>
        </label>
        <label>
          <span>Departure date</span>
          <input
            onChange={(event) => setDepartureDate(event.target.value)}
            required
            type="date"
            value={departureDate}
          />
          {formErrors.departureDate && <small className="field-error">{formErrors.departureDate}</small>}
        </label>
        {tripType === "round_trip" && (
          <label>
            <span>Return date</span>
            <input
              onChange={(event) => setReturnDate(event.target.value)}
              required
              type="date"
              value={returnDate}
            />
            {formErrors.returnDate && <small className="field-error">{formErrors.returnDate}</small>}
          </label>
        )}
        <label>
          <span>Passengers</span>
          <input
            max={9}
            min={1}
            onChange={(event) => setPassengerCount(Number(event.target.value))}
            required
            type="number"
            value={passengerCount}
          />
        </label>
        <label>
          <span>Cabin class</span>
          <select
            onChange={(event) => setCabinClass(event.target.value as CabinClass)}
            value={cabinClass}
          >
            <option value="economy">Economy</option>
            <option value="premium_economy">Premium economy</option>
            <option value="business">Business</option>
            <option value="first">First</option>
          </select>
        </label>
        <label>
          <span>Stops</span>
          <select onChange={(event) => setMaxStops(event.target.value)} value={maxStops}>
            <option value="any">Any number of stops</option>
            <option value="non_stop">Non-stop only</option>
          </select>
        </label>
        <button className="button button-primary search-submit" disabled={isLoading} type="submit">
          {isLoading ? "Searching..." : "Search flights"}
        </button>
      </form>

      {error && <p className="form-message form-message-error">{error}</p>}
      {notice && <p className="form-message form-message-success">{notice}</p>}
      {response?.fallback_reason && (
        <p className="provider-note">{response.fallback_reason}</p>
      )}

      {response && (
        <section className="search-results-section">
          <div className="results-heading">
            <h2>Flight options</h2>
            <span className="provider-badge">
              {response.provider_used === "duffel" ? "Duffel" : "Unavailable"}
            </span>
          </div>
          {response.flights.length === 0 && (
            <p className="provider-note">No provider-backed flight options are available for this search.</p>
          )}
          <div className="result-controls">
            <label>
              <span>Sort</span>
              <select onChange={(event) => setSortBy(event.target.value as typeof sortBy)} value={sortBy}>
                <option value="cheapest">Cheapest</option>
                <option value="fastest">Fastest</option>
                <option value="best">Best value</option>
              </select>
            </label>
            <label>
              <span>Max price</span>
              <input onChange={(event) => setPriceLimit(event.target.value)} placeholder="Any" type="number" value={priceLimit} />
            </label>
            <label>
              <span>Airline</span>
              <input onChange={(event) => setAirlineFilter(event.target.value)} placeholder="Any airline" value={airlineFilter} />
            </label>
          </div>
          {isLoading && <div className="loading-skeleton">Searching current flight options...</div>}
          {displayedFlights.length === 0 ? (
            <p>No flight options were returned for this search.</p>
          ) : (
            <div className="manual-result-grid">
              {displayedFlights.map((flight) => (
                <FlightCard
                  flight={flight}
                  key={flight.id}
                  onAddToCart={(selectedFlight) => {
                    addFlight(selectedFlight);
                    setNotice("Flight added to your trip cart.");
                  }}
                />
              ))}
            </div>
          )}
        </section>
      )}
    </section>
  );
}
