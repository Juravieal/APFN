"""Download raw Sleeper data into data/raw/ as JSON.

Usage: python scripts/pull_sleeper.py [--nfl] [--players]
  (no flags)  league data for every league the user is in this season
  --nfl       weekly NFL stats/projections/schedule for STAT_SEASONS
  --players   the full player database (large; once a day at most)
  --history   past seasons of the Eagle and Kangaroo conferences and the Guillotine league
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

USERNAME = "Juravieal"
STAT_SEASONS = [2024, 2025, 2026]
API = "https://api.sleeper.app/v1"
API2 = "https://api.sleeper.com"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
# APFN leagues the user is not in (yet), pulled by ID alongside the user's own.
EXTRA_LEAGUES = ["1389743156127334400"]  # APFN Guillotine League S3


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "league-os/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def save(rel, data):
    path = RAW / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def pull(rel, url):
    time.sleep(0.1)
    return save(rel, get(url))


def pull_leagues(state):
    season, week = state["season"], state["week"]
    user = pull("user.json", f"{API}/user/{USERNAME}")
    leagues = pull(f"leagues_{season}.json", f"{API}/user/{user['user_id']}/leagues/nfl/{season}")
    mine = {lg["league_id"] for lg in leagues}
    leagues += [get(f"{API}/league/{lid}") for lid in EXTRA_LEAGUES if lid not in mine]
    for lg in leagues:
        lid = lg["league_id"]
        d = f"leagues/{lid}"
        save(f"{d}/league.json", lg)
        for name in ("rosters", "users", "traded_picks", "winners_bracket", "losers_bracket", "drafts"):
            pull(f"{d}/{name}.json", f"{API}/league/{lid}/{name}")
        for w in range(1, week + 1):
            pull(f"{d}/matchups_{w:02d}.json", f"{API}/league/{lid}/matchups/{w}")
            pull(f"{d}/transactions_{w:02d}.json", f"{API}/league/{lid}/transactions/{w}")
        for dr in get(f"{API}/league/{lid}/drafts"):
            did = dr["draft_id"]
            pull(f"{d}/draft_{did}.json", f"{API}/draft/{did}")
            pull(f"{d}/draft_{did}_picks.json", f"{API}/draft/{did}/picks")
        print("league", lid, lg["name"])


def pull_nfl(state):
    cur_season, cur_week = int(state["season"]), state["week"]
    for season in STAT_SEASONS:
        pull(f"nfl/schedule_{season}.json", f"{API2}/schedule/nfl/regular/{season}")
        last = 18 if season < cur_season else cur_week
        for w in range(1, last + 1):
            pull(f"nfl/stats_{season}_{w:02d}.json", f"{API}/stats/nfl/regular/{season}/{w}")
            pull(f"nfl/proj_{season}_{w:02d}.json", f"{API}/projections/nfl/regular/{season}/{w}")
        if season == cur_season:  # look-ahead projections for the advisor
            for w in range(cur_week + 1, min(18, cur_week + 3) + 1):
                pull(f"nfl/proj_{season}_{w:02d}.json", f"{API}/projections/nfl/regular/{season}/{w}")
        print("nfl", season, "weeks 1 -", last)


# Most recent season of each conference's lineage; earlier seasons are found via previous_league_id.
HISTORY_ROOTS = {"Eagle": "1226271583442587648", "Kangaroo": "1257064572448157696"}
# Past Guillotine seasons, newest first. S3 is not linked to S2 in Sleeper, so the lineage starts at S2.
GUILLOTINE_ROOT = "1237196507828998144"


def pull_history():
    """Completed past seasons of both conferences (they do not change, so this is a one-off)."""
    index = []
    for conf, lid in HISTORY_ROOTS.items():
        while lid:
            lg = pull(f"history/{lid}/league.json", f"{API}/league/{lid}")
            for name in ("rosters", "users", "winners_bracket", "losers_bracket"):
                pull(f"history/{lid}/{name}.json", f"{API}/league/{lid}/{name}")
            for w in range(1, 19):
                pull(f"history/{lid}/matchups_{w:02d}.json", f"{API}/league/{lid}/matchups/{w}")
            index.append({"conference": conf, "season": lg["season"], "league_id": lid})
            print("history", conf, lg["season"])
            lid = lg.get("previous_league_id")
    save("history/index.json", index)

    index, lid = [], GUILLOTINE_ROOT
    while lid:
        lg = pull(f"history/{lid}/league.json", f"{API}/league/{lid}")
        for name in ("rosters", "users"):
            pull(f"history/{lid}/{name}.json", f"{API}/league/{lid}/{name}")
        for w in range(1, 19):
            pull(f"history/{lid}/matchups_{w:02d}.json", f"{API}/league/{lid}/matchups/{w}")
        index.append({"season": lg["season"], "name": lg["name"].strip(), "league_id": lid})
        print("history Guillotine", lg["season"])
        lid = lg.get("previous_league_id")
    save("history/guillotine_index.json", index)


if __name__ == "__main__":
    state = pull("state.json", f"{API}/state/nfl")
    pull_leagues(state)
    if "--nfl" in sys.argv:
        pull_nfl(state)
    if "--history" in sys.argv:
        pull_history()
    if "--players" in sys.argv:
        pull("players.json", f"{API}/players/nfl")
        print("players")
