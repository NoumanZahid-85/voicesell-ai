"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { LoginScreen, useIdentity } from "@/components/auth";

/**
 * Dedicated auth route: /login (and /signup?mode=signup via the same form).
 * After a successful sign-in — or an immediate guest assignment when auth
 * isn't configured — send the user to `?next=` or the console.
 */
function LoginFlow() {
  const { identity, ready } = useIdentity();
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/chat";
  const initialMode = params.get("mode") === "signup" ? "signup" : "signin";

  useEffect(() => {
    if (ready && identity) {
      router.replace(next);
    }
  }, [ready, identity, router, next]);

  if (ready && identity) return null; // redirecting
  return <LoginScreen initialMode={initialMode} />;
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginFlow />
    </Suspense>
  );
}
