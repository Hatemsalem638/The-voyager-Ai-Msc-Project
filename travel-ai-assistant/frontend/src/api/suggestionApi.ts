const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type AirportSuggestion = {
  code: string;
  name: string;
  city: string;
  country: string;
  displayName: string;
  provider: string;
  source: "provider_api" | "provider_cache";
};

export type HotelLocationSuggestion = {
  city: string;
  country: string;
  displayName: string;
  providerLocationId: string | null;
  latitude: number | null;
  longitude: number | null;
  provider: string;
  source: "provider_api" | "provider_cache";
};

async function readError(response: Response): Promise<string> {
  return response
    .json()
    .then((body: { detail?: string }) => body.detail ?? "Request failed.")
    .catch(() => "Request failed.");
}

export async function searchAirports(
  query: string,
  signal?: AbortSignal,
): Promise<AirportSuggestion[]> {
  const trimmed = query.trim();
  if (trimmed.length < 2) {
    return [];
  }
  const response = await fetch(
    `${API_BASE_URL}/airports/suggest?q=${encodeURIComponent(trimmed)}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}

export async function searchHotelLocations(
  query: string,
  signal?: AbortSignal,
): Promise<HotelLocationSuggestion[]> {
  const trimmed = query.trim();
  if (trimmed.length < 2) {
    return [];
  }
  const response = await fetch(
    `${API_BASE_URL}/locations/suggest?q=${encodeURIComponent(trimmed)}&type=hotel`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}
