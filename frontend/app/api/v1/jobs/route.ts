/**
 * Same-origin public jobs endpoint.
 * Browsers must never depend on cross-origin Render CORS configuration.
 * The upstream is fixed; user-controlled query strings cannot redirect requests.
 */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 120;

const UPSTREAM = 'https://datapulse-api-slxd.onrender.com/api/v1/jobs';

export async function GET(request: Request) {
  const incoming = new URL(request.url);
  const url = new URL(UPSTREAM);
  url.search = incoming.search;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 50_000);
    try {
      const upstream = await fetch(url, {
        method: 'GET',
        headers: { accept: 'application/json' },
        cache: 'no-store',
        signal: controller.signal,
      });
      if (upstream.status >= 500 && attempt === 0) {
        console.warn('DataPulse jobs upstream returned', upstream.status, 'retrying once');
        continue;
      }
      const contentType = upstream.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        console.error('DataPulse jobs upstream returned unexpected content type', contentType);
        return Response.json({ detail: 'Jobs service returned an unexpected response. Please retry.' }, { status: 502 });
      }
      return new Response(await upstream.text(), {
        status: upstream.status,
        headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
      });
    } catch (error) {
      console.error('DataPulse jobs upstream request failed', attempt + 1, String(error));
      if (attempt === 1) {
        return Response.json({ detail: 'Jobs service is temporarily unreachable. Please try again shortly.' }, { status: 503 });
      }
    } finally {
      clearTimeout(timeout);
    }
  }
  return Response.json({ detail: 'Jobs service is temporarily unavailable.' }, { status: 503 });
}
