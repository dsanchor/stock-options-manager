import BestOptionsView from "@/components/BestOptionsView";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export const dynamic = "force-dynamic";

export default async function BestOptionsPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol: _rawSymbol } = await params;
  const symbol = decodeSymbolParam(_rawSymbol);
  return <BestOptionsView symbol={symbol} />;
}
