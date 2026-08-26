import { type FormEvent, useState } from "react";

import {
  type CabinClass,
  type FlightSearchResponse,
  searchFlights,
} from "../api/flightApi";
import { FlightCard } from "./FlightCard";

export function FlightSearch() {
  const [originCode, setOriginCode] = useState("LHR");
  const [destinationCode, setDestinationCode] = useState("JFK");
  const [departureDate, setDepartureDate] = useState("2026-09-12");
  const [returnDate, setReturnDate] = useState("2026-09-19");
  const [passengerCount, setPassengerCount] = useState(1);
  const [cabinClass, setCabinClass] = useState<CabinClass>("economy");
  const [response, setResponse] = useState<FlightSearchResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setIsLoading(true);
    setError("");
    setResponse(null);

    try {
      const searchResponse = await searchFlights({
        searchType: "flight",
        passengerCount,
        itinerary: {
          tripType: "round_trip",
          origin: { code: originCode.trim().toUpperCase() },
          destination: { code: destinationCode.trim().toUpperCase() },
          dates: {
            departure: departureDate,
            return: returnDate,
          },
        },
        preferences: {
          cabinClass,
          maxStops: 0,
        },
      });
      setResponse(searchResponse);
    } catch {
      setError("Could not search flights. Check that FastAPI is running.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="flight-search-shell" aria-label="Flight search">
      <div className="flight-search-header">
        <h2>Flight Search</h2>
        {response && (
          <span className={`provider-badge provider-${response.provider_used}`}>
            {response.provider_used === "duffel" ? "Duffel" : "Unavailable"}
          </span>
        )}
      </div>

      <form className="flight-search-form" onSubmit={handleSubmit}>
        <label>
          <span>Origin airport code</span>
          <input
            maxLength={3}
            minLength={3}
            onChange={(event) => setOriginCode(event.target.value)}
            required
            type="text"
            value={originCode}
          />
        </label>
        <label>
          <span>Destination airport code</span>
          <input
            maxLength={3}
            minLength={3}
            onChange={(event) => setDestinationCode(event.target.value)}
            required
            type="text"
            value={destinationCode}
          />
        </label>
        <label>
          <span>Departure date</span>
          <input
            onChange={(event) => setDepartureDate(event.target.value)}
            required
            type="date"
            value={departureDate}
          />
        </label>
        <label>
          <span>Return date</span>
          <input
            onChange={(event) => setReturnDate(event.target.value)}
            required
            type="date"
            value={returnDate}
          />
        </label>
        <label>
          <span>Passenger count</span>
          <input
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
        <button disabled={isLoading} type="submit">
          {isLoading ? "Searching..." : "Search"}
        </button>
      </form>

      {error && <p className="flight-search-error">{error}</p>}

      {response?.fallback_reason && (
        <p className="fallback-note">{response.fallback_reason}</p>
      )}

      {response && (
        <div className="flight-results">
          {response.flights.map((flight) => (
            <FlightCard flight={flight} key={flight.id} />
          ))}
          {response.flights.length === 0 && (
            <p className="fallback-note">No provider-backed flight options are available for this search.</p>
          )}
        </div>
      )}
    </section>
  );
}
