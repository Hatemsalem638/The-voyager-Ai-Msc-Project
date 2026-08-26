import { getAuthHeaders } from "./authApi";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type CabinClass =
  | "economy"
  | "premium_economy"
  | "business"
  | "first";

export type FlightSearchPayload = {
  searchType: "flight";
  passengerCount: number;
  itinerary: {
    tripType: "one_way" | "round_trip";
    origin: {
      city?: string;
      code: string;
    };
    destination: {
      city?: string;
      code: string;
    };
    dates: {
      departure: string;
      return?: string;
    };
  };
  preferences: {
    cabinClass: CabinClass;
    maxStops: number | null;
  };
};

export type NormalisedFlightResult = {
  id: string;
  offer_id: string | null;
  display_id: string | null;
  option_number: number | null;
  origin: string;
  destination: string;
  airline: string | null;
  outbound_departure_date: string | null;
  outbound_departure_time: string | null;
  outbound_arrival_date: string | null;
  outbound_arrival_time: string | null;
  return_departure_date: string | null;
  return_departure_time: string | null;
  return_arrival_date: string | null;
  return_arrival_time: string | null;
  departure_date: string | null;
  return_date: string | null;
  stops: number | null;
  stops_label: string | null;
  duration_minutes: number | null;
  duration_label: string | null;
  cabin_class: CabinClass;
  cabin_class_label: string | null;
  passenger_count: number;
  price: string | null;
  currency: string | null;
  provider: string;
};

export type FlightSearchResponse = {
  provider_used: "duffel" | "mock_fallback" | "unavailable";
  flights: NormalisedFlightResult[];
  fallback_reason: string | null;
};

export async function searchFlights(
  payload: FlightSearchPayload,
): Promise<FlightSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/flights/search`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error("The flight search API did not respond successfully.");
  }

  return response.json();
}
