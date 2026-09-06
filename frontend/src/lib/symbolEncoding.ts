/**
 * Symbol encoding helpers.
 *
 * Next.js 16 route-handler params may arrive as the raw URL segment —
 * meaning a MIC:TICKER like "XNYS:AAD" can appear as "XNYS%3AAAD" if the
 * browser (or a Link href) percent-encoded the colon before navigation.
 * Calling `encodeURIComponent` on an already-encoded value produces
 * double-encoding (%253A instead of %3A) which the backend rejects with 404.
 *
 * Encoding boundary contract:
 *  1. `decodeSymbolParam` — called ONCE at the entry point (BFF route
 *     handler or server-component getData) to guarantee a plain decoded value.
 *  2. `encodeURIComponent` — called ONCE on the decoded value when building
 *     the upstream backend path.
 *
 * Colons in URL path segments are RFC 3986 §3.3 pchars and do not require
 * percent-encoding, so raw security_id values (e.g. "XNYS:AAD") are valid
 * as Next.js `href` strings. Prefer `symbolHref` over manual concatenation
 * to avoid inadvertent pre-encoding.
 */

/**
 * Normalise a dynamic route param that may be either decoded ("XNYS:AAD")
 * or once-encoded ("XNYS%3AAAD"). Returns the decoded form in both cases.
 *
 * Safe: MIC:TICKER identifiers only contain alphanumeric, "_", "-", ".", ":"
 * so a single decodeURIComponent is idempotent and cannot corrupt the value.
 */
export function decodeSymbolParam(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/**
 * Build a /symbols/{id} client-side href.
 * Colons in security_id are valid pchars; passing them un-encoded avoids
 * the double-encoding that occurs when encodeURIComponent is used in hrefs
 * and Next.js then delivers the param without decoding it.
 */
export function symbolHref(securityIdOrTicker: string): string {
  return `/symbols/${securityIdOrTicker}`;
}
