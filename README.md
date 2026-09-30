# APFN League Hub

A companion site for the APFN fantasy football leagues on Sleeper: the Armadillo and Grizzly
conferences and the Robber League. It reads Sleeper's public API and adds what Sleeper does
not show.

- **Hub** (`index.html`): weekly recap, power rankings across all 20 conference teams,
  conference standings, the Robber League robbery ledger, and an all-time record book back
  to 2021.
- **Live scoreboard** (`live.html`): game-day scores for all three leagues, refreshed from
  Sleeper every 60 seconds in the browser.

## How it updates

`.github/workflows/update.yml` runs every day at 10:15 UTC, and on every push to `main`.
It downloads the data, builds both pages and publishes them to GitHub Pages. Nothing in
`data/` or the built pages is stored in the repository.

To run it by hand: Actions tab, "Update site", "Run workflow".

## Weekly recap

The recap text lives in `content/recaps/<season>-week-<NN>.json`. If the file for the last
finished week is missing:

- with an `ANTHROPIC_API_KEY` repository secret set, the workflow writes it with Claude and
  commits it;
- without one, the Recap tab shows final scores only.

An existing file is never overwritten, so a recap can be written or corrected by hand.

## Running locally

```bash
python scripts/update.py
```

That pulls everything, rebuilds the pages in `site/`, and also builds `site/advisor.html`,
a private lineup and waiver page that is never committed or published (it needs `numpy`).
Serve the folder to view the pages:

```bash
python -m http.server 8765 --directory site
```

## Layout

| Path | What it is |
|---|---|
| `scripts/pull_sleeper.py` | Downloads league, NFL and history data into `data/raw` |
| `scripts/build_site.py` | Builds the hub from `site/template.html` |
| `scripts/records.py` | All-time record book calculations |
| `scripts/build_live.py` | Builds the live scoreboard from `site/live-template.html` |
| `scripts/write_recap.py` | Optional Claude-written recap |
| `scripts/build_dataset.py`, `scripts/build_advisor.py` | Local-only player dataset and advice page |
| `docs/sleeper-api-reference.md` | Notes on the Sleeper API, documented and undocumented |

Sleeper's API is free for non-commercial use. This project is not affiliated with Sleeper.
