import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  type BookingDetails,
  type UserPreferences,
  type UserSummary,
  getBookings,
  getPreferences,
  updateCurrentUser,
  updatePreferences,
} from "../api/authApi";
import { formatMoney } from "../utils/format";

type ProfilePanelProps = {
  user: UserSummary;
  onLogout: () => void;
  onUserUpdated: (user: UserSummary) => void;
};

type PreferenceFormState = {
  preferred_cabin_class: string;
  max_stops: string;
  budget_min: string;
  budget_max: string;
  preferred_hotel_rating: string;
  hotel_location_preference: string;
  sustainability_preference: string;
  accessibility_needs: string;
};

const EMPTY_PREFERENCES: PreferenceFormState = {
  preferred_cabin_class: "",
  max_stops: "",
  budget_min: "",
  budget_max: "",
  preferred_hotel_rating: "",
  hotel_location_preference: "",
  sustainability_preference: "",
  accessibility_needs: "",
};

function toFormState(preferences: UserPreferences): PreferenceFormState {
  return {
    preferred_cabin_class: preferences.preferred_cabin_class ?? "",
    max_stops: preferences.max_stops?.toString() ?? "",
    budget_min: preferences.budget_min?.toString() ?? "",
    budget_max: preferences.budget_max?.toString() ?? "",
    preferred_hotel_rating: preferences.preferred_hotel_rating?.toString() ?? "",
    hotel_location_preference: preferences.hotel_location_preference ?? "",
    sustainability_preference: preferences.sustainability_preference ?? "",
    accessibility_needs:
      typeof preferences.accessibility_needs === "string"
        ? preferences.accessibility_needs
        : "",
  };
}

function optionalNumber(value: string): number | null {
  return value.trim() ? Number(value) : null;
}

export function ProfilePanel({
  user,
  onLogout,
  onUserUpdated,
}: ProfilePanelProps) {
  const [fullName, setFullName] = useState(user.full_name ?? "");
  const [preferences, setPreferences] = useState<PreferenceFormState>(
    EMPTY_PREFERENCES,
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [bookings, setBookings] = useState<BookingDetails[]>([]);
  const [bookingsError, setBookingsError] = useState("");
  const [bookingsLoading, setBookingsLoading] = useState(false);

  useEffect(() => {
    setFullName(user.full_name ?? "");
    getPreferences()
      .then((response) => setPreferences(toFormState(response)))
      .catch(() => setError("Could not load account preferences."));

    setBookingsLoading(true);
    setBookingsError("");
    getBookings()
      .then((response) => setBookings(response.bookings))
      .catch(() => setBookingsError("Could not load previous bookings."))
      .finally(() => setBookingsLoading(false));
  }, [user.id, user.full_name]);

  function updatePreference(
    field: keyof PreferenceFormState,
    value: string,
  ) {
    setPreferences((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    setIsSaving(true);

    try {
      const profile = await updateCurrentUser({ full_name: fullName || null });
      await updatePreferences({
        preferred_cabin_class: preferences.preferred_cabin_class
          ? (preferences.preferred_cabin_class as UserPreferences["preferred_cabin_class"])
          : null,
        max_stops: optionalNumber(preferences.max_stops),
        budget_min: optionalNumber(preferences.budget_min),
        budget_max: optionalNumber(preferences.budget_max),
        preferred_hotel_rating: optionalNumber(preferences.preferred_hotel_rating),
        hotel_location_preference: preferences.hotel_location_preference || null,
        sustainability_preference: preferences.sustainability_preference || null,
        accessibility_needs: preferences.accessibility_needs || null,
      });
      onUserUpdated({
        id: profile.id,
        email: profile.email,
        full_name: profile.full_name,
      });
      setNotice("Profile and preferences saved.");
    } catch (requestError) {
      setError(
        requestError instanceof Error ? requestError.message : "Unable to save profile.",
      );
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="profile-panel" aria-label="Account and preferences">
      <div className="profile-panel-header">
        <div>
          <p className="auth-kicker">Signed in</p>
          <h2>{user.full_name || user.email}</h2>
          <p>{user.email}</p>
        </div>
        <button className="secondary-button" onClick={onLogout} type="button">
          Log out
        </button>
      </div>
      <form className="preferences-form" onSubmit={handleSubmit}>
        <label>
          <span>Full name</span>
          <input
            autoComplete="name"
            onChange={(event) => setFullName(event.target.value)}
            type="text"
            value={fullName}
          />
        </label>
        <label>
          <span>Preferred cabin</span>
          <select
            onChange={(event) =>
              updatePreference("preferred_cabin_class", event.target.value)
            }
            value={preferences.preferred_cabin_class}
          >
            <option value="">No preference</option>
            <option value="economy">Economy</option>
            <option value="premium_economy">Premium economy</option>
            <option value="business">Business</option>
            <option value="first">First</option>
          </select>
        </label>
        <label>
          <span>Maximum stops</span>
          <input
            min="0"
            onChange={(event) => updatePreference("max_stops", event.target.value)}
            type="number"
            value={preferences.max_stops}
          />
        </label>
        <label>
          <span>Budget minimum</span>
          <input
            min="0"
            onChange={(event) => updatePreference("budget_min", event.target.value)}
            type="number"
            value={preferences.budget_min}
          />
        </label>
        <label>
          <span>Budget maximum</span>
          <input
            min="0"
            onChange={(event) => updatePreference("budget_max", event.target.value)}
            type="number"
            value={preferences.budget_max}
          />
        </label>
        <label>
          <span>Preferred hotel rating</span>
          <input
            max="5"
            min="1"
            onChange={(event) =>
              updatePreference("preferred_hotel_rating", event.target.value)
            }
            type="number"
            value={preferences.preferred_hotel_rating}
          />
        </label>
        <label>
          <span>Hotel location</span>
          <input
            onChange={(event) =>
              updatePreference("hotel_location_preference", event.target.value)
            }
            type="text"
            value={preferences.hotel_location_preference}
          />
        </label>
        <label>
          <span>Sustainability preference</span>
          <input
            onChange={(event) =>
              updatePreference("sustainability_preference", event.target.value)
            }
            type="text"
            value={preferences.sustainability_preference}
          />
        </label>
        <label className="preferences-wide">
          <span>Accessibility needs</span>
          <input
            onChange={(event) =>
              updatePreference("accessibility_needs", event.target.value)
            }
            type="text"
            value={preferences.accessibility_needs}
          />
        </label>
        {error && <p className="auth-error preferences-wide">{error}</p>}
        {notice && <p className="auth-notice preferences-wide">{notice}</p>}
        <button className="preferences-wide" disabled={isSaving} type="submit">
          {isSaving ? "Saving..." : "Save preferences"}
        </button>
      </form>

      <section className="profile-bookings">
        <div>
          <p className="auth-kicker">My trips</p>
          <h2>Previous bookings</h2>
        </div>
        {bookingsLoading ? (
          <p>Loading your trips...</p>
        ) : bookingsError ? (
          <p className="auth-error">{bookingsError}</p>
        ) : bookings.length === 0 ? (
          <p>No bookings yet. Build a trip and complete checkout to see it here.</p>
        ) : (
          <div className="booking-history-list">
            {bookings.map((booking) => (
              <article className="booking-history-card" key={booking.reference}>
                <div>
                  <strong>{booking.reference}</strong>
                  <span>{booking.status}</span>
                </div>
                <p>{booking.summary}</p>
                <div className="booking-history-meta">
                  <strong>
                    {formatMoney(
                      booking.totalPrice ?? booking.estimated_price,
                      booking.currency,
                    )}
                  </strong>
                  <span>{booking.destination}</span>
                </div>
                <div className="booking-history-actions">
                  <Link className="button button-secondary" to={`/bookings/${booking.reference}`}>
                    View details
                  </Link>
                  {(booking.flightTicket || booking.hotelVoucher) && (
                    <Link className="button button-primary" to={`/bookings/${booking.reference}`}>
                      View tickets
                    </Link>
                  )}
                </div>
                <small>
                  {booking.booking_type.replace(/_/g, " ")} ·{" "}
                  {new Date(booking.created_at).toLocaleDateString("en-GB")}
                </small>
              </article>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
