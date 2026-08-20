import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { AuthSession } from "../api/authApi";
import {
  type CheckoutResponse,
  confirmCheckout,
} from "../api/checkoutApi";
import {
  FlightTicketCard,
  HotelVoucherCard,
} from "../components/TravelDocuments";
import { useCart } from "../context/CartContext";
import { formatMoney } from "../utils/format";

type CheckoutPageProps = {
  session: AuthSession | null;
};

export function CheckoutPage({ session }: CheckoutPageProps) {
  const navigate = useNavigate();
  const { items, totalsByCurrency, clearCart } = useCart();
  const [fullName, setFullName] = useState(session?.user.full_name ?? "");
  const [email, setEmail] = useState(session?.user.email ?? "");
  const [phone, setPhone] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [confirmation, setConfirmation] = useState<CheckoutResponse | null>(null);

  useEffect(() => {
    setFullName(session?.user.full_name ?? "");
    setEmail(session?.user.email ?? "");
  }, [session]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) {
      navigate("/profile?next=/checkout");
      return;
    }
    setError("");
    setIsSubmitting(true);
    try {
      const response = await confirmCheckout(
        items,
        { full_name: fullName, email, phone: phone || undefined },
        notes,
      );
      setConfirmation(response);
      clearCart();
      localStorage.setItem("voyage-ai-latest-booking", response.booking_reference);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Could not create the booking.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  if (confirmation) {
    return (
      <section className="page-container">
        <section className="booking-confirmation">
          <p className="eyebrow">Booking confirmed</p>
          <h1>Booking reference {confirmation.booking_reference}</h1>
          <p>{confirmation.message}</p>
          {confirmation.cartCleared && (
            <p>Your cart has been checked out and cleared.</p>
          )}
          <p>{confirmation.summary}</p>
          <div className="confirmation-totals">
            {Object.entries(confirmation.totals_by_currency).map(
              ([currency, total]) => (
                <strong key={currency}>{formatMoney(total, currency)}</strong>
              ),
            )}
          </div>
          {(confirmation.flightTicket || confirmation.hotelVoucher) && (
            <div className="document-grid">
              {confirmation.flightTicket && (
                <FlightTicketCard ticket={confirmation.flightTicket} />
              )}
              {confirmation.hotelVoucher && (
                <HotelVoucherCard voucher={confirmation.hotelVoucher} />
              )}
            </div>
          )}
          <div className="hero-actions">
            <button className="button button-primary" onClick={() => navigate("/")} type="button">
              Go home
            </button>
            <Link className="button button-secondary" to="/profile">
              View Profile
            </Link>
            <Link className="button button-secondary" to={`/bookings/${confirmation.booking_reference}`}>
              View tickets
            </Link>
          </div>
        </section>
      </section>
    );
  }

  if (items.length === 0) {
    return (
      <section className="page-container">
        <div className="page-heading">
          <p className="eyebrow">Your journey</p>
          <h1>Checkout</h1>
        </div>
        <section className="empty-state">
          <h2>Your cart is empty</h2>
          <p>Add a travel package, flight, or hotel before starting checkout.</p>
          <Link className="button button-primary" to="/flights">
            Search Flights
          </Link>
        </section>
      </section>
    );
  }

  return (
    <section className="page-container">
      <div className="page-heading">
        <p className="eyebrow">Your journey</p>
        <h1>Checkout</h1>
        <p>Review your journey, add your contact details, and confirm your booking.</p>
      </div>

      {!session && (
        <section className="auth-required-panel">
          <p className="eyebrow">Sign in required</p>
          <h2>Please sign in to complete checkout</h2>
          <p>
            I saved your selected trip cart on this device, so you can continue
            checkout after signing in or creating an account.
          </p>
          <div className="hero-actions">
            <Link className="button button-primary" to="/profile?next=/checkout">
              Sign in or register
            </Link>
            <Link className="button button-secondary" to="/cart">
              Back to cart
            </Link>
          </div>
        </section>
      )}

      <div className="checkout-layout">
        <form className="checkout-form" onSubmit={handleSubmit}>
          <h2>Traveller and contact</h2>
          <label>
            <span>Full name</span>
            <input
              autoComplete="name"
              onChange={(event) => setFullName(event.target.value)}
              required
              value={fullName}
            />
          </label>
          <label>
            <span>Email</span>
            <input
              autoComplete="email"
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
              value={email}
            />
          </label>
          <label>
            <span>Phone (optional)</span>
            <input
              autoComplete="tel"
              onChange={(event) => setPhone(event.target.value)}
              type="tel"
              value={phone}
            />
          </label>
          <label className="checkout-notes">
            <span>Notes (optional)</span>
            <textarea
              onChange={(event) => setNotes(event.target.value)}
              rows={4}
              value={notes}
            />
          </label>
          {error && <p className="form-message form-message-error">{error}</p>}
          <button className="button button-primary" disabled={isSubmitting} type="submit">
            {!session
              ? "Sign in to checkout"
              : isSubmitting
                ? "Creating booking..."
                : "Confirm Booking"}
          </button>
        </form>

        <aside className="checkout-summary">
          <h2>Order summary</h2>
          {items.map((item) => (
            <p className="checkout-line-item" key={item.cartItemId}>
              <span>{item.title}</span>
              <strong>{formatMoney(item.price, item.currency)}</strong>
            </p>
          ))}
          <div className="checkout-total-list">
            {Object.entries(totalsByCurrency).map(([currency, total]) => (
              <p key={currency}>
                <span>Total ({currency})</span>
                <strong>{formatMoney(total, currency)}</strong>
              </p>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}
