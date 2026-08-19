import { redirect } from "next/navigation";

// The catalog moved to a user-facing route. Old admin links keep working.
export default function OldProductsRedirect() {
  redirect("/catalog");
}