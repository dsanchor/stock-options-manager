import PortfolioMovementsTable from "@/components/PortfolioMovementsTable";

export const metadata = { title: "Movements — Portfolio Income Lab" };

export default function MovementsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Movements</h1>
        <p className="text-sm text-text-muted">
          All ledger entries — purchases, sales, and dividends.
        </p>
      </div>
      <PortfolioMovementsTable />
    </div>
  );
}
