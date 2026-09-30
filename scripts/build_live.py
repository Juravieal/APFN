"""Build site/live.html, the game-day scoreboard.

The page itself fetches scores from Sleeper in the browser every 60 seconds. This script
only bakes in what does not change during a week: league ids, a slim player-name table
and the all-time high score to chase. Rebuild it weekly so new players have names.
"""
import json
from pathlib import Path

from records import build_records

ROOT = Path(__file__).resolve().parent.parent
RAW, SITE = ROOT / "data" / "raw", ROOT / "site"
LEAGUES = [("1388319888270970880", "Armadillo", False), ("1388320595925557248", "Grizzly", False),
           ("1389735668468453376", "Robber", True)]
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


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
                       for lid, name, robber in LEAGUES], "players": slim}
    html = (SITE / "live-template.html").read_text(encoding="utf-8")
    blob = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (SITE / "live.html").write_text(html.replace("/*__DATA__*/null", blob), encoding="utf-8")
    print("built site/live.html with", len(slim), "players; record to beat", record)


if __name__ == "__main__":
    main()
