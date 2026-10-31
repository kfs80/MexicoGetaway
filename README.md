# MexicoGetaway
Site to track the best all-inclusive deals in Mexico from Des Moines (DSM).

## Layout
```
config.toml                 search + filter settings
destinations.csv            destination codes (CUN, PVR)
requirements.txt
src/package_scraper.py      scrapes CheapCaribbean via ZenRows, merges results
site/index.html             static front end
site/data/packages.json     scraper output (committed, deployed with the site)
.github/workflows/refresh.yml   twice-daily scrape + commit + deploy
.github/workflows/deploy.yml    redeploys the site when you push to main
```

## Setup
1. Repo **Settings > Pages > Source: GitHub Actions**.
2. Repo **Settings > Secrets and variables > Actions**: add `ZENROWS_API_KEY`.
3. **Actions > Scrape and Deploy Resort Deals > Run workflow** to test.

## How a run works
Each run searches the next `request_budget_per_run` date/destination combos from a rotating
cursor, merges them into the existing `packages.json`, drops stale or past deals, and deploys.
A full sweep of the calendar takes several runs. Fewer weekdays or nights = fresher data.

## Known gaps
- The CheapCaribbean search URL in `build_search_url` and the `.hotel-card` selectors are
  unverified guesses. If a run finds 0 cards, download the `scraper-debug` artifact from the
  run and fix the selectors against the real HTML.
- User ratings, airline, and beachfront can't be read from search cards, so they are shown as
  unknown rather than guessed. The `min_user_rating` and `must_have_beachfront` filters need a
  detail-page lookup or a vetted resort whitelist.
- Expedia has no parser yet.
