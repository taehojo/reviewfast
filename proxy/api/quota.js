// GET /api/quota: remaining free records for the signed-in GitHub user.
import { githubUser, quota } from './_lib.js';

export default async function handler(req, res) {
  const user = await githubUser(req);
  if (!user) return res.status(401).json({ error: { message: 'Sign in with GitHub first.' } });
  return res.status(200).json(await quota(user.id));
}
