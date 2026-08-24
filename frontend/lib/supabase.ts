import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Browser-side Supabase client for email/password auth.
 *
 * Uses the PUBLIC anon key only — never the service/secret key, which must
 * stay server-side. When the NEXT_PUBLIC_* vars are absent (local dev before
 * setup, or a deployer who skips auth), `getSupabase()` returns null and the
 * app falls back to guest-only mode automatically.
 */

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

let client: SupabaseClient | null = null;

export function authConfigured(): boolean {
  return Boolean(url && anonKey);
}

export function getSupabase(): SupabaseClient | null {
  if (!url || !anonKey) return null;
  if (!client) {
    client = createClient(url, anonKey, {
      auth: { persistSession: true, autoRefreshToken: true },
    });
  }
  return client;
}

/** localStorage key holding this browser's guest identity. */
export const GUEST_ID_KEY = "voicesell.guest_id";

/** Random per-browser id so incognito/private windows never share orders. */
export function getOrCreateGuestId(): string {
  if (typeof window === "undefined") return "";
  let id = window.localStorage.getItem(GUEST_ID_KEY);
  if (!id) {
    id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `g-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    window.localStorage.setItem(GUEST_ID_KEY, id);
  }
  return id;
}
