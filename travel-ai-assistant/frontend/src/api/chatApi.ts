import type { NormalisedFlightResult } from "./flightApi";
import { clearAuthSession, getAuthHeaders } from "./authApi";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const CHAT_SESSION_STORAGE_KEY = "voyage_session_id";

function getStoredChatSessionId(): number | null {
  const value = localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
  const parsed = value ? Number(value) : NaN;
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function saveChatSessionId(sessionId: number | null | undefined) {
  if (typeof sessionId === "number" && sessionId > 0) {
    localStorage.setItem(CHAT_SESSION_STORAGE_KEY, String(sessionId));
  }
}

export type TravelIntent = {
  intent_type:
    | "flight_search"
    | "hotel_search"
    | "package_search"
    | "package_option_view"
    | "package_option_selection"
    | "booking_retrieval"
    | "booking_confirmation"
    | "booking_cancellation"
    | "general_enquiry";
  destination: string | null;
  booking_reference: string | null;
  departure_city: string | null;
  travelers: number;
  budget_level: string | null;
  message_summary: string;
};

export type FlightResult = NormalisedFlightResult;

export type NormalisedHotelResult = {
  id: string;
  option_number: number;
  hotel_name: string;
  location: string;
  check_in_date: string;
  check_out_date: string;
  nights: number;
  guest_count: number;
  room_count: number;
  rating: number | null;
  board_type: string | null;
  room_name: string | null;
  refundable: boolean | null;
  price: number;
  currency: string;
  provider: string;
  short_id: string | null;
  description: string | null;
  amenities: string[];
  latitude: number | null;
  longitude: number | null;
};

export type HotelResult = NormalisedHotelResult;

export type PackageResult = {
  id?: string;
  packageId?: string;
  option_number?: number;
  optionNumber?: number;
  destination?: string;
  destinationCity?: string;
  destinationAirportCode?: string;
  flight_id?: string;
  flight_airline?: string;
  hotel_id?: string;
  hotel_name?: string;
  summary?: string;
  estimated_price?: number;
  totalPrice?: number;
  currency: string;
  nights?: number;
  passengers?: number;
  rooms?: number;
  flight?: {
    offerId: string;
    airline: string | null;
    origin: string;
    destination: string;
    departureDate: string;
    returnDate: string | null;
    departureTime: string | null;
    returnTime: string | null;
    cabin: string;
    stops: number | null;
    duration: string | null;
    price: number;
    currency: string;
    provider: string;
  };
  hotel?: {
    hotelId: string | null;
    hotelName: string;
    city: string;
    checkInDate: string;
    checkOutDate: string;
    nights: number;
    rating: number | null;
    address: string | null;
    boardType: string | null;
    roomName: string | null;
    price: number;
    currency: string;
    provider: string;
  };
  reasons?: string[];
  warnings?: string[];
  providerSummary?: string;
};

export type PackageCartSummary = {
  cartId: string;
  items: Array<{
    cartItemId: string;
    itemType: "flight" | "hotel" | "package";
    type?: "flight" | "hotel" | "package";
    title?: string;
    subtitle?: string;
    provider?: string;
    price?: number;
    currency?: string;
    packageOption?: PackageResult;
    hotelOption?: NormalisedHotelResult;
    flightOption?: FlightResult;
    itemData?: Record<string, unknown>;
    quantity: number;
    addedAt: string;
    status: string;
  }>;
  totalPrice: number;
  currency: string;
  status: string;
};

export type FlightTicket = {
  ticketNumber: string;
  passengerName: string | null;
  airline: string | null;
  origin: string;
  destination: string;
  departureDate: string;
  returnDate: string | null;
  departureTime: string | null;
  returnTime: string | null;
  cabin: string;
  bookingReference: string;
  status: string;
};

export type HotelVoucher = {
  voucherNumber: string;
  hotelName: string;
  city: string;
  checkInDate: string;
  checkOutDate: string;
  nights: number;
  roomName: string | null;
  boardType: string | null;
  guests: number;
  bookingReference: string;
  status: string;
};

export type DestinationRecommendation = {
  city: string;
  country: string;
  airportCode: string;
  airportName: string | null;
  score: number;
  matchReasons: string[];
  warnings: string[];
  evidenceSummary: string;
  estimatedFlightMinutes: number | null;
};

export type BookingResult = {
  id?: number;
  reference: string;
  status: string;
  destination: string;
  summary: string;
  flight_id?: string | null;
  hotel_id?: string | null;
  estimated_price?: number | null;
  currency: string;
  created_at?: string;
};

export type TravelResults = {
  flights: FlightResult[];
  hotels: HotelResult[];
  destinations: DestinationRecommendation[];
  packages: PackageResult[];
  cart: PackageCartSummary | null;
  checkout: Record<string, unknown> | null;
  flightTicket: FlightTicket | null;
  hotelVoucher: HotelVoucher | null;
  booking: BookingResult | null;
  suggestions: string[];
};

export type ChatResponse = {
  reply: string;
  intent: TravelIntent;
  results: TravelResults;
  requires_confirmation: boolean;
  pending_action: string | null;
  sessionId: number | null;
  nextAction?: string | null;
  nextPath?: string | null;
  redirect_to?: string | null;
};

async function postChat(
  message: string,
  includeAuth: boolean,
): Promise<Response> {
  const sessionId = getStoredChatSessionId();
  return fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(includeAuth ? getAuthHeaders() : {}),
    },
    body: JSON.stringify({ message, sessionId }),
  });
}

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  let response = await postChat(message, true);

  if (response.status === 401) {
    clearAuthSession();
    response = await postChat(message, false);
  }

  if (!response.ok) {
    throw new Error("The Voyage AI assistant API did not respond successfully.");
  }

  const data = (await response.json()) as ChatResponse;
  saveChatSessionId(data.sessionId);
  return data;
}

export function getChatSessionId(): number | null {
  return getStoredChatSessionId();
}

export function clearChatSessionId(): void {
  localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
}
