"""Client for Jev (TypeSafe AI) through the Vercel AI Gateway.

The request format, prompt wording, batch size and retry policy are those of the accompanying paper (synergy/run_screen.mjs),
because the reported ranking performance was measured with exactly this format. Changing the wording or the batch size changes
the scores.

Only the review criteria and the title and abstract of each record are sent. Nothing else about the project leaves the machine.
"""
import json
import random
import threading
import time

import httpx

GATEWAY_URL = 'https://ai-gateway.vercel.sh/typesafe/v1/systemone'
MODEL = 'typesafe-ai/jev'
BATCH_SIZE = 10
QUESTION = ('Based on the title and abstract, should this record be advanced to full-text screening for this review? '
            'Give the probability that it meets the eligibility criteria.')
BATCH_INTRO = 'The following records are independent candidates retrieved by the search; judge each one on its own.'
USD_PER_M_INPUT_TOKENS = 0.042      # list price at the time of the paper; the gateway reports the charged cost per request
TOKENS_PER_RECORD = 455             # mean input tokens per record in 10-record batches in the paper


class AuthError(Exception):
    pass


def criteria_block(title, question, criteria):
    return (f'Systematic review title: {title.strip()}\nResearch question: {question.strip()}\n'
            f'Eligibility criteria reported by the review authors:\n{criteria.strip()}')


def record_text(rec):
    return (f"Title: {rec.get('title') or '(no title)'}\n"
            f"Abstract: {rec.get('abstract') or '(no abstract available; judge from the title)'}")


def build_request(crit, recs):
    """crit: the criteria block; recs: up to BATCH_SIZE dicts with title and abstract. Returns the JSON body."""
    if len(recs) == 1:
        state = crit + '\n\n' + record_text(recs[0])
        questions = {'r1': {'type': 'noul', 'instructions': QUESTION}}
    else:
        state = crit + '\n\n' + BATCH_INTRO + '\n\n' + '\n\n'.join(f'[Record R{i + 1}]\n{record_text(d)}' for i, d in enumerate(recs))
        questions = {f'r{i + 1}': {'type': 'noul', 'instructions': f'Consider only Record R{i + 1}. {QUESTION}'} for i in range(len(recs))}
    return {'model': MODEL, 'state': state, 'questions': questions}


def parse_answers(resp, n):
    """Probabilities for records R1..Rn (None where Jev gave no usable answer)."""
    out = []
    for i in range(n):
        v = (resp.get('answers') or {}).get(f'r{i + 1}', {})
        v = v.get('noul') if isinstance(v, dict) else None
        try:
            v = float(v)
            out.append(v if 0.0 <= v <= 1.0 else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def estimate_cost(n_records):
    return n_records * TOKENS_PER_RECORD * USD_PER_M_INPUT_TOKENS / 1e6


class Pacer:
    """Global request slots shared by all worker threads; 429 responses push the next slot back by retry-after plus jitter."""

    def __init__(self, gap_ms=700):
        self.gap = gap_ms / 1000; self.next = 0.0; self.lock = threading.Lock(); self.throttled = 0

    def wait(self):
        with self.lock:
            now = time.monotonic(); at = max(now, self.next); self.next = at + self.gap
        if at > now:
            time.sleep(at - now)

    def backoff(self, retry_after):
        w = (retry_after or 5) + random.random() * 1.5
        with self.lock:
            self.throttled += 1; self.next = max(self.next, time.monotonic() + w)
        time.sleep(w)


class JevClient:
    """endpoint: the gateway URL (own key) or the trial proxy URL (trial token). The body is identical in both cases."""

    def __init__(self, token, endpoint=GATEWAY_URL, gap_ms=700, timeout=120, max_retries=40, transport=None):
        if not token:
            raise AuthError('No API key given.')
        self.endpoint = endpoint; self.pacer = Pacer(gap_ms); self.max_retries = max_retries
        self.http = httpx.Client(timeout=timeout, transport=transport,
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        self.spent = 0.0; self.lock = threading.Lock()

    def close(self):
        self.http.close()

    def send(self, body, cancel=None):
        """Sends one request with retries. Returns dict(status, response, error, tokens, cost, attempts)."""
        attempt = 0; net_err = 0
        while True:
            attempt += 1
            if cancel is not None and cancel.is_set():
                return {'status': None, 'response': None, 'error': 'cancelled', 'tokens': None, 'cost': 0.0, 'attempts': attempt}
            self.pacer.wait()
            try:
                r = self.http.post(self.endpoint, content=json.dumps(body))
            except httpx.HTTPError as e:
                net_err += 1
                if net_err <= 8:
                    time.sleep(1.5 * net_err); continue
                return {'status': None, 'response': None, 'error': f'network: {type(e).__name__}', 'tokens': None, 'cost': 0.0, 'attempts': attempt}
            if r.status_code in (401, 403):
                raise AuthError(f'HTTP {r.status_code}: {r.text[:200]}')
            if r.status_code == 429 or r.status_code >= 500:
                if attempt > self.max_retries:
                    return {'status': r.status_code, 'response': None, 'error': f'HTTP {r.status_code} after {attempt} attempts', 'tokens': None, 'cost': 0.0, 'attempts': attempt}
                try:
                    ra = float(r.headers.get('retry-after') or 0)
                except ValueError:
                    ra = 0
                self.pacer.backoff(ra); continue
            if r.status_code != 200:
                return {'status': r.status_code, 'response': None, 'error': f'HTTP {r.status_code}: {r.text[:200]}', 'tokens': None, 'cost': 0.0, 'attempts': attempt}
            j = r.json()
            cost = float(((j.get('provider_metadata') or {}).get('gateway') or {}).get('marketCost') or 0)
            with self.lock:
                self.spent += cost
            return {'status': 200, 'response': j, 'error': None, 'tokens': (j.get('usage') or {}).get('input_tokens'), 'cost': cost, 'attempts': attempt}
