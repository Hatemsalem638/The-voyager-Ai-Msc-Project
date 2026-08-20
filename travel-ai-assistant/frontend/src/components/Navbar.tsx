import { NavLink } from "react-router-dom";

import type { AuthSession } from "../api/authApi";
import { useCart } from "../context/CartContext";

type NavbarProps = {
  session: AuthSession | null;
  onLogout: () => void;
};

const navigation = [
  { to: "/", label: "Home", end: true },
  { to: "/hotels", label: "Stays" },
  { to: "/flights", label: "Flights" },
  { to: "/packages", label: "Packages" },
  { to: "/voyager", label: "The Voyager" },
  { to: "/my-trips", label: "My Trips" },
];

export function Navbar({ session, onLogout }: NavbarProps) {
  const { itemCount } = useCart();

  return (
    <header className="site-navbar">
      
      <div className="navbar-inner">
        <NavLink className="site-brand" to="/">
          <span className="brand-wordmark">Voyage AI</span>
          <span className="brand-tagline">Your travel companion</span>
        </NavLink>

        <nav aria-label="Main navigation" className="primary-nav">
          {navigation.map((item) => (
            <NavLink
              className={({ isActive }) =>
                `nav-link${isActive ? " nav-link-active" : ""}`
              }
              end={item.end}
              key={item.to}
              to={item.to}
            >
              {item.label}
            </NavLink>
          ))}
          <NavLink
            className={({ isActive }) =>
              `nav-link nav-cart-link${isActive ? " nav-link-active" : ""}`
            }
            to="/cart"
          >
            Cart <span className="cart-count">{itemCount}</span>
          </NavLink>
        </nav>

        <div className="navbar-account">
          <NavLink className="account-link" to="/profile">
            {session?.user.full_name || session?.user.email || "Profile / Login"}
          </NavLink>
          {session && (
            <button className="navbar-logout" onClick={onLogout} type="button">
              Log out
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
