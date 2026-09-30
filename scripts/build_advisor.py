"""Build site/advisor.html: private lineup, waiver, trade and steal advice for my teams.

Run pull_sleeper.py --nfl and build_dataset.py first.

Numbers used:
  proj     Sleeper's projection for this week, scored with the league's rules
  outlook  mean Sleeper projection over the next four weeks, byes excluded
  xpts     points the player's 2026 workload usually produces, from a regression of
           2024-25 weekly points on carries, targets and red-zone work
  avg      actual 2026 points per game
  value    rest-of-season weekly expectation (blend of outlook, xpts, avg)
  start    this-week expectation (blend of proj, xpts, avg)
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW, SITE = ROOT / "data" / "raw", ROOT / "site"
LEAGUES = {"1388319888270970880": "Armadillo", "1389735668468453376": "Robber"}
NO_BENCH = {"1389735668468453376"}
FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}, "REC_FLEX": {"WR", "TE"}}
SKILL = ("RB", "WR", "TE")
FEATURES = ["rush_att", "rec_tgt", "rush_rz_att", "rec_rz_tgt"]  # workload only; no yardage, which is an outcome


def load(rel):
    return json.loads((RAW / rel).read_text(encoding="utf-8"))


STATE = load("state.json")
SEASON, WEEK = STATE["season"], STATE["week"]
DONE = list(range(1, WEEK))
AHEAD = [w for w in range(WEEK, WEEK + 4) if (RAW / f"nfl/proj_{SEASON}_{w:02d}.json").exists()]
PLAYERS = load("players.json")
STATS = {w: load(f"nfl/stats_{SEASON}_{w:02d}.json") for w in DONE}
PROJ = {w: load(f"nfl/proj_{SEASON}_{w:02d}.json") for w in AHEAD}
SCHED = load(f"nfl/schedule_{SEASON}.json")
ME = load("user.json")["user_id"]


def opponent(team, week):
    for g in SCHED:
        if g["week"] == week and team in (g["home"], g["away"]):
            return ("v " + g["away"]) if g["home"] == team else ("@ " + g["home"])
    return "BYE"


def fit_usage_model():
    """Per-position least squares: weekly points from workload, on 2024-25 games."""
    rows = {p: ([], []) for p in SKILL}
    with open(ROOT / "data/processed/player_weeks.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["pos"] in SKILL and r["season"] != SEASON and float(r["off_snp"]) > 0:
                rows[r["pos"]][0].append([1.0] + [float(r[f]) for f in FEATURES])
                rows[r["pos"]][1].append(float(r["pts"]))
    return {p: np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0] for p, (x, y) in rows.items()}


MODEL = fit_usage_model()


def score(stats, scoring):
    return sum(stats.get(k, 0) * v for k, v in scoring.items())


def profile(pid, scoring):
    p = PLAYERS.get(pid, {})
    pos, team = p.get("position") or "", p.get("team") or ""
    games = [(w, STATS[w][pid]) for w in DONE if STATS[w].get(pid, {}).get("gp")]
    avg = round(sum(score(s, scoring) for _, s in games) / len(games), 1) if games else None
    xpts = None
    if pos in SKILL and games:
        preds = [float(MODEL[pos] @ np.array([1.0] + [s.get(f, 0) for f in FEATURES])) for _, s in games]
        xpts = round(sum(preds) / len(preds), 1)
    weekly = {w: round(score(PROJ[w].get(pid, {}), scoring), 1) for w in AHEAD}
    live = [v for w, v in weekly.items() if not team or opponent(team, w) != "BYE"]
    outlook = round(sum(live) / len(live), 1) if live else 0.0
    proj = weekly.get(WEEK, 0.0)
    if pos in SKILL:
        parts = [(outlook, .5), (xpts, .3), (avg, .2)]
        now = [(proj, .6), (xpts, .25), (avg, .15)]
    else:
        parts = [(outlook, .7), (avg, .3)]
        now = [(proj, .75), (avg, .25)]
    blend = lambda ps: round(sum(v * w for v, w in ps if v is not None) / sum(w for v, w in ps if v is not None), 1)
    snaps = [round(100 * s.get("off_snp", 0) / s["tm_off_snp"]) if s.get("tm_off_snp") else None
             for s in (STATS[w].get(pid, {}) for w in DONE)]
    opps = [int(s.get("rush_att", 0) + s.get("rec_tgt", 0)) for _, s in games]
    return {
        "id": pid, "name": p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid,
        "pos": pos, "nfl": team or "FA", "opp": opponent(team, WEEK) if team else "",
        "injury": p.get("injury_status") or "", "games": len(games), "avg": avg, "xpts": xpts,
        "heat": round(avg - xpts, 1) if avg is not None and xpts is not None else None,
        "proj": proj, "outlook": outlook, "value": blend(parts), "start": blend(now) if proj > 0 else 0.0,
        "snaps": snaps, "opps": round(sum(opps) / len(opps), 1) if opps else None,
        "byes": [w for w in AHEAD if team and opponent(team, w) == "BYE"],
    }


def best_lineup(players, slots, key):
    pool = sorted(players, key=lambda p: -p[key])
    used, lineup = set(), []
    order = [s for s in slots if s not in FLEX and s != "BN"] + [s for s in slots if s in FLEX]
    for slot in order:
        ok = FLEX.get(slot, {slot})
        pick = next((p for p in pool if p["id"] not in used and p["pos"] in ok), None)
        if pick:
            used.add(pick["id"])
        lineup.append({"slot": slot, "player": pick})
    return lineup


def advise(lid):
    d = f"leagues/{lid}"
    league = load(f"{d}/league.json")
    scoring, slots = league["scoring_settings"], league["roster_positions"]
    users = {u["user_id"]: u["display_name"] for u in load(f"{d}/users.json")}
    rosters = load(f"{d}/rosters.json")
    mine = next(r for r in rosters if r["owner_id"] == ME)
    owner = {pid: users.get(r["owner_id"], "?") for r in rosters for pid in (r["players"] or [])}
    prof = {}
    P = lambda pid: prof.setdefault(pid, profile(pid, scoring))

    reserve = set(mine.get("reserve") or [])
    roster = [dict(P(pid), where="IR" if pid in reserve else "Start" if pid in mine["starters"] else "Bench")
              for pid in mine["players"]]
    active = [p for p in roster if p["where"] != "IR"]
    ideal = best_lineup(active, slots, "start")
    ideal_ids = {x["player"]["id"] for x in ideal if x["player"]}
    sit = [p for p in active if p["where"] == "Start" and p["id"] not in ideal_ids]
    start = [p for p in active if p["where"] == "Bench" and p["id"] in ideal_ids]
    swaps = [{"start": s, "sit": next((o for o in sit if o["pos"] == s["pos"]), sit[i] if i < len(sit) else None)}
             for i, s in enumerate(sorted(start, key=lambda p: -p["start"]))]

    matchups = load(f"{d}/matchups_{WEEK:02d}.json")
    my_m = next(m for m in matchups if m["roster_id"] == mine["roster_id"])
    opp_m = next((m for m in matchups if m["matchup_id"] == my_m["matchup_id"] and m["roster_id"] != mine["roster_id"]), None)
    opp_r = next((r for r in rosters if opp_m and r["roster_id"] == opp_m["roster_id"]), None)
    opp_players = [P(pid) for pid in (opp_r["players"] if opp_r else [])]
    opp_best = best_lineup([p for p in opp_players if p["id"] not in set(opp_r.get("reserve") or [])], slots, "start") if opp_r else []
    total = lambda lu: round(sum(x["player"]["start"] for x in lu if x["player"]), 1)

    # Free agents: anyone unowned with a team. Gain is measured against my weakest player at the position.
    floor = {}
    for p in active:
        if p["pos"] not in floor or p["value"] < floor[p["pos"]]["value"]:
            floor[p["pos"]] = p
    free = []
    for pid, pl in PLAYERS.items():
        if pid in owner or not pl.get("team") or pl.get("position") not in ("QB", "RB", "WR", "TE", "K", "DEF"):
            continue
        if not any(PROJ[w].get(pid) for w in AHEAD) and not any(STATS[w].get(pid, {}).get("gp") for w in DONE):
            continue
        f = P(pid)
        base = floor.get(f["pos"])
        f = dict(f, drop=base["name"] if base else None, gain=round(f["value"] - base["value"], 1) if base else f["value"])
        free.append(f)
    adds = {pos: sorted((f for f in free if f["pos"] == pos), key=lambda f: -f["value"])[:6]
            for pos in ("RB", "WR", "TE", "QB", "K", "DEF")}

    others = [dict(P(pid), owner=o) for pid, o in owner.items() if pid not in mine["players"]]
    buy = sorted((p for p in others if p["heat"] is not None and p["heat"] <= -2.5 and p["games"] >= 2),
                 key=lambda p: -p["value"])[:10]
    sell = sorted((p for p in roster if p["heat"] is not None and p["heat"] >= 2.5), key=lambda p: -p["heat"])

    out = {
        "league": league["name"], "kind": LEAGUES[lid], "no_bench": lid in NO_BENCH,
        "record": f"{mine['settings']['wins']}-{mine['settings']['losses']}",
        "waiver": mine["settings"].get("waiver_position"),
        "opponent": users.get(opp_r["owner_id"]) if opp_r else None,
        "my_total": total(ideal), "opp_total": total(opp_best),
        "current_total": round(sum(p["start"] for p in active if p["where"] == "Start"), 1),
        "roster": sorted(roster, key=lambda p: (["Start", "Bench", "IR"].index(p["where"]), -p["start"])),
        "ideal": ideal, "swaps": swaps, "adds": adds, "buy": buy, "sell": sell,
    }
    if lid in NO_BENCH:
        need = {s: slots.count(s) for s in set(slots) if s != "BN"}
        have = {pos: sum(1 for p in active if p["pos"] == pos) for pos in ("QB", "RB", "WR", "TE", "K", "DEF")}
        out["shape"] = {"need": need, "have": have}
        out["steal_board"] = sorted(opp_players, key=lambda p: -p["value"])[:8]
        out["at_risk"] = sorted(active, key=lambda p: -p["value"])[:3]
    return out


def main():
    data = {"season": SEASON, "week": WEEK, "ahead": AHEAD,
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "teams": [advise(lid) for lid in LEAGUES]}
    html = (SITE / "advisor-template.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (SITE / "advisor.html").write_text(html.replace("/*__DATA__*/null", blob), encoding="utf-8")
    (SITE / "advisor.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for t in data["teams"]:
        print(f"\n== {t['league']} {t['record']} vs {t['opponent']}: best lineup {t['my_total']} (current {t['current_total']}) vs {t['opp_total']}")
        for s in t["swaps"]:
            print("  start", s["start"]["name"], s["start"]["start"], "over", s["sit"] and (s["sit"]["name"], s["sit"]["start"]))
        for p in t["roster"]:
            print(f"  {p['where']:<6}{p['pos']:<4}{p['name']:<22}{p['opp']:<7} proj {p['proj']:>5} xpts {str(p['xpts']):>5} avg {str(p['avg']):>5} start {p['start']:>5} value {p['value']:>5} heat {p['heat']} snaps {p['snaps']} opps {p['opps']} {p['injury']}")
        for pos, lst in t["adds"].items():
            print("  ADD", pos, [(f["name"], f["value"], f["gain"], f["drop"]) for f in lst[:4]])
        print("  BUY", [(p["name"], p["owner"], p["value"], p["heat"]) for p in t["buy"]])
        print("  SELL", [(p["name"], p["heat"]) for p in t["sell"]])
        if t["no_bench"]:
            print("  SHAPE", t["shape"])
            print("  STEAL", [(p["name"], p["value"]) for p in t["steal_board"]])


if __name__ == "__main__":
    main()
