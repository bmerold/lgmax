# Cross-device sync backend

A tiny Cloudflare Worker + KV store that lets your LeafGreen Maximizer progress
follow you between devices. It's free (Cloudflare's free tier covers this many
times over), anonymous, and stores no personal data — a random **sync key** is
the whole identity. The rest of the app keeps working with zero backend: sync is
purely additive, and the in-app **sync code** (copy/paste) works offline without
this Worker at all.

## What it stores

One JSON row per sync key: the checked-off stops (`done`), the play position
(`play`), and your starter + trading choice. Nothing else — no IP logging, no
accounts. See `worker.js` for the exact shape and the merge rule (done-lists are
unioned so two devices can't wipe each other's progress).

## Deploy it (one time, ~5 minutes)

You need a free Cloudflare account: <https://dash.cloudflare.com/sign-up>. Then,
from this `sync/` directory:

1. **Log in** — this opens a browser for you to authorize (run it yourself; it's
   an interactive login):

   ```
   npx wrangler login
   ```

2. **Create the KV namespace** and copy the id it prints:

   ```
   npx wrangler kv namespace create LGMAX
   ```

   Paste that id into `wrangler.toml` in place of `REPLACE_WITH_KV_NAMESPACE_ID`.

3. **Deploy:**

   ```
   npx wrangler deploy
   ```

   Wrangler prints the live URL, e.g. `https://lgmax-sync.<your-subdomain>.workers.dev`.

4. **Point the app at it.** Paste that URL into the app's Sync panel (the
   "server URL" field), or bake it in as the default: set `SYNC_ENDPOINT` near
   the top of `app_template.html` to the URL, then rebuild + deploy the app.

That's it. Open the Sync panel, note your key, and enter the same key on your
other device.

## Notes

- CORS is locked to the site origin (`https://lgmax.arcane-collectibles.com`)
  plus `localhost:8000` for local testing. If you host the app elsewhere, add
  that origin to `ALLOWED_ORIGINS` in `worker.js` and redeploy.
- To wipe a key server-side: `npx wrangler kv key delete --binding LGMAX "<key>"`.
- Free-tier KV limits (100k reads/day, 1k writes/day) are far beyond a single
  player's use; the app debounces writes so a run costs a handful of them.
