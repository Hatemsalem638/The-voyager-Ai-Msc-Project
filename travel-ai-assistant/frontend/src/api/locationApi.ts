import { getAuthHeaders } from "./authApi";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type DefaultOrigin = {
  city: string;
  country: string | null;
  airportCode: string;
  airportName: string | null;
  source: string;
};

export type CurrentLocationResponse = {
  defaultOrigin: DefaultOrigin;
  message: string;
};

export async function saveCurrentLocation(
  latitude: number,
  longitude: number,
): Promise<CurrentLocationResponse> {
  const response = await fetch(`${API_BASE_URL}/location/current`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify({ latitude, longitude }),
  });

  if (!response.ok) {
    throw new Error("Could not save your location.");
  }

  return response.json();
}
