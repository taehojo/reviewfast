# Free-trial proxy (optional)

This small Vercel project lets ReviewFast users score a limited number of records with the operator's AI Gateway key, so that they can try the tool before creating their own key. It is **not deployed**. Before deploying it, the operator must confirm that the TypeSafe AI and Vercel terms allow a third-party application to relay requests on its key, and must accept responsibility for the records that pass through it.

## What it does

- Identifies users by a GitHub OAuth token obtained by the desktop app with the GitHub device flow. Accounts younger than 30 days are refused.
- Accepts only ReviewFast screening requests: model `typesafe-ai/jev`, at most 10 `noul` questions, at most 60,000 characters of state.
- Limits: 5,000 records per GitHub account, 60 requests per minute per account, and a monthly budget of US$20 across all users. All three are environment variables.
- Stores only counters in Upstash Redis (per GitHub user id, per minute and per month). It does not store or log request bodies, records or scores.

## Deploy

1. Create a GitHub OAuth App with device flow enabled. Note its client id.
2. Create a Vercel project from this folder, add an Upstash Redis store (sets `KV_REST_API_URL` and `KV_REST_API_TOKEN`), and set `AI_GATEWAY_API_KEY` to the operator key. Optional: `TRIAL_RECORDS_PER_USER`, `TRIAL_MONTHLY_USD`, `TRIAL_REQUESTS_PER_MINUTE`, `TRIAL_MIN_ACCOUNT_AGE_DAYS`.
3. Turn off request body logging in the Vercel project, and set a spending limit on the AI Gateway key.
4. Users enable the trial by setting `REVIEWFAST_TRIAL_URL` (the deployment URL) and `REVIEWFAST_GITHUB_CLIENT_ID` before `reviewfast serve`; a release can set defaults for both.
