// Shared helpers for the free-trial proxy (Vercel Functions, Node runtime).
// The proxy holds the operator's AI Gateway key, identifies users by their GitHub token, and enforces per-user and monthly limits.
// It stores only counters (per GitHub user id and per month). It never stores or logs request bodies or responses.

const KV_URL = process.env.KV_REST_API_URL;            // Upstash Redis REST endpoint (Vercel Marketplace)
const KV_TOKEN = process.env.KV_REST_API_TOKEN;
export const LIMITS = {
  recordsPerUser: Number(process.env.TRIAL_RECORDS_PER_USER || 5000),
  monthlyUsd: Number(process.env.TRIAL_MONTHLY_USD || 20),
  requestsPerMinute: Number(process.env.TRIAL_REQUESTS_PER_MINUTE || 60),
  minAccountAgeDays: Number(process.env.TRIAL_MIN_ACCOUNT_AGE_DAYS || 30),
  maxStateChars: 60000,
  maxQuestions: 10,
};

export async function kv(...cmd) {
  const r = await fetch(KV_URL, { method: 'POST', headers: { Authorization: `Bearer ${KV_TOKEN}` }, body: JSON.stringify(cmd) });
  if (!r.ok) throw new Error(`kv ${r.status}`);
  return (await r.json()).result;
}

export const month = () => new Date().toISOString().slice(0, 7);

export async function githubUser(req) {
  const auth = req.headers.authorization || '';
  if (!auth.startsWith('Bearer ')) return null;
  const r = await fetch('https://api.github.com/user', { headers: { Authorization: auth, 'User-Agent': 'reviewfast-trial', Accept: 'application/vnd.github+json' } });
  if (!r.ok) return null;
  const u = await r.json();
  return { id: String(u.id), ageDays: (Date.now() - Date.parse(u.created_at)) / 86400000 };
}

export async function quota(uid) {
  const used = Number((await kv('GET', `trial:user:${uid}`)) || 0);
  const spent = Number((await kv('GET', `trial:usd:${month()}`)) || 0);
  return { used, remaining: Math.max(0, LIMITS.recordsPerUser - used), limit: LIMITS.recordsPerUser, monthly_budget_left: spent < LIMITS.monthlyUsd };
}
