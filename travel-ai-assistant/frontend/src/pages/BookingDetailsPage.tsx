import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { AuthSession, BookingDetails } from "../api/authApi";
import { getBooking } from "../api/authApi";
import {
  FlightTicketCard,
  HotelVoucherCard,
} from "../components/TravelDocuments";
import { formatDate, formatMoney } from "../utils/format";

type BookingDetailsPageProps = {
  session: AuthSession | null;
};

export function BookingDetailsPage({ session }: BookingDetailsPageProps) {
  const { reference } = useParams();
  const [booking, setBooking] = useState<BookingDetails | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(session && reference));

  useEffect(() => {
    if (!session || !reference) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    getBooking(reference)
      .then(setBooking)
      .catch((requestError) =>
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Could not load booking details.",
        ),
      )
      .finally(() => setLoading(false));
  }, [reference, session]);

  if (!session) {
    return (
      <section className="page-container">
        <section className="auth-required-panel">
          <p className="eyebrow">Sign in required</p>
          <h1>View booking details</h1>
          <p>Please sign in to view saved tickets and vouchers.</p>
          <Link className="button button-primary" to={`/profile?next=/bookings/${reference ?? ""}`}>
            Sign in
          </Link>
        </section>
      </section>
    );
  }

  return (
    <section className="page-container">
      <div className="page-heading page-heading-row">
        <div>
          <p className="eyebrow">Booking details</p>
          <h1>{reference}</h1>
          <p>Review your saved trip summary and travel documents.</p>
        </div>
        <Link className="button button-secondary" to="/profile">
          Back to Profile
        </Link>
      </div>

      {loading ? (
        <div className="loading-skeleton">Loading booking details...</div>
      ) : error ? (
        <section className="empty-state">
          <h2>Could not load this booking</h2>
          <p>{error}</p>
        </section>
      ) : booking ? (
        <>
          <section className="booking-confirmation">
            <p className="eyebrow">{booking.status}</p>
            <h1>Booking reference {booking.reference}</h1>
            <p>{booking.summary}</p>
            <dl className="result-details">
              <div className="result-detail">
                <dt>Destination</dt>
                <dd>{booking.destination}</dd>
              </div>
              <div className="result-detail">
                <dt>Created</dt>
                <dd>{formatDate(booking.created_at.slice(0, 10))}</dd>
              </div>
              <div className="result-detail">
                <dt>Total</dt>
                <dd>{formatMoney(booking.totalPrice ?? booking.estimated_price, booking.currency)}</dd>
              </div>
              <div className="result-detail">
                <dt>Type</dt>
                <dd>{booking.booking_type.replace(/_/g, " ")}</dd>
              </div>
            </dl>
          </section>

          {(booking.flightTicket || booking.hotelVoucher) ? (
            <section className="document-grid">
              {booking.flightTicket && (
                <FlightTicketCard ticket={booking.flightTicket} />
              )}
              {booking.hotelVoucher && (
                <HotelVoucherCard voucher={booking.hotelVoucher} />
              )}
            </section>
          ) : (
            <section className="empty-state">
              <h2>Ticket details unavailable</h2>
              <p>Ticket details were not saved for this older booking.</p>
            </section>
          )}
        </>
      ) : null}
    </section>
  );
}
