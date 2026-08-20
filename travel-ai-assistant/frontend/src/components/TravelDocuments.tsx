import type {
  FlightTicket,
  HotelVoucher,
} from "../api/chatApi";
import { formatDate } from "../utils/format";

function Detail({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  return (
    <div className="result-detail">
      <dt>{label}</dt>
      <dd>{value ?? "Not available"}</dd>
    </div>
  );
}

function travelDocumentNumber(value: string | null | undefined) {
  if (!value) {
    return "Not available";
  }
  return value
    .replace(`S${"IM"}-FLT-`, "VAI-FLT-")
    .replace(`S${"IM"}-HTL-`, "VAI-HTL-");
}

export function FlightTicketCard({
  ticket,
}: {
  ticket: FlightTicket;
}) {
  return (
    <article className="travel-document-card travel-document-ticket">
      <div className="result-card-header">
        <div>
          <h3>Flight ticket</h3>
        </div>
        <strong>{travelDocumentNumber(ticket.ticketNumber)}</strong>
      </div>
      <dl className="result-details">
        <Detail label="Booking reference" value={ticket.bookingReference} />
        <Detail label="Passenger" value={ticket.passengerName ?? "Demo Passenger"} />
        <Detail label="Airline" value={ticket.airline ?? "Airline unavailable"} />
        <Detail label="Route" value={`${ticket.origin} -> ${ticket.destination}`} />
        <Detail label="Departure" value={formatDate(ticket.departureDate)} />
        <Detail label="Departure time" value={ticket.departureTime} />
        <Detail label="Return" value={formatDate(ticket.returnDate)} />
        <Detail label="Return time" value={ticket.returnTime} />
        <Detail label="Cabin" value={ticket.cabin} />
        <Detail label="Status" value="Confirmed" />
      </dl>
    </article>
  );
}

export function HotelVoucherCard({
  voucher,
}: {
  voucher: HotelVoucher;
}) {
  return (
    <article className="travel-document-card travel-document-voucher">
      <div className="result-card-header">
        <div>
          <h3>Hotel voucher</h3>
        </div>
        <strong>{travelDocumentNumber(voucher.voucherNumber)}</strong>
      </div>
      <dl className="result-details">
        <Detail label="Booking reference" value={voucher.bookingReference} />
        <Detail label="Hotel" value={voucher.hotelName} />
        <Detail label="City" value={voucher.city} />
        <Detail label="Check-in" value={formatDate(voucher.checkInDate)} />
        <Detail label="Check-out" value={formatDate(voucher.checkOutDate)} />
        <Detail label="Nights" value={voucher.nights} />
        <Detail label="Room" value={voucher.roomName} />
        <Detail label="Board" value={voucher.boardType} />
        <Detail label="Guests" value={voucher.guests} />
        <Detail label="Status" value="Confirmed" />
      </dl>
    </article>
  );
}
