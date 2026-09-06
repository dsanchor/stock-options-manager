import PortfolioHoldingsTable from "@/components/PortfolioHoldingsTable";

export const metadata = { title: "Holdings — Option Income Lab" };

export default function HoldingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Holdings</h1>
        <p className="text-sm text-text-muted">
          Derived share positions from committed ledger movements.
        </p>
      </div>
      <PortfolioHoldingsTable />
    </div>
  );
}
