import { timingSafeEqual } from 'node:crypto';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 120;

// Vercel Cron sends CRON_SECRET as Authorization: Bearer <secret>.
// Never allow an unauthenticated GET to trigger employer board requests.
export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET;
  const supplied = request.headers.get('authorization') || '';
  const expected = `Bearer ${secret || ''}`;
  const a = Buffer.from(supplied, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (!secret || secret.length < 32 || a.length !== b.length || !timingSafeEqual(a, b)) {
    return Response.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 105_000); // Free Render may be asleep.
  try {
    const response = await fetch('https://datapulse-api-slxd.onrender.com/api/v1/internal/scan', {
      method: 'POST',
      headers: { authorization: `Bearer ${secret}` },
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) {
      return Response.json({ error: 'Read-only scanner could not be triggered', backendStatus: response.status },
                           { status: 502 });
    }
    const result = await response.json();
    return Response.json({ status: 'forwarded', ...result }, { status: 202 });
  } catch {
    return Response.json({ error: 'Read-only scan trigger timed out or failed' }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
}
