import { useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import {
  clearAuthSession,
  getAuthSession,
  saveAuthSession,
  type AuthSession,
  type UserSummary,
} from "./api/authApi";
import { AppLayout } from "./layout/AppLayout";
import { BookingDetailsPage } from "./pages/BookingDetailsPage";
import { CartPage } from "./pages/CartPage";
import { CheckoutPage } from "./pages/CheckoutPage";
import { FlightsPage } from "./pages/FlightsPage";
import { HomePage } from "./pages/HomePage";
import { HotelsPage } from "./pages/HotelsPage";
import { ProfilePage } from "./pages/ProfilePage";
import { VoyagerPage } from "./pages/VoyagerPage";

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(getAuthSession);

  function handleAuthenticated(nextSession: AuthSession) {
    saveAuthSession(nextSession);
    setSession(nextSession);
  }

  function handleLogout() {
    clearAuthSession();
    setSession(null);
  }

  function handleUserUpdated(user: UserSummary) {
    if (!session) {
      return;
    }
    const updatedSession = { ...session, user };
    saveAuthSession(updatedSession);
    setSession(updatedSession);
  }

  return (
    <Routes>
      <Route element={<AppLayout onLogout={handleLogout} session={session} />}>
        <Route element={<HomePage />} path="/" />
        <Route element={<FlightsPage />} path="/flights" />
        <Route element={<HotelsPage />} path="/hotels" />
        <Route element={<Navigate replace to="/voyager" />} path="/packages" />
        <Route element={<CartPage />} path="/cart" />
        <Route element={<CheckoutPage session={session} />} path="/checkout" />
        <Route
          element={<BookingDetailsPage session={session} />}
          path="/bookings/:reference"
        />
        <Route element={<VoyagerPage />} path="/voyager" />
        <Route
          element={
            <ProfilePage
              onAuthenticated={handleAuthenticated}
              onLogout={handleLogout}
              onUserUpdated={handleUserUpdated}
              session={session}
            />
          }
          path="/profile"
        />
        <Route element={<Navigate replace to="/profile" />} path="/signin" />
        <Route element={<Navigate replace to="/profile" />} path="/register" />
        <Route element={<Navigate replace to="/profile" />} path="/my-trips" />
        <Route element={<Navigate replace to="/" />} path="*" />
      </Route>
    </Routes>
  );
}
