import ImportChat from "@/components/ImportChat";

export const metadata = { title: "Import — Portfolio Income Lab" };

export default function ImportPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Import</h1>
        <p className="text-sm text-text-muted">
          Upload a CSV file or paste its content to import dividends, purchases, or sales.
        </p>
      </div>
      <ImportChat />
    </div>
  );
}
