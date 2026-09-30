"""Turn data/raw into flat CSVs in data/processed/.

player_weeks.csv   one row per player per NFL week, scored with APFN league scoring
player_seasons.csv season totals, per-game averages, positional rank
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
CONF_LEAGUE = "1388319888270970880"   # Armadillo; Grizzly uses identical scoring
ROBBER_LEAGUE = "1389735668468453376"
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
USAGE = ["gp", "off_snp", "tm_off_snp", "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int",
         "rush_att", "rush_yd", "rush_td", "rec_tgt", "rec", "rec_yd", "rec_td", "rec_rz_tgt",
         "rush_rz_att", "rec_air_yd", "fum_lost", "fgm", "fga", "xpm", "sack", "int", "fum_rec",
         "def_td", "pts_allow"]


def load(rel):
    return json.loads((RAW / rel).read_text(encoding="utf-8"))


def score(stats, scoring):
    return round(sum(stats.get(k, 0) * v for k, v in scoring.items()), 2)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    players = load("players.json")
    conf = load(f"leagues/{CONF_LEAGUE}/league.json")["scoring_settings"]
    robber = load(f"leagues/{ROBBER_LEAGUE}/league.json")["scoring_settings"]

    rows = []
    for f in sorted((RAW / "nfl").glob("stats_*.json")):
        _, season, week = f.stem.split("_")
        stats = json.loads(f.read_text(encoding="utf-8"))
        proj = load(f"nfl/proj_{season}_{week}.json")
        for pid, s in stats.items():
            p = players.get(pid)
            if not p or p.get("position") not in POSITIONS or not s.get("gp"):
                continue
            row = {"season": int(season), "week": int(week), "player_id": pid,
                   "name": p.get("full_name") or f"{p.get('first_name')} {p.get('last_name')}",
                   "pos": p["position"], "team_now": p.get("team") or "",
                   "pts": score(s, conf), "pts_robber": score(s, robber),
                   "proj": score(proj.get(pid, {}), conf)}
            row.update({k: s.get(k, 0) for k in USAGE})
            rows.append(row)

    with open(OUT / "player_weeks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    agg = defaultdict(lambda: defaultdict(float))
    meta = {}
    for r in rows:
        key = (r["season"], r["player_id"])
        meta[key] = (r["name"], r["pos"], r["team_now"])
        a = agg[key]
        a["games"] += 1
        for k in ["pts", "pts_robber", "proj"] + USAGE[1:]:
            a[k] += r[k]
    seasons = []
    for (season, pid), a in agg.items():
        name, pos, team = meta[(season, pid)]
        g = a["games"]
        seasons.append({"season": season, "player_id": pid, "name": name, "pos": pos, "team_now": team,
                        "games": int(g), "pts": round(a["pts"], 2), "ppg": round(a["pts"] / g, 2),
                        "proj_ppg": round(a["proj"] / g, 2),
                        "snap_pct": round(100 * a["off_snp"] / a["tm_off_snp"], 1) if a["tm_off_snp"] else 0,
                        **{f"{k}_pg": round(a[k] / g, 2) for k in
                           ["pass_att", "pass_yd", "pass_td", "rush_att", "rush_yd", "rec_tgt", "rec", "rec_yd",
                            "rec_rz_tgt", "rush_rz_att"]},
                        "td": int(a["pass_td"] + a["rush_td"] + a["rec_td"])})
    for season in {s["season"] for s in seasons}:
        for pos in POSITIONS:
            grp = sorted((s for s in seasons if s["season"] == season and s["pos"] == pos), key=lambda s: -s["pts"])
            for i, s in enumerate(grp, 1):
                s["pos_rank"] = i
    seasons.sort(key=lambda s: (s["season"], s["pos"], s["pos_rank"]))
    with open(OUT / "player_seasons.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(seasons[0]))
        w.writeheader()
        w.writerows(seasons)
    print(len(rows), "player-weeks,", len(seasons), "player-seasons")


if __name__ == "__main__":
    main()
