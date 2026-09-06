import AgentMarkdownView from "@/components/AgentMarkdownView";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export const dynamic = "force-dynamic";

export default async function ReportPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol: _rawSymbol } = await params;
  const symbol = decodeSymbolParam(_rawSymbol);
  return (
    <AgentMarkdownView
      symbol={symbol}
      endpoint={`/api/symbols/${encodeURIComponent(symbol)}/report`}
      resultKey="report"
      title={`📊 ${symbol} Report`}
      subtitle="Comprehensive position & situation analysis."
      emptyText="(Empty report)"
    />
  );
}
