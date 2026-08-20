import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { AuthSession, UserSummary } from "../api/authApi";
import { LoginForm } from "../components/LoginForm";
import { ProfilePanel } from "../components/ProfilePanel";
import { RegisterForm } from "../components/RegisterForm";

type ProfilePageProps = {
  session: AuthSession | null;
  onAuthenticated: (session: AuthSession) => void;
  onLogout: () => void;
  onUserUpdated: (user: UserSummary) => void;
};

export function ProfilePage({
  session,
  onAuthenticated,
  onLogout,
  onUserUpdated,
}: ProfilePageProps) {
  const [showRegistration, setShowRegistration] = useState(false);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const nextPath = searchParams.get("next");

  function handleAuthenticated(session: AuthSession) {
    onAuthenticated(session);
    if (nextPath && nextPath.startsWith("/")) {
      navigate(nextPath);
    }
  }

  return (
    <section className="page-container profile-page">
      <div className="page-heading">
        <p className="eyebrow">Your Voyage AI account</p>
        <h1>Profile</h1>
        <p>Manage your local profile and travel preferences.</p>
      </div>
      {session ? (
        <ProfilePanel
          onLogout={onLogout}
          onUserUpdated={onUserUpdated}
          user={session.user}
        />
      ) : showRegistration ? (
        <RegisterForm
          onAuthenticated={handleAuthenticated}
          onLoginRequested={() => setShowRegistration(false)}
        />
      ) : (
        <LoginForm
          onAuthenticated={handleAuthenticated}
          onRegisterRequested={() => setShowRegistration(true)}
        />
      )}
    </section>
  );
}
