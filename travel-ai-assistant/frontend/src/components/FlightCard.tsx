import type { NormalisedFlightResult } from "../api/flightApi";

function formatDate(value: string | null | undefined) {
  if (!value) {
    return "Not available";
  }

  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(parsed);
}

function formatPrice(price: string | null | undefined, currency: string | null | undefined) {
  if (!price) {
    return "Price unavailable";
  }

  const amount = Number(price);
  if (Number.isNaN(amount) || !currency) {
    return currency ? `${price} ${currency}` : price;
  }

  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function FlightDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flight-card-detail">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function FlightCard({
  flight,
  onAddToCart,
}: {
  flight: NormalisedFlightResult;
  onAddToCart?: (flight: NormalisedFlightResult) => void;
}) {
  const optionLabel = flight.option_number
    ? `Option ${flight.option_number}`
    : "Flight option";

  const passengerLabel =
    flight.passenger_count === 1
      ? "1 passenger"
      : flight.passenger_count
        ? `${flight.passenger_count} passengers`
        : "Passengers unavailable";

  const cabinLabel =
    flight.cabin_class_label ??
    (flight.cabin_class
      ? flight.cabin_class.replace(/_/g, " ")
      : "Cabin unavailable");

  const stopsLabel =
    flight.stops_label ??
    (flight.stops === 0
      ? "Non-stop"
      : flight.stops === 1
        ? "1 stop"
        : flight.stops === null || flight.stops === undefined
          ? "Stops unavailable"
          : `${flight.stops} stops`);

  const providerLabel =
    flight.provider === "duffel"
      ? "Duffel"
      : flight.provider ?? "Provider unavailable";

  const originLabel = flight.origin ?? "Origin unavailable";
  const destinationLabel = flight.destination ?? "Destination unavailable";

  return (
    <article className="flight-card">
      <header className="flight-card-header">
        <div className="flight-card-title">
          <span className="flight-option-label">{optionLabel}</span>
          <h3>{flight.airline ?? "Airline unavailable"}</h3>
          <span className="flight-offer-id">
            Offer: {flight.display_id ?? "Not available"}
          </span>
        </div>

        <strong className="flight-card-price">
          {formatPrice(flight.price, flight.currency)}
        </strong>
      </header>

      <div className="flight-card-badges" aria-label="Flight attributes">
        <span className="flight-badge flight-badge-cabin">{cabinLabel}</span>
        <span className="flight-badge flight-badge-stops">{stopsLabel}</span>
        <span className="flight-badge flight-badge-provider">
          {providerLabel}
        </span>
      </div>

      <div className="flight-route-strip" aria-label="Flight route">
        <div className="flight-route-point">
          <strong>{originLabel}</strong>
          <span>{flight.outbound_departure_time ?? "Time unavailable"}</span>
        </div>
        <div className="flight-route-line">
          <span />
        </div>
        <div className="flight-route-point flight-route-point-end">
          <strong>{destinationLabel}</strong>
          <span>{flight.duration_label ?? stopsLabel}</span>
        </div>
      </div>

      <dl className="flight-card-details">
        <FlightDetail
          label="Outbound"
          value={formatDate(flight.outbound_departure_date)}
        />
        <FlightDetail
          label="Outbound time"
          value={flight.outbound_departure_time ?? "Not available"}
        />
        <FlightDetail
          label="Return"
          value={formatDate(flight.return_departure_date)}
        />
        <FlightDetail
          label="Return time"
          value={flight.return_departure_time ?? "Not available"}
        />
        <FlightDetail label="Cabin" value={cabinLabel} />
        <FlightDetail label="Passengers" value={passengerLabel} />
        <FlightDetail label="Stops" value={stopsLabel} />

        {flight.duration_label && (
          <FlightDetail label="Duration" value={flight.duration_label} />
        )}

        <FlightDetail label="Provider" value={providerLabel} />
      </dl>

      {onAddToCart && (
        <button
          className="card-action-button"
          onClick={() => onAddToCart(flight)}
          type="button"
        >
          Add to cart
        </button>
      )}
    </article>
  );
}
