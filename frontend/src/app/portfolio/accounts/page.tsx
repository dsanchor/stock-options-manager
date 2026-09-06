import AccountsView from "@/components/AccountsView";

export const metadata = { title: "Accounts — Portfolio Income Lab" };

export default function AccountsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Broker Accounts</h1>
        <p className="text-sm text-text-muted">
          Manage your brokerage accounts. Assign movements to accounts during import or via reassignment.
        </p>
      </div>
      <AccountsView />
    </div>
  );
}
