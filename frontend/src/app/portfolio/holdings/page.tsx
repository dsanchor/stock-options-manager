import { redirect } from "next/navigation";

/** /portfolio/holdings → unified Symbols page (HTTP 308) */
export default function HoldingsPage() {
  redirect("/symbols");
}
