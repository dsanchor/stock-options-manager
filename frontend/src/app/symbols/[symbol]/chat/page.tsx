import SymbolChat from "@/components/SymbolChat";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export const dynamic = "force-dynamic";

export default async function ChatPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol: _rawSymbol } = await params;
  const symbol = decodeSymbolParam(_rawSymbol);
  return <SymbolChat symbol={symbol} />;
}
