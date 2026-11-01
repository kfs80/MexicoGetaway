# MexicoGetaway
Tracks the best all-inclusive deals from Des Moines (DSM), by departure month.

## Layout
```
config.toml                      search, filters, alert rules, ratings
destinations.csv                 destinations to track (add a row to add one)
src/package_scraper.py           reads CheapCaribbean's price feed; builds deals, calendars, history, alerts
src/ratings.py                   optional Google Places guest-rating lookup
site/index.html                  the page (month tabs, date calendar, price-change markers)
site/data/*.json                 packages, history, ratings, alert state (committed by the workflow)
.github/workflows/refresh.yml    twice-daily scrape, alerts, commit, deploy
.github/workflows/deploy.yml     redeploys the site when you push to main
```

## Setup
1. Settings > Pages > Source: **GitHub Actions**.
2. Settings > Secrets and variables > Actions. All optional:
   - `ZENROWS_API_KEY` - only used if the price feed blocks GitHub's servers.
   - `GOOGLE_PLACES_API_KEY` - turns on guest ratings (Places API (New)).
   - `NTFY_TOPIC` - phone push alerts through the ntfy app (pick a hard-to-guess topic).
3. For email/mobile alerts via GitHub: Settings (your account) > Notifications, make sure email
   and the GitHub mobile app are on. Alerts arrive as a new issue assigned to and mentioning you.
4. Actions > Scrape and Deploy Resort Deals > Run workflow.

## Alerts
Edit the `[[alerts.rules]]` blocks in `config.toml`. A rule needs at least one price test
(`max_total`, `max_per_night`, or `min_pct_below`) and can narrow by `months`, `codes`,
`nights`, `min_stars`. A resort is alerted once per month unless its price drops another
`realert_drop_pct` percent.

## Known gaps
- Flight stops/layover/duration aren't in the price feed. Each card links to Google Flights instead.
- Beachfront isn't in the feed. Expedia and other sites have no adapter yet.
- The price unit (per person vs total) is assumed; see `[pricing]` in `config.toml`.
