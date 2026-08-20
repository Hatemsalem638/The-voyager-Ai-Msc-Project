import { type FormEvent, useState } from "react";

import { type AuthSession, registerUser } from "../api/authApi";

type RegisterFormProps = {
  onAuthenticated: (session: AuthSession) => void;
  onLoginRequested: () => void;
};

export function RegisterForm({
  onAuthenticated,
  onLoginRequested,
}: RegisterFormProps) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError("");

    try {
      const session = await registerUser({
        email,
        password,
        full_name: fullName || undefined,
      });
      onAuthenticated(session);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to create the account.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="auth-panel" aria-label="Create account">
      <div>
        <p className="auth-kicker">Local account</p>
        <h2>Create account</h2>
      </div>
      <form className="auth-form" onSubmit={handleSubmit}>
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
          <span>Password</span>
          <div className="password-field">
            <input
              autoComplete="new-password"
              minLength={8}
              onChange={(event) => setPassword(event.target.value)}
              required
              type={showPassword ? "text" : "password"}
              value={password}
            />
            <button
              className="password-toggle"
              onClick={() => setShowPassword((visible) => !visible)}
              type="button"
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
        </label>
        {error && <p className="auth-error">{error}</p>}
        <button disabled={isSubmitting} type="submit">
          {isSubmitting ? "Creating..." : "Create account"}
        </button>
      </form>
      <button className="auth-link" onClick={onLoginRequested} type="button">
        I already have an account
      </button>
    </section>
  );
}
