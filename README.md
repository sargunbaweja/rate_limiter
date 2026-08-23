# Rate Limiter

A rate limiter built from scratch in Python/FastAPI, implementing four different rate-limiting
algorithms — each as both an in-memory version (for understanding the mechanism) and a
Redis-backed, production-ready version (for correctness across multiple app instances) — wired
into a FastAPI middleware that protects an endpoint with HTTP 429 responses.

## Problem statement

An API needs to stop any single client from making more than N requests in a given time period,
to protect backend resources from being overwhelmed and to prevent abuse (e.g. brute-forcing,
scraping, accidental retry storms). This is "rate limiting."

There's no single correct way to define "N requests per period" — different algorithms make
different trade-offs between **accuracy** (how precisely the limit is enforced), **memory/compute
cost**, and **whether bursts of saved-up traffic should be allowed**. This project implements four
of the standard approaches so the trade-offs can be compared directly, then productionizes one
of them behind a real HTTP API.

## Architecture

```
                        ┌─────────────────────────────┐
   Client Request  ───► │   RateLimiterMiddleware      │
                        │   (app/middleware.py)        │
                        │                               │
                        │  1. identify client            │
                        │     (X-API-Key header, or      │
                        │      X-Forwarded-For / IP)     │
                        │  2. limiter.is_allowed(key)    │
                        └───────────┬──────────┬────────┘
                                    │          │
                          allowed   │          │  rejected
                                    ▼          ▼
                        ┌───────────────┐   ┌─────────────────────┐
                        │  Route handler │   │  429 Too Many        │
                        │  (/search)     │   │  Requests +           │
                        └───────────────┘   │  Retry-After header  │
                                             └─────────────────────┘

                        One of four limiter implementations, chosen
                        at startup by RATE_LIMIT_ALGO env var:

                        ┌─────────────────┐   ┌──────────────────────┐
                        │ Fixed Window     │   │ Sliding Window Log    │
                        │ (INCR + EXPIRE)  │   │ (sorted set)           │
                        └─────────────────┘   └──────────────────────┘
                        ┌─────────────────────┐ ┌──────────────────┐
                        │ Sliding Window       │ │ Token Bucket      │
                        │ Counter (2 counters) │ │ (Lua script)      │
                        └─────────────────────┘ └──────────────────┘
                                    │
                                    ▼
                        ┌─────────────────────┐
                        │   Redis              │
                        │   (shared state       │
                        │    across all app     │
                        │    instances)          │
                        └─────────────────────┘
```

Every limiter's Redis backend is reached through a single shared connection pool
(`app/redis_client.py`), created once at FastAPI startup and closed at shutdown. All four
limiter classes expose the same interface — `async is_allowed(key: str) -> bool` — which is what
lets the middleware swap between algorithms with a single environment variable, with zero
branching logic of its own.

## Project layout

```
app/
  main.py                          FastAPI app, routes, startup/shutdown lifespan
  middleware.py                    Client identification + limiter dispatch + 429 handling
  config.py                        Env-var-driven settings (pydantic-settings)
  redis_client.py                  Async Redis connection pool lifecycle
  limiters/
    fixed_window.py                In-memory fixed window
    sliding_window.py               In-memory sliding window log
    sliding_window_counter.py       In-memory sliding window counter
    token_bucket.py                 In-memory token bucket
    redis_fixed_window.py           Redis: INCR + EXPIRE
    redis_sliding_window.py         Redis: sorted set (ZADD/ZCARD/ZREMRANGEBYSCORE)
    redis_sliding_window_counter.py Redis: two counters with weighted estimate
    redis_token_bucket.py           Redis: atomic Lua script
tests/
  load_test.py                     Fires a paced burst at a live server, compares all 4 algorithms
Dockerfile
requirements.txt
```

## The four algorithms

### 1. Fixed Window

Time is chopped into fixed-size blocks (e.g. every 1 second). Each client gets a counter that
resets whenever the current block changes.

- **Cost:** O(1) memory per client — a single counter.
- **Problem — boundary bursting:** a client can send up to N requests right before a window ends,
  then another N right after it starts — 2x the intended limit in a very short span, because the
  counter resets on a clock edge that has nothing to do with the client's actual behavior.
- **Redis version:** the window number is baked into the key name itself
  (`rl:fixed:{client}:{window}`), so a new window is automatically a brand-new key — no reset logic
  needed. `INCR` is atomic in Redis, so this is safe even with many app instances hitting the same
  key concurrently.

### 2. Sliding Window Log

Instead of fixed blocks, keep the exact timestamp of every request in a rolling log. On each
request, drop timestamps older than `window_seconds` ago, then check how many remain.

- **Fixes boundary bursting** because there's no reset instant — the window is always "the last N
  seconds relative to right now."
- **Cost:** O(limit) memory per client — up to `limit` timestamps stored, not just one number.
- **Redis version:** a sorted set per client, using the timestamp as score. `ZREMRANGEBYSCORE`
  trims old entries, `ZADD` records the new one, `ZCARD` counts what's left. To avoid a
  check-then-act race under concurrency, the entry is added first and only rolled back
  (`ZREM`) if that pushed the count over the limit.

### 3. Sliding Window Counter

An approximation of sliding window log at fixed-window cost: keep two counters (current window,
previous window), and estimate the sliding count as a weighted blend:

```
estimated_count = previous_window_count * (fraction of previous window still "in range") + current_window_count
```

- **Cost:** O(1) memory per client — two counters, not a full timestamp list.
- **Trade-off:** it's an *estimate*, not exact — it assumes requests were spread evenly across
  the previous window. If real traffic was clustered at one end of that window, the estimate is
  slightly off (bounded error, rarely significant in practice).
- **Redis version:** two keys (`rl:swc:{client}:{window}` and `...{window-1}`). The current
  window's key is kept alive for `2 * window_seconds` via TTL — long enough to still be readable
  as "previous" once the next window starts.

### 4. Token Bucket

Each client has a bucket holding up to `capacity` tokens, refilled at `refill_rate` tokens/second.
Each request costs 1 token; no token, no admission. Refill is computed lazily (`elapsed_time *
refill_rate`) rather than via a background job.

- **The one algorithm here that *intentionally* allows bursts:** an idle client accumulates
  tokens up to `capacity`, then can spend all of them in a single instant — a legitimate burst —
  before being throttled down to the steady `refill_rate`. Fixed/sliding window can never do this;
  they cap the rate at all times, with no concept of "saved up" allowance.
- **Cost:** O(1) memory per client, like fixed window.
- **Redis version — the interesting one:** a naive `GET → compute → SET` sequence has a
  read-modify-write race under concurrency (two requests can both read the same token count before
  either writes back, both get allowed off of one token). Fixed with a **Lua script** that Redis
  executes as a single, uninterruptible unit — read, refill, check, decrement, and write all happen
  atomically, with no gap for another request to interleave.

## Trade-off summary

| Algorithm | Memory/client | Accuracy | Boundary-safe | Allows bursts |
|---|---|---|---|---|
| Fixed Window | O(1) | Poor | No | No |
| Sliding Window Counter | O(1) | Close approximation | Yes | No |
| Sliding Window Log | O(limit) | Exact | Yes | No |
| Token Bucket | O(1) | N/A (different model) | Yes | Yes, by design |

## Design decisions

**Why sliding window log as the "exact" baseline, and sliding window counter as the cheap
approximation (not the other way around):** the log is the ground truth — it's the literal
definition of "how many requests happened in the trailing N seconds," derived directly from
data, with no assumptions. The counter is a deliberate trade: give up exactness (by assuming
uniform request distribution within the previous window) to buy back the O(1) memory profile of
fixed window. Building the log first makes clear exactly what the counter is approximating and
why its error is bounded — a counter can only be justified in terms of the log it's standing in
for.

**Why token bucket over leaky bucket, despite both being classic "bucket" algorithms:** they
solve different problems. Leaky bucket smooths *throughput* — requests queue up and drain at a
constant rate, so the algorithm controls how work is processed (used for traffic shaping /
smoothing output rate). Token bucket controls *admission* — it answers "should this request be
allowed in at all right now," with no queueing, which is what a rate limiter for an HTTP API
actually needs: instant accept/reject, not a request queue. Token bucket's burst allowance
(spend saved-up tokens instantly) also has no equivalent in leaky bucket, which enforces a flat
output rate regardless of how idle the client was beforehand.

**The atomicity fix for token bucket:** see the section above — a naive GET-then-SET has a
read-modify-write race that let two concurrent requests both spend what should have been the
bucket's last token. Fixed with a Lua script, which Redis runs as one atomic, uninterruptible
unit. This was chosen over `WATCH`/`MULTI`/`EXEC` optimistic locking because rate-limiting
traffic is exactly the high-contention case (many requests hitting the *same* client's key at
once) where optimistic retries would add latency; a Lua script never retries — it just runs once,
guaranteed.

**The reverse-proxy client-IP gotcha (found during Railway deployment):** the middleware's
client-identification fallback originally used `request.client.host`. Behind a reverse proxy
(Railway, Render, nginx, etc.) that value is the proxy's own internal address — not the real
caller — and on Railway specifically it changed on almost every single request. That meant every
request without an explicit `X-API-Key` was treated as a brand-new client with a fresh, full
bucket, silently defeating the rate limiter in production while unit-testing perfectly locally.
Root-caused by adding temporary debug endpoints that exercised the limiter directly and echoed
back what the middleware was actually seeing per-request, which showed `X-Forwarded-For` reliably
carrying the real client IP where `request.client.host` did not. Fixed by preferring
`X-Forwarded-For`'s first (leftmost) address, falling back to `request.client.host` only when no
proxy is present (e.g. local dev).

## Configuration

All config is via environment variables (`app/config.py`, `pydantic-settings`):

| Variable | Default | Used by |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | all Redis limiters |
| `RATE_LIMIT_ALGO` | `token_bucket` | selects `fixed_window` / `sliding_window` / `sliding_window_counter` / `token_bucket` |
| `RATE_LIMIT_LIMIT` | `5` | fixed/sliding window algorithms |
| `RATE_LIMIT_WINDOW_SECONDS` | `1` | fixed/sliding window algorithms |
| `RATE_LIMIT_CAPACITY` | `5` | token bucket |
| `RATE_LIMIT_REFILL_RATE` | `5.0` | token bucket |

Invalid `RATE_LIMIT_ALGO` values fail app startup immediately with a clear validation error,
rather than failing silently mid-request.

## Running locally

```bash
pip install -r requirements.txt
# requires a local Redis instance, e.g.: docker run -p 6379:6379 redis
uvicorn app.main:app --reload
```

```bash
curl http://localhost:8000/health
curl http://localhost:8000/search?q=hello
```

## Load testing

```bash
python tests/load_test.py
```

Launches its own `uvicorn` server once per algorithm (via `RATE_LIMIT_ALGO`), fires a paced burst
of 20 requests at ~8 req/s against each, and prints an allow/reject timeline plus a summary
comparison — this is where fixed window's boundary burst, sliding window's exact enforcement,
sliding window counter's smoothed approximation, and token bucket's burst tolerance are all
visible side by side.

## Deployment

Deployed via Docker to Railway, with a managed Redis add-on. See `Dockerfile` — note the `CMD`
uses shell form (`sh -c "... --port ${PORT:-8000}"`) specifically so it can read the `PORT`
environment variable Railway assigns dynamically; exec-form `CMD` can't do that shell
substitution.

Railway config:
1. Add a Redis service to the project.
2. Add this repo as a service — Railway auto-detects and builds the `Dockerfile`.
3. Set `REDIS_URL=${{Redis.REDIS_URL}}` (Railway's reference syntax, pointing at the Redis
   service) plus the `RATE_LIMIT_*` variables above.
4. Generate a public domain under the service's Networking settings.

## Picking real limit values

The numbers used in the load test (`limit=5, window=1s`) are deliberately small and round, so the
differences between algorithms are visible within a few seconds of output. Real production values
come from three inputs instead:

- **What your backend can survive** — if the protected endpoint hits a database or a third-party
  API, the limit should sit comfortably under whatever that downstream system can handle per
  second.
- **What normal usage looks like** — a limit below the size of a real, legitimate burst (e.g. a
  page load firing a handful of requests at once) will throttle innocent users, not just abuse.
- **What you're actually defending against** — a login endpoint (brute-force risk) wants a tight
  limit; a public search endpoint used continuously by a real UI wants something much more
  generous. There is no universal number, only a per-endpoint judgment call.

For token bucket specifically, `refill_rate` should be set to the desired long-run sustained rate,
and `capacity` set independently based on how large a legitimate burst should be tolerated.
