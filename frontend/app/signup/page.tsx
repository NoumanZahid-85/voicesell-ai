"use client";

import { Suspense, useEffect } from "react";
import { useRouter } from "next/navigation";
import { LoginScreen, useIdentity } from "@/components/auth";

/** /signup — the same auth card, opened on the Create-account tab.
 *  Redirects into the console once a session exists. */
function SignupFlow() {
  const { identity, ready } = useIdentity();
  const router = useRouter();

  useEffect(() => {
    if (ready && identity) router.replace("/chat");
  }, [ready, identity, router]);

  if (ready && identity) return null;
  return <LoginScreen initialMode="signup" />;
}

export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupFlow />
    </Suspense>
  );
}
