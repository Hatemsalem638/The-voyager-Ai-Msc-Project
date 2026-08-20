import { getAuthHeaders } from "./authApi";
import type { NormalisedHotelResult } from "./chatApi";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type { NormalisedHotelResult };

export type HotelSearchPayload = {
  searchType: "hotel";
  destination: {
    city?: string;
    country?: string;
    latitude?: number;
    longitude?: number;
  };
  checkInDate: string;
  checkOutDate: string;
  guestCount: number;
  roomCount: number;
  preferences: {
    minRating?: number;
    maxPrice?: number;
    currency: string;
    boardType?: string;
    amenities?: string[];
  };
};

export type HotelSearchResponse = {
  provider_used: "liteapi" | "mock_hotel_fallback";
  results: NormalisedHotelResult[];
  message: string | null;
};

export async function searchHotels(
  payload: HotelSearchPayload,
): Promise<HotelSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/hotels/search`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error("The hotel search API did not respond successfully.");
  }

  return response.json();
}
