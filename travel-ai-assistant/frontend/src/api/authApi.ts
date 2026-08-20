import type {
  PackageResult,
  FlightTicket,
  HotelVoucher,
} from "./chatApi";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

const AUTH_SESSION_STORAGE_KEY = "travel-assistant-auth-session";

export type UserSummary = {
  id: number;
  email: string;
  full_name: string | null;
};

export type UserPreferences = {
  preferred_cabin_class: "economy" | "premium_economy" | "business" | "first" | null;
  max_stops: number | null;
  preferred_departure_time: string | null;
  budget_min: number | null;
  budget_max: number | null;
  preferred_hotel_rating: number | null;
  hotel_location_preference: string | null;
  accessibility_needs: unknown;
  sustainability_preference: string | null;
};

export type UserProfile = UserSummary & {
  created_at: string;
  preferences: UserPreferences | null;
};

export type BookingDetails = {
  id: number;
  reference: string;
  status: string;
  destination: string;
  summary: string;
  flight_id: string | null;
  hotel_id: string | null;
  estimated_price: number | null;
  currency: string;
  booking_type: string;
  created_at: string;
  totalPrice: number | null;
  packageOption: PackageResult | null;
  packageSummary: Record<string, unknown> | null;
  flightTicket: FlightTicket | null;
  hotelVoucher: HotelVoucher | null;
  checkoutDetails: Record<string, unknown> | null;
  detailsAvailable: boolean;
};

export type BookingListResponse = {
  bookings: BookingDetails[];
};

export type AuthSession = {
  access_token: string;
  token_type: "bearer";
  user: UserSummary;
};

export type RegistrationPayload = {
  email: string;
  password: string;
  full_name?: string;
};

export type LoginPayload = {
  email: string;
  password: string;
};

function readError(response: Response): Promise<string> {
  return response
    .json()
    .then((body: { detail?: string }) => body.detail ?? "Request failed.")
    .catch(() => "Request failed.");
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...options.headers,
    },
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  return response.json() as Promise<T>;
}

export function getAuthSession(): AuthSession | null {
  try {
    const value = localStorage.getItem(AUTH_SESSION_STORAGE_KEY);
    return value ? (JSON.parse(value) as AuthSession) : null;
  } catch {
    return null;
  }
}

export function saveAuthSession(session: AuthSession): void {
  localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(session));
}

export function clearAuthSession(): void {
  localStorage.removeItem(AUTH_SESSION_STORAGE_KEY);
}

export function getAuthHeaders(): Record<string, string> {
  const session = getAuthSession();
  return session ? { Authorization: `Bearer ${session.access_token}` } : {};
}

export async function registerUser(
  payload: RegistrationPayload,
): Promise<AuthSession> {
  const session = await apiRequest<AuthSession>("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  saveAuthSession(session);
  return session;
}

export async function loginUser(payload: LoginPayload): Promise<AuthSession> {
  const session = await apiRequest<AuthSession>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  saveAuthSession(session);
  return session;
}

export function getCurrentUser(): Promise<UserProfile> {
  return apiRequest<UserProfile>("/auth/me");
}

export function getBookings(): Promise<BookingListResponse> {
  return apiRequest<BookingListResponse>("/bookings");
}

export function getBooking(reference: string): Promise<BookingDetails> {
  return apiRequest<BookingDetails>(`/bookings/${encodeURIComponent(reference)}`);
}

export function updateCurrentUser(payload: {
  full_name: string | null;
}): Promise<UserProfile> {
  return apiRequest<UserProfile>("/auth/me", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function getPreferences(): Promise<UserPreferences> {
  return apiRequest<UserPreferences>("/auth/preferences");
}

export function updatePreferences(
  payload: Partial<UserPreferences>,
): Promise<UserPreferences> {
  return apiRequest<UserPreferences>("/auth/preferences", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
