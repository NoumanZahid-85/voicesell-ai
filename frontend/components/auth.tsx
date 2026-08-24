"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter, usePathname } from "next/navigation";
import { EnvelopeSimple, LockKey, UserCircle, Waveform } from "@phosphor-icons/react";
import {
  authConfigured,
  getOrCreateGuestId,
  getSupabase,
  GUEST_CHOICE_KEY,
} from "@/lib/supabase";

/**
 * Identity layer for the whole console.
 *
 * Two modes:
 *  - "user":  Supabase email/password session. customerId = auth user id.
 *  - "guest": per-browser random id (developer/demo mode). A fresh incognito
 *             window gets a brand-new id, so guests never see each other's
 *             orders — fixing the old shared-demo-customer behaviour.
 *
 * If Supabase env vars are missing, only guest mode is offered.
 */

export type IdentityMode = "user" | "guest";

interface Identity {
  customerId: string;
  mode: IdentityMode;
  label: string; // email or "Guest · <short id>"
}

interface AuthCtx {
  identity: Identity | null;
  ready: boolean;
  signIn: (email: string, password: string) => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<string | null>;
  resetPassword: (email: string) => Promise<string | null>;
  signOut: () => Promise<void>;
  continueAsGuest: () => void;
}

const Ctx = createContext<AuthCtx>({
  identity: null,
  ready: false,
  signIn: async () => null,
  signUp: async () => null,
  resetPassword: async () => null,
  signOut: async () => {},
  continueAsGuest: () => {},
});

export function useIdentity() {
  return useContext(Ctx);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [ready, setReady] = useState(false);

  // Restore an existing Supabase session on load; fall back to nothing so
  // the gate shows. Guest identity is intentionally NOT auto-restored into
  // the gate bypass when auth is configured — but IS used directly when
  // auth is not configured at all.
  useEffect(() => {
    let cancelled = false;
    const supabase = getSupabase();
    if (!supabase) {
      // Auth not configured: guest-only mode, no login screen.
      setIdentity({
        customerId: getOrCreateGuestId(),
        mode: "guest",
        label: `Guest · ${getOrCreateGuestId().slice(0, 8)}`,
      });
      setReady(true);
      return;
    }
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (cancelled) return;
        const u = data.session?.user;
        if (u) {
          setIdentity({ customerId: u.id, mode: "user", label: u.email ?? "Signed in" });
        } else if (
          typeof window !== "undefined" &&
          window.sessionStorage.getItem(GUEST_CHOICE_KEY) === "1"
        ) {
          // This tab already chose guest mode — keep it across reloads so
          // testing doesn't bounce back to the login gate every refresh.
          const id = getOrCreateGuestId();
          setIdentity({ customerId: id, mode: "guest", label: `Guest · ${id.slice(0, 8)}` });
        }
        setReady(true);
      })
      .catch(() => !cancelled && setReady(true));

    const { data: sub } = supabase.auth.onAuthStateChange((_evt, session) => {
      const u = session?.user;
      setIdentity(
        u
          ? { customerId: u.id, mode: "user", label: u.email ?? "Signed in" }
          : null,
      );
    });
    return () => {
      cancelled = true;
      sub.subscription.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const supabase = getSupabase();
    if (!supabase) return "Auth is not configured on this deployment.";
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return error ? error.message : null;
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    const supabase = getSupabase();
    if (!supabase) return "Auth is not configured on this deployment.";
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        // Where "Confirm your email" links land when confirmation is on.
        emailRedirectTo: typeof window !== "undefined" ? window.location.origin : undefined,
      },
    });
    if (error) return error.message;
    // Project setting "Confirm email" may require verification before a
    // session exists — surface that instead of a confusing empty state.
    if (!data.session && data.user) {
      return "CHECK_EMAIL";
    }
    return null;
  }, []);

  const resetPassword = useCallback(async (email: string) => {
    const supabase = getSupabase();
    if (!supabase) return "Auth is not configured on this deployment.";
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: typeof window !== "undefined" ? window.location.origin : undefined,
    });
    return error ? error.message : null;
  }, []);

  const signOut = useCallback(async () => {
    // Robust by construction: whatever happens below (invalid key, network
    // failure, no session), the login gate MUST come back. A thrown error
    // here used to skip setIdentity(null) entirely — the reported
    // "sign out does nothing" bug.
    try {
      if (typeof window !== "undefined") {
        window.sessionStorage.removeItem(GUEST_CHOICE_KEY);
      }
      await getSupabase()?.auth.signOut();
    } catch (err) {
      console.warn("signOut: supabase call failed (clearing local identity anyway)", err);
    } finally {
      setIdentity(null);
    }
  }, []);

  const continueAsGuest = useCallback(() => {
    const id = getOrCreateGuestId();
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem(GUEST_CHOICE_KEY, "1");
    }
    setIdentity({ customerId: id, mode: "guest", label: `Guest · ${id.slice(0, 8)}` });
  }, []);

  const value = useMemo(
    () => ({ identity, ready, signIn, signUp, resetPassword, signOut, continueAsGuest }),
    [identity, ready, signIn, signUp, resetPassword, signOut, continueAsGuest],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Gate: blocks rendering until an identity exists (signed-in user or an
 * explicit guest choice). When auth isn't configured, the provider already
 * assigns a guest id, so children render immediately.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { identity, ready } = useIdentity();
  const router = useRouter();
  const pathname = usePathname();

  // Protected routes never render an inline login card — they bounce to
  // the dedicated /login page (preserving the destination for after auth).
  useEffect(() => {
    if (ready && !identity) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [ready, identity, router, pathname]);

  if (!ready || !identity) return null;
  return <>{children}</>;
}

/* ── Login / signup screen (rendered on the dedicated /login page) ──── */

export function LoginScreen({
  initialMode = "signin",
}: { initialMode?: "signin" | "signup" } = {}) {
  const { signIn, signUp, resetPassword, continueAsGuest } = useIdentity();
  const [mode, setMode] = useState<"signin" | "signup">(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const err =
        mode === "signin" ? await signIn(email, password) : await signUp(email, password);
      if (err === "CHECK_EMAIL") {
        setNotice("Account created. Check your inbox to confirm your email, then sign in.");
      } else if (err) {
        setError(err);
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? `Unexpected error: ${err.message}`
          : "Unexpected error — please try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  const forgot = async () => {
    if (!email) {
      setError("Enter your email above first, then click Forgot password.");
      return;
    }
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const err = await resetPassword(email);
      setNotice(
        err ?? "Password reset email sent — check your inbox to set a new password.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
        background: "var(--ink-0)",
        position: "relative",
        zIndex: 1,
      }}
    >
      <div
        className="panel"
        style={{ width: "100%", maxWidth: 400, padding: "30px 28px", zIndex: 1 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <Waveform size={24} color="var(--signal)" weight="fill" />
          <strong
            style={{
              fontSize: 17,
              letterSpacing: "0.08em",
              color: "var(--text-hi)",
            }}
          >
            OMNIVOICE
          </strong>
        </div>
        <p style={{ color: "var(--text-mid)", fontSize: 13.5, marginBottom: 18, lineHeight: 1.5 }}>
          Sign in so your orders stay private to your account.
        </p>

        <div role="tablist" style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          {(["signin", "signup"] as const).map((m) => (
            <button
              key={m}
              type="button"
              className="pill"
              aria-pressed={mode === m}
              onClick={() => {
                setMode(m);
                setError(null);
                setNotice(null);
              }}
              style={{ flex: 1, fontSize: "0.8rem" }}
            >
              {m === "signin" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
          <label className="auth-field" style={fieldWrap}>
            <EnvelopeSimple size={15} color="var(--text-low)" />
            <input
              className="auth-input"
              type="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <label className="auth-field" style={fieldWrap}>
            <LockKey size={15} color="var(--text-low)" />
            <input
              className="auth-input"
              type="password"
              required
              minLength={6}
              placeholder="Password (min 6 chars)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
            />
          </label>

          {error && (
            <p role="alert" style={{ color: "var(--danger)", fontSize: 12.5, margin: 0 }}>
              {error}
            </p>
          )}
          {notice && (
            <p style={{ color: "var(--ok)", fontSize: 12.5, margin: 0 }}>{notice}</p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="btn-primary"
            style={{
              marginTop: 4,
              width: "100%",
              borderRadius: "var(--r-md)",
              fontWeight: 600,
              fontSize: 14,
              cursor: busy ? "wait" : "pointer",
              opacity: busy ? 0.7 : 1,
            }}
          >
            {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Sign up"}
          </button>
        </form>

        {mode === "signin" && (
          <button
            type="button"
            onClick={() => void forgot()}
            disabled={busy}
            style={{
              marginTop: 10,
              border: "none",
              background: "transparent",
              color: "var(--signal)",
              fontSize: 12.5,
              cursor: "pointer",
              padding: 0,
            }}
          >
            Forgot password?
          </button>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "18px 0 14px" }}>
          <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
          <span className="label-mono">or</span>
          <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
        </div>

        <button
          type="button"
          onClick={continueAsGuest}
          style={{
            width: "100%",
            padding: "10px 0",
            borderRadius: "var(--r-md)",
            border: "1px dashed var(--line-strong)",
            background: "transparent",
            color: "var(--text-hi)",
            fontSize: 13,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 7,
          }}
        >
          <UserCircle size={16} weight="duotone" color="var(--signal)" />
          Continue as Guest
          <span style={{ color: "var(--text-low)" }}>(developer mode)</span>
        </button>
        <p
          style={{
            fontSize: 11.5,
            color: "var(--text-mid)",
            margin: "12px 0 0",
            lineHeight: 1.55,
          }}
        >
          Guests get a private, browser-local ID — orders stay isolated per browser and are not
          linked to an email. Sign in with any account (or create several) to keep each
          account's orders separate; use Sign out in the sidebar to switch.
        </p>
        {!authConfigured() && (
          <p className="label-mono" style={{ marginTop: 10 }}>
            Email login disabled — server env NEXT_PUBLIC_SUPABASE_* not set
          </p>
        )}
      </div>
    </div>
  );
}

const fieldWrap: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 9,
  border: "1px solid var(--line-strong)",
  borderRadius: "var(--r-md)",
  padding: "9px 12px",
  background: "var(--ink-1)",
};
