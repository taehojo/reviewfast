// POST /api/score: forwards one Jev screening request (the same body jev-screen sends to the gateway) with the operator's key.
import { LIMITS, githubUser, kv, month } from './_lib.js';

const GATEWAY = 'https://ai-gateway.vercel.sh/typesafe/v1/systemone';

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: { message: 'POST only' } });
  const user = await githubUser(req);
  if (!user) return res.status(401).json({ error: { message: 'Sign in with GitHub first.' } });
  if (user.ageDays < LIMITS.minAccountAgeDays) return res.status(403).json({ error: { message: `The free trial needs a GitHub account older than ${LIMITS.minAccountAgeDays} days. Use your own AI Gateway key instead.` } });

  const b = req.body || {};
  const qs = b.questions && typeof b.questions === 'object' ? Object.values(b.questions) : [];
  if (b.model !== 'typesafe-ai/jev' || typeof b.state !== 'string' || b.state.length > LIMITS.maxStateChars || !qs.length || qs.length > LIMITS.maxQuestions
      || qs.some((q) => q?.type !== 'noul' || typeof q.instructions !== 'string' || q.instructions.length > 500)) {
    return res.status(400).json({ error: { message: 'Only jev-screen screening requests are accepted.' } });
  }
  // per-minute rate, monthly budget, per-user record quota (reserved before the call, released if the call fails)
  const minuteKey = `trial:rpm:${user.id}:${Math.floor(Date.now() / 60000)}`;
  if (Number(await kv('INCR', minuteKey)) > LIMITS.requestsPerMinute) { await kv('EXPIRE', minuteKey, 120); res.setHeader('retry-after', '30'); return res.status(429).json({ error: { message: 'Too many requests.' } }); }
  await kv('EXPIRE', minuteKey, 120);
  if (Number((await kv('GET', `trial:usd:${month()}`)) || 0) >= LIMITS.monthlyUsd) return res.status(402).json({ error: { message: 'The free trial budget for this month is used up. Use your own AI Gateway key.' } });
  const userKey = `trial:user:${user.id}`;
  if (Number(await kv('INCRBY', userKey, qs.length)) > LIMITS.recordsPerUser) {
    await kv('DECRBY', userKey, qs.length);
    return res.status(402).json({ error: { message: 'Your free records are used up. Use your own AI Gateway key.' } });
  }
  let r;
  try {
    r = await fetch(GATEWAY, { method: 'POST', headers: { Authorization: `Bearer ${process.env.AI_GATEWAY_API_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: b.model, state: b.state, questions: b.questions }), signal: AbortSignal.timeout(110000) });
  } catch (e) {
    await kv('DECRBY', userKey, qs.length);
    return res.status(502).json({ error: { message: 'Upstream request failed.' } });
  }
  if (r.status !== 200) {
    await kv('DECRBY', userKey, qs.length);
    if (r.headers.get('retry-after')) res.setHeader('retry-after', r.headers.get('retry-after'));
    return res.status(r.status === 429 ? 429 : 502).json({ error: { message: `Upstream HTTP ${r.status}` } });
  }
  const j = await r.json();
  const cost = Number(j.provider_metadata?.gateway?.marketCost || 0);
  if (cost) await kv('INCRBYFLOAT', `trial:usd:${month()}`, cost);
  return res.status(200).json({ answers: j.answers, usage: j.usage, provider_metadata: { gateway: { marketCost: j.provider_metadata?.gateway?.marketCost } } });
}
