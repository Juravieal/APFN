"""Build site/live.html, the game-day scoreboard.

The page itself fetches scores from Sleeper in the browser every 60 seconds. This script
only bakes in what does not change during a week: league ids, a slim player-name table
and the all-time high score to chase. Rebuild it weekly so new players have names.
"""
import json
from pathlib import Path

import build_site
from records import build_records

ROOT = Path(__file__).resolve().parent.parent
RAW, SITE = ROOT / "data" / "raw", ROOT / "site"
LEAGUES = [("1388319888270970880", "Armadillo", False), ("1388320595925557248", "Grizzly", False),
           ("1389735668468453376", "Robber", True)]
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def this_weeks_league_games():
    """Cross-conference games for the current week: tournament games in weeks 15-17,
    otherwise the scheduled league games, already in game-of-the-week order."""
    week = build_site.WEEK_NOW
    league = build_site.league_season({lid: build_site.load_league(lid) for lid in build_site.CONFERENCES})
    games = [{"label": g["label"], "a": g["hi"], "b": g["lo"], "a_tag": f"#{g['hi_seed']} seed", "b_tag": f"#{g['lo_seed']} seed"}
             for r in league["title"] + league["consolation"] if r["week"] == week for g in r["games"]]
    if not games and league["league_games"]["week"] == week:
        games = [{"label": "Game of the week" if i == 0 else "League game", "a": g["a"], "b": g["b"],
                  "a_tag": f"#{g['a_rank']}", "b_tag": f"#{g['b_rank']}"}
                 for i, g in enumerate(league["league_games"]["games"])]
    return games


def main():
    players = json.loads((RAW / "players.json").read_text(encoding="utf-8"))
    slim = {}
    for pid, p in players.items():
        if p.get("position") in POSITIONS and p.get("team"):
            name = p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")]))
            slim[pid] = [name, p["position"], p["team"]]
    rec = build_records()
    top = rec["high"][0]
    record = {"pts": top["pts"], "name": rec["names"].get(top["id"], "Unknown"), "season": top["season"], "week": top["week"]}
    cfg = {"leagues": [{"id": lid, "name": name, "robber": robber, "record": None if robber else record}
                       for lid, name, robber in LEAGUES], "players": slim, "league_games": this_weeks_league_games()}
    html = (SITE / "live-template.html").read_text(encoding="utf-8")
    blob = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (SITE / "live.html").write_text(html.replace("/*__DATA__*/null", blob), encoding="utf-8")
    print("built site/live.html with", len(slim), "players; record to beat", record)


if __name__ == "__main__":
    main()
