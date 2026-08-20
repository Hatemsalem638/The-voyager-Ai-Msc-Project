const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type HomepageSource = "provider_api" | "provider_cache";

export type HomepageFlightDeal = {
  id: string;
  originAirportCode: string;
  originCity: string;
  originDisplayName: string;
  destinationAirportCode: string;
  destinationCity: string;
  destinationDisplayName: string;
  departureDate: string;
  returnDate: string | null;
  airline: string | null;
  price: number | null;
  currency: string | null;
  duration: string | null;
  stops: string | null;
  provider: string;
  source: HomepageSource;
  searchUrl: string;
};

export type HomepagePackageDeal = {
  id: string;
  originAirportCode: string;
  originCity: string;
  destinationAirportCode: string;
  destinationCity: string;
  destinationCountry: string;
  departureDate: string;
  returnDate: string;
  nights: number;
  flightPrice: number;
  hotelPrice: number;
  totalPrice: number;
  currency: string;
  hotelName: string;
  hotelRating: number | null;
  providerSummary: string;
  source: HomepageSource;
  searchUrl: string;
};

export type HomepageDestination = {
  id: string;
  city: string;
  country: string;
  displayName: string;
  airportCode: string | null;
  providerLocationId: string | null;
  tags: string[];
  source: HomepageSource;
  searchUrl: string;
};

export type HomepageTripIdea = {
  id: string;
  theme: string;
  city: string;
  country: string;
  displayName: string;
  airportCode: string | null;
  estimatedFlightPrice: number | null;
  estimatedPackagePrice: number | null;
  tags: string[];
  source: HomepageSource;
  voyagerPrompt: string;
  searchUrl: string;
};

export type HomepagePropertyType = {
  id: string;
  label: string;
  description: string;
  searchUrl: string;
};

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error("Could not load homepage data.");
  }
  return response.json();
}

export function getRandomFlights(
  origin = "EMA",
  limit = 6,
  seed = "random",
): Promise<HomepageFlightDeal[]> {
  return apiGet(
    `/homepage/random-flights?origin=${encodeURIComponent(origin)}&limit=${limit}&seed=${encodeURIComponent(seed)}`,
  );
}

export function getRandomPackages(
  origin = "EMA",
  limit = 4,
  seed = "random",
): Promise<HomepagePackageDeal[]> {
  return apiGet(
    `/homepage/random-packages?origin=${encodeURIComponent(origin)}&limit=${limit}&seed=${encodeURIComponent(seed)}`,
  );
}

export function getTrendingDestinations(): Promise<HomepageDestination[]> {
  return apiGet("/homepage/trending-destinations");
}

export function getTripIdeas(theme: string): Promise<HomepageTripIdea[]> {
  return apiGet(`/homepage/trip-ideas?theme=${encodeURIComponent(theme)}`);
}

export function getPropertyTypes(): Promise<HomepagePropertyType[]> {
  return apiGet("/homepage/property-types");
}
