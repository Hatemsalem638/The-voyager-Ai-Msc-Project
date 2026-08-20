import { Outlet } from "react-router-dom";

import type { AuthSession } from "../api/authApi";
import { Navbar } from "../components/Navbar";

type AppLayoutProps = {
  session: AuthSession | null;
  onLogout: () => void;
};

export function AppLayout({ session, onLogout }: AppLayoutProps) {
  return (
    <div className="site-shell">
      <Navbar onLogout={onLogout} session={session} />
      <main className="site-main">
        <Outlet />
      </main>
    </div>
  );
}
