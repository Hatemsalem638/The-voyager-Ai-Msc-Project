import { Link } from "react-router-dom";

import { useCart } from "../context/CartContext";
import { formatMoney } from "../utils/format";

export function CartPage() {
  const { items, removeItem, clearCart, totalsByCurrency } = useCart();
  const currencies = Object.entries(totalsByCurrency);
  const latestBooking =
    typeof window !== "undefined"
      ? window.localStorage.getItem("voyage-ai-latest-booking")
      : null;

  return (
    <section className="page-container">
      <div className="page-heading page-heading-row">
        <div>
          <p className="eyebrow">Manual trip builder</p>
          <h1>Your Trip Cart</h1>
          <p>Review the flight and hotel options you want to include.</p>
        </div>
        {items.length > 0 && (
          <button className="button button-text" onClick={clearCart} type="button">
            Clear cart
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <section className="empty-state">
          <h2>Your cart is empty</h2>
          <p>Search flights, hotels, or ask The Voyager to build a travel package.</p>
          {latestBooking && (
            <p>
              Your latest booking is {latestBooking}. You can reopen its tickets
              from your booking details.
            </p>
          )}
          <div className="empty-state-actions">
            {latestBooking && (
              <Link className="button button-primary" to={`/bookings/${latestBooking}`}>
                View booking
              </Link>
            )}
            <Link className="button button-primary" to="/flights">
              Search Flights
            </Link>
            <Link className="button button-secondary" to="/hotels">
              Find Hotels
            </Link>
          </div>
        </section>
      ) : (
        <div className="cart-layout">
          <div className="cart-items">
            {items.map((item) => (
              <article className="cart-item" key={item.cartItemId}>
                <div className="cart-item-main">
                  <span className={`cart-item-type cart-item-type-${item.type}`}>
                    {item.type}
                  </span>
                  <h2>{item.title}</h2>
                  <p>{item.subtitle}</p>
                  <small>Provider: {item.provider}</small>
                </div>
                <div className="cart-item-price">
                  <strong>{formatMoney(item.price, item.currency)}</strong>
                  <button
                    className="button button-text"
                    onClick={() => removeItem(item.cartItemId)}
                    type="button"
                  >
                    Remove
                  </button>
                </div>
              </article>
            ))}
          </div>
          <aside className="cart-summary">
            <h2>Trip total</h2>
            {currencies.map(([currency, total]) => (
              <p key={currency}>
                <span>{currency}</span>
                <strong>{formatMoney(total, currency)}</strong>
              </p>
            ))}
            {currencies.length > 1 && (
              <small>
                Cart contains mixed currencies; totals are shown separately.
              </small>
            )}
            <Link className="button button-primary checkout-link" to="/checkout">
              Proceed to Checkout
            </Link>
          </aside>
        </div>
      )}
    </section>
  );
}
