import type { NormalisedHotelResult } from "../api/chatApi";
import { inferPropertyType } from "../data/travelSuggestions";

function formatMoney(amount: number, currency: string) {
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatDate(value: string) {
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.valueOf())
    ? value
    : new Intl.DateTimeFormat("en-GB", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(parsed);
}

function Detail({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="hotel-card-detail">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function HotelCard({
  hotel,
  onAddToCart,
}: {
  hotel: NormalisedHotelResult;
  onAddToCart?: (hotel: NormalisedHotelResult) => void;
}) {
  const guestLabel = hotel.guest_count === 1 ? "1 guest" : `${hotel.guest_count} guests`;
  const roomLabel = hotel.room_count === 1 ? "1 room" : `${hotel.room_count} rooms`;
  const propertyType = inferPropertyType(
    `${hotel.hotel_name} ${hotel.room_name ?? ""} ${hotel.description ?? ""}`,
  );

  return (
    <article className="hotel-card">
      <div className="hotel-card-visual">
        <span>{propertyType ?? "Stay"}</span>
      </div>
      <header className="hotel-card-header">
        <div className="hotel-card-title">
          <span className="hotel-option-label">Option {hotel.option_number}</span>
          <h3>{hotel.hotel_name}</h3>
          <span className="hotel-short-id">{hotel.short_id ?? `Hotel ${hotel.option_number}`}</span>
        </div>
        <strong className="hotel-card-price">
          {formatMoney(hotel.price, hotel.currency)}
        </strong>
      </header>

      <div className="hotel-card-badges">
        {hotel.rating !== null && <span className="hotel-badge hotel-badge-rating">{hotel.rating.toFixed(1)} rating</span>}
        {hotel.board_type && <span className="hotel-badge hotel-badge-board">{hotel.board_type}</span>}
        {propertyType && <span className="hotel-badge hotel-badge-board">{propertyType}</span>}
        {hotel.refundable !== null && (
          <span className={`hotel-badge ${hotel.refundable ? "hotel-badge-refundable" : "hotel-badge-nonrefundable"}`}>
            {hotel.refundable ? "Refundable" : "Non-refundable"}
          </span>
        )}
        <span className="hotel-badge hotel-badge-provider">{hotel.provider === "liteapi" ? "LiteAPI" : "Mock fallback"}</span>
      </div>

      <dl className="hotel-card-details">
        <Detail label="Location" value={hotel.location} />
        <Detail label="Stay" value={`${formatDate(hotel.check_in_date)} - ${formatDate(hotel.check_out_date)}`} />
        <Detail label="Nights" value={hotel.nights} />
        <Detail label="Guests / rooms" value={`${guestLabel} / ${roomLabel}`} />
        {hotel.room_name && <Detail label="Room" value={hotel.room_name} />}
        <Detail label="Total price" value={`${formatMoney(hotel.price, hotel.currency)} ${hotel.currency}`} />
      </dl>
      {onAddToCart && (
        <button
          className="card-action-button"
          onClick={() => onAddToCart(hotel)}
          type="button"
        >
          Add to cart
        </button>
      )}
    </article>
  );
}
