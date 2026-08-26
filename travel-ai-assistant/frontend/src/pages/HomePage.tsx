import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { CabinClass } from "../api/flightApi";
import {
  type HomepageDestination,
  type HomepageFlightDeal,
  type HomepagePackageDeal,
  type HomepagePropertyType,
  type HomepageTripIdea,
  getPropertyTypes,
  getRandomFlights,
  getRandomPackages,
  getTrendingDestinations,
  getTripIdeas,
} from "../api/homepageApi";
import type {
  AirportSuggestion,
  HotelLocationSuggestion,
} from "../api/suggestionApi";
import { AirportAutocomplete } from "../components/search/AirportAutocomplete";
import { DestinationAutocomplete } from "../components/search/DestinationAutocomplete";
import { PropertyTypeFilter } from "../components/search/PropertyTypeFilter";
import type { PropertyType } from "../data/travelSuggestions";

type HomeSearchTab = "stays" | "flights" | "packages" | "voyager";
type TripTheme = "skiing" | "beach" | "city_break" | "family" | "food" | "culture" | "budget";

const tripThemes: { id: TripTheme; label: string }[] = [
  { id: "skiing", label: "Skiing" },
  { id: "beach", label: "Beach" },
  { id: "city_break", label: "City break" },
  { id: "family", label: "Family" },
  { id: "food", label: "Food" },
  { id: "culture", label: "Culture" },
  { id: "budget", label: "Budget" },
];

const whyVoyage = [
  {
    title: "AI trip planning",
    text: "Tell The Voyager what kind of trip you want and get destination ideas from your current context.",
  },
  {
    title: "Flights + hotels together",
    text: "Build package options with return flights, hotel stays, and total prices.",
  },
  {
    title: "Connected search",
    text: "Homepage cards open Flights, Stays, or The Voyager with filters already applied.",
  },
  {
    title: "Smart trip memory",
    text: "Voyage AI keeps your chosen destination, dates, package, and cart together during the session.",
  },
];

function formatMoney(value: number | null, currency: string | null): string {
  if (value === null || !currency) {
    return "Price unavailable";
  }
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

function formatDate(value: string | null): string {
  if (!value) {
    return "Date unavailable";
  }
  const parsed = new Date(`${value}T12:00:00`);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      }).format(parsed);
}

function HomeSection({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="home-section">
      <div className="home-section-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function LoadingCards({ count = 4 }: { count?: number }) {
  return (
    <div className="deal-card-grid">
      {Array.from({ length: count }, (_, index) => (
        <div className="home-data-card home-data-card-loading" key={index}>
          Loading...
        </div>
      ))}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="home-empty-state">{text}</p>;
}

export function HomePage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<HomeSearchTab>("stays");
  const [voyagerPrompt, setVoyagerPrompt] = useState("");
  const [homeOrigin, setHomeOrigin] = useState<AirportSuggestion | null>(null);
  const [homeFlightDestination, setHomeFlightDestination] =
    useState<AirportSuggestion | null>(null);
  const [homeStayDestination, setHomeStayDestination] =
    useState<HotelLocationSuggestion | null>(null);
  const [homePropertyType, setHomePropertyType] = useState<PropertyType>("any");
  const [homePassengers, setHomePassengers] = useState(1);
  const [homeCabin, setHomeCabin] = useState<CabinClass>("economy");
  const [theme, setTheme] = useState<TripTheme>("skiing");
  const [dealSeed, setDealSeed] = useState(() => String(Date.now()));
  const [flightDeals, setFlightDeals] = useState<HomepageFlightDeal[]>([]);
  const [packageDeals, setPackageDeals] = useState<HomepagePackageDeal[]>([]);
  const [propertyTypes, setPropertyTypes] = useState<HomepagePropertyType[]>([]);
  const [destinations, setDestinations] = useState<HomepageDestination[]>([]);
  const [tripIdeas, setTripIdeas] = useState<HomepageTripIdea[]>([]);
  const [loadingDeals, setLoadingDeals] = useState(true);
  const [loadingStatic, setLoadingStatic] = useState(true);
  const [loadingIdeas, setLoadingIdeas] = useState(true);
  const [homeError, setHomeError] = useState("");
  const hasVoyagerSession = useMemo(
    () =>
      typeof window !== "undefined" &&
      Boolean(window.localStorage.getItem("voyage_session_id")),
    [],
  );

  useEffect(() => {
    let isActive = true;
    setLoadingDeals(true);
    setHomeError("");
    Promise.all([
      getRandomFlights("EMA", 6, dealSeed),
      getRandomPackages("EMA", 4, dealSeed),
    ])
      .then(([flights, packages]) => {
        if (!isActive) {
          return;
        }
        setFlightDeals(flights);
        setPackageDeals(packages);
      })
      .catch(() => {
        if (isActive) {
          setHomeError("Could not load the latest homepage deals.");
        }
      })
      .finally(() => {
        if (isActive) {
          setLoadingDeals(false);
        }
      });
    return () => {
      isActive = false;
    };
  }, [dealSeed]);

  useEffect(() => {
    let isActive = true;
    setLoadingStatic(true);
    Promise.all([getPropertyTypes(), getTrendingDestinations()])
      .then(([types, trends]) => {
        if (!isActive) {
          return;
        }
        setPropertyTypes(types);
        setDestinations(trends);
      })
      .catch(() => {
        if (isActive) {
          setHomeError("Could not load homepage options.");
        }
      })
      .finally(() => {
        if (isActive) {
          setLoadingStatic(false);
        }
      });
    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    let isActive = true;
    setLoadingIdeas(true);
    getTripIdeas(theme)
      .then((ideas) => {
        if (isActive) {
          setTripIdeas(ideas);
        }
      })
      .catch(() => {
        if (isActive) {
          setTripIdeas([]);
        }
      })
      .finally(() => {
        if (isActive) {
          setLoadingIdeas(false);
        }
      });
    return () => {
      isActive = false;
    };
  }, [theme]);

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (tab === "flights") {
      const params = new URLSearchParams();
      if (homeOrigin) {
        params.set("from", homeOrigin.code);
        params.set("fromLabel", homeOrigin.displayName);
        params.set("fromCity", homeOrigin.city);
      }
      if (homeFlightDestination) {
        params.set("to", homeFlightDestination.code);
        params.set("toLabel", homeFlightDestination.displayName);
        params.set("toCity", homeFlightDestination.city);
      }
      params.set("passengers", String(homePassengers));
      params.set("cabin", homeCabin);
      navigate(`/flights?${params.toString()}`);
    } else if (tab === "stays") {
      const params = new URLSearchParams();
      if (homeStayDestination) {
        params.set("destination", homeStayDestination.displayName);
        params.set("city", homeStayDestination.city);
        params.set("country", homeStayDestination.country);
      }
      params.set("propertyType", homePropertyType);
      navigate(`/hotels?${params.toString()}`);
    } else if (tab === "packages") {
      const prompt = `Build me a flight and hotel package${
        homeStayDestination ? ` to ${homeStayDestination.displayName}` : ""
      }`;
      navigate(`/voyager?prompt=${encodeURIComponent(prompt)}`);
    } else {
      navigate(`/voyager?prompt=${encodeURIComponent(voyagerPrompt)}`);
    }
  }

  return (
    <>
      <section className="home-hero home-hero-data">
        <div className="home-hero-inner">
          <p className="hero-kicker">Voyage AI</p>
          <h1>Where do you want to go next?</h1>
          <p className="hero-copy">
            Search flights, compare stays, open dynamic package ideas, or ask
            The Voyager to plan from your own words.
          </p>

          <form className="home-search-panel" onSubmit={handleSearch}>
            <div className="home-search-tabs" role="tablist" aria-label="Travel search type">
              {(["stays", "flights", "packages", "voyager"] as HomeSearchTab[]).map((item) => (
                <button
                  aria-selected={tab === item}
                  className={tab === item ? "home-search-tab home-search-tab-active" : "home-search-tab"}
                  key={item}
                  onClick={() => setTab(item)}
                  role="tab"
                  type="button"
                >
                  {item === "stays"
                    ? "Stays"
                    : item === "voyager"
                      ? "Ask The Voyager"
                      : item[0].toUpperCase() + item.slice(1)}
                </button>
              ))}
            </div>

            {tab === "voyager" ? (
              <div className="home-search-grid home-search-grid-voyager">
                <label>
                  <span>Trip idea</span>
                  <input
                    onChange={(event) => setVoyagerPrompt(event.target.value)}
                    placeholder="Tell me what kind of trip you want..."
                    value={voyagerPrompt}
                  />
                </label>
                <button className="button button-primary" type="submit">
                  Ask The Voyager
                </button>
              </div>
            ) : (
              <div className={`home-search-grid home-search-grid-${tab}`}>
                {tab === "flights" && (
                  <>
                    <AirportAutocomplete label="From" onChange={setHomeOrigin} placeholder="City or airport" value={homeOrigin} />
                    <AirportAutocomplete label="To" onChange={setHomeFlightDestination} placeholder="City or airport" value={homeFlightDestination} />
                    <label><span>Passengers</span><input min={1} onChange={(event) => setHomePassengers(Number(event.target.value))} type="number" value={homePassengers} /></label>
                    <label><span>Cabin</span><select onChange={(event) => setHomeCabin(event.target.value as CabinClass)} value={homeCabin}><option value="economy">Economy</option><option value="premium_economy">Premium economy</option><option value="business">Business</option><option value="first">First</option></select></label>
                  </>
                )}
                {tab === "stays" && (
                  <>
                    <DestinationAutocomplete onChange={setHomeStayDestination} value={homeStayDestination} />
                    <PropertyTypeFilter onChange={setHomePropertyType} value={homePropertyType} />
                  </>
                )}
                {tab === "packages" && (
                  <>
                    <AirportAutocomplete label="From" onChange={setHomeOrigin} placeholder="City or airport" value={homeOrigin} />
                    <DestinationAutocomplete label="Destination" onChange={setHomeStayDestination} value={homeStayDestination} />
                    <PropertyTypeFilter onChange={setHomePropertyType} value={homePropertyType} />
                  </>
                )}
                <button className="button button-primary" type="submit">
                  {tab === "packages" ? "Build package" : tab === "flights" ? "Search flights" : "Find stays"}
                </button>
              </div>
            )}
          </form>
        </div>
      </section>

      <main className="home-content page-container">
        {homeError && <p className="form-message form-message-error">{homeError}</p>}

        <HomeSection
          title="Flight ideas for your next trip"
          subtitle="Fresh fares generated through the Voyage AI backend"
          action={
            <button className="button button-secondary" onClick={() => setDealSeed(String(Date.now()))} type="button">
              Refresh deals
            </button>
          }
        >
          {loadingDeals ? (
            <LoadingCards count={4} />
          ) : flightDeals.length === 0 ? (
            <EmptyState text="No flight ideas are available right now." />
          ) : (
            <div className="deal-card-grid">
              {flightDeals.map((deal) => (
                <Link className="home-data-card home-flight-deal-card" key={deal.id} to={deal.searchUrl}>
                  <span className="home-card-icon">Flight</span>
                  <h3>{deal.originCity} to {deal.destinationCity}</h3>
                  <p className="route-badge">{deal.originAirportCode} to {deal.destinationAirportCode}</p>
                  <p>{formatDate(deal.departureDate)} to {formatDate(deal.returnDate)}</p>
                  <strong>{formatMoney(deal.price, deal.currency)}</strong>
                  <small>{deal.airline ?? "Airline varies"} - {deal.stops ?? "Stops vary"} - Provider data</small>
                </Link>
              ))}
            </div>
          )}
        </HomeSection>

        <HomeSection
          title="Flight + hotel package ideas"
          subtitle="Dynamic packages with total prices"
        >
          {loadingDeals ? (
            <LoadingCards count={3} />
          ) : packageDeals.length === 0 ? (
            <EmptyState text="No package ideas are available right now." />
          ) : (
            <div className="deal-card-grid">
              {packageDeals.map((deal) => (
                <Link className="home-data-card home-package-deal-card" key={deal.id} to={deal.searchUrl}>
                  <span className="home-card-icon">Package</span>
                  <h3>{deal.nights} nights in {deal.destinationCity}</h3>
                  <p className="route-badge">{deal.originAirportCode} to {deal.destinationAirportCode}</p>
                  <p>{formatDate(deal.departureDate)} to {formatDate(deal.returnDate)}</p>
                  <strong>{formatMoney(deal.totalPrice, deal.currency)}</strong>
                  <small>{deal.hotelName} - {deal.providerSummary}</small>
                </Link>
              ))}
            </div>
          )}
        </HomeSection>

        <HomeSection title="Browse by property type">
          {loadingStatic ? (
            <LoadingCards count={4} />
          ) : (
            <div className="home-filter-card-grid">
              {propertyTypes.map((type) => (
                <Link className="home-data-card home-filter-card" key={type.id} to={type.searchUrl}>
                  <span className="home-card-icon">Stay</span>
                  <h3>{type.label}</h3>
                  <p>{type.description}</p>
                  <small>Browse {type.label.toLowerCase()} stays</small>
                </Link>
              ))}
            </div>
          )}
        </HomeSection>

        <HomeSection title="Trending destinations" subtitle="Provider-backed destination ideas">
          {loadingStatic ? (
            <LoadingCards count={4} />
          ) : destinations.length === 0 ? (
            <EmptyState text="No trending destinations are available right now." />
          ) : (
            <div className="home-filter-card-grid">
              {destinations.map((destination) => (
                <Link className="home-data-card home-destination-card" key={destination.id} to={destination.searchUrl}>
                  <span className="home-card-icon">Place</span>
                  <h3>{destination.displayName}</h3>
                  {destination.airportCode && <p className="route-badge">Nearest airport {destination.airportCode}</p>}
                  <div className="home-card-tags">
                    {destination.tags.map((tag) => <span key={tag}>{tag}</span>)}
                  </div>
                  <small>Destination guide</small>
                </Link>
              ))}
            </div>
          )}
        </HomeSection>

        <HomeSection title="Quick and easy trip planner" subtitle="Pick a vibe and open it in The Voyager">
          <div className="planner-tabs" role="tablist" aria-label="Trip planner categories">
            {tripThemes.map((item) => (
              <button
                aria-selected={theme === item.id}
                className={theme === item.id ? "planner-tab planner-tab-active" : "planner-tab"}
                key={item.id}
                onClick={() => setTheme(item.id)}
                role="tab"
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>
          {loadingIdeas ? (
            <LoadingCards count={4} />
          ) : tripIdeas.length === 0 ? (
            <EmptyState text="No trip ideas are available for this theme right now." />
          ) : (
            <div className="home-filter-card-grid">
              {tripIdeas.map((idea) => (
                <Link className="home-data-card home-trip-idea-card" key={idea.id} to={idea.searchUrl}>
                  <span className="home-card-icon">AI idea</span>
                  <h3>{idea.displayName}</h3>
                  <p>{idea.voyagerPrompt}</p>
                  <div className="home-card-tags">
                    {idea.tags.map((tag) => <span key={tag}>{tag}</span>)}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </HomeSection>

        <section className="continue-trip-panel continue-trip-panel-feature">
          <div>
            <p className="eyebrow">Continue your trip</p>
            <h2>{hasVoyagerSession ? "Continue your active Voyage AI trip" : "Start planning with The Voyager"}</h2>
            <p>
              {hasVoyagerSession
                ? "Your current session can keep destination, dates, package options, and cart context together."
                : "Tell The Voyager what kind of trip you want and move from idea to package checkout."}
            </p>
          </div>
          <div className="continue-trip-actions">
            <Link className="button button-primary" to="/voyager">Continue in The Voyager</Link>
            <Link className="button button-secondary" to="/cart">Go to cart</Link>
          </div>
        </section>

        <HomeSection title="Why travellers use Voyage AI">
          <div className="why-voyage-grid">
            {whyVoyage.map((feature, index) => (
              <article className="why-voyage-card" key={feature.title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{feature.title}</h3>
                <p>{feature.text}</p>
              </article>
            ))}
          </div>
        </HomeSection>
      </main>
    </>
  );
}
