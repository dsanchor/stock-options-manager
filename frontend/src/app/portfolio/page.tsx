import { redirect } from "next/navigation";

/** /portfolio → unified Symbols page (HTTP 308) */
export default function PortfolioPage() {
  redirect("/symbols");
}
