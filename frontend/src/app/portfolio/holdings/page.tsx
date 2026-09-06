import PortfolioHoldingsTable from "@/components/PortfolioHoldingsTable";

export const metadata = { title: "Portfolio — Portfolio Income Lab" };

export default function HoldingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
        <p className="text-sm text-text-muted">
          Derived share positions from committed ledger movements.
        </p>
      </div>
      <PortfolioHoldingsTable />
    </div>
  );
}
