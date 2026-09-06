import { redirect } from "next/navigation";

/** Portfolio root → redirect to Holdings view. */
export default function PortfolioPage() {
  redirect("/portfolio/holdings");
}
