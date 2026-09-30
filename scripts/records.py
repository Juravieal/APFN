"""All-time record book for the two conferences.

Covers Eagle and Kangaroo (2021-2025, data/raw/history) and their 2026 successors
Armadillo and Grizzly (data/raw/leagues). Managers are keyed by Sleeper user_id, so a
team keeps its history when the manager renames it or the conference is renamed.

content/league-history.json adds what Sleeper cannot know: which accounts belong to the
same person, and the commissioner's list of overall league champions.
"""
import json
from collections import defaultdict
from pathlib import Path

import tournament

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CURRENT = {"1388319888270970880": "Armadillo", "1388320595925557248": "Grizzly"}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _seasons():
    out = [(x["conference"], int(x["season"]), RAW / "history" / x["league_id"])
           for x in _load(RAW / "history" / "index.json") or []]
    state = _load(RAW / "state.json")
    out += [(name, int(state["season"]), RAW / "leagues" / lid) for lid, name in CURRENT.items()]
    return sorted(out, key=lambda s: (s[1], s[0])), state


def past_tournaments(alias, names, official):
    """Replay the league tournament for every finished season."""
    out = []
    by_season = defaultdict(list)
    for x in _load(RAW / "history" / "index.json") or []:
        by_season[int(x["season"])].append(x)
    for season, leagues in sorted(by_season.items(), reverse=True):
        feed = []
        for x in leagues:
            d = RAW / "history" / x["league_id"]
            owner = {r["roster_id"]: alias.get(r["owner_id"], r["owner_id"]) for r in _load(d / "rosters.json")}
            feed.append((x["conference"], owner, {w: _load(d / f"matchups_{w:02d}.json") or [] for w in range(1, 19)}))
        teams = tournament.teams_from_matchups(feed)
        seeds = official.get(str(season))
        if seeds:  # the commissioner's own table decides who played whom
            key_of = {names.get(t["owner"], "").lower(): k for k, t in teams.items()}
            fixed = [key_of[n.lower()] for n in seeds if n.lower() in key_of]
            fixed += [k for k in tournament.standings(teams, tournament.SEED_WEEK) if k not in fixed]
            result = tournament.season(teams, 17, order=fixed)
        else:
            result = tournament.season(teams, 17)
        owner = lambda k: teams[k]["owner"]
        swap = lambda g: {**g, "hi": owner(g["hi"]), "lo": owner(g["lo"]),
                          "winner": owner(g["winner"]) if g["winner"] else None,
                          "loser": owner(g["loser"]) if g["loser"] else None}
        out.append({
            "season": season, "official_seeds": bool(seeds),
            "champion": owner(result["champion"]) if result["champion"] else None,
            "seeds": [{**s, "key": owner(s["key"]), "conf": teams[s["key"]]["conf"]} for s in result["seeds"]],
            "title": [{**r, "games": [swap(g) for g in r["games"]]} for r in result["title"]],
            "consolation": [{**r, "games": [swap(g) for g in r["games"]]} for r in result["consolation"]],
        })
    return out


def build_records():
    seasons, state = _seasons()
    names, games, champions, entries = {}, [], [], []
    playoffs = defaultdict(set)
    history = _load(ROOT / "content" / "league-history.json") or {}
    alias = {old: p["main_account"] for p in history.get("same_person", []) for old in p["earlier_accounts"]}
    league_champs = {int(y): name for y, name in history.get("league_champions", {}).items()}

    for conf, season, d in seasons:
        league = _load(d / "league.json")
        live = str(season) == state["season"] and league["status"] != "complete"
        for u in _load(d / "users.json"):
            names[u["user_id"]] = u["display_name"]  # seasons are in order, so the latest name wins
        owner = {r["roster_id"]: alias.get(r["owner_id"], r["owner_id"]) for r in _load(d / "rosters.json")}
        first_playoff = league["settings"].get("playoff_week_start") or 99
        last_week = state["week"] - 1 if live else 18
        for uid in owner.values():
            if uid:
                entries.append((uid, season, conf))

        for w in range(1, last_week + 1):
            rows = [m for m in _load(d / f"matchups_{w:02d}.json") or [] if m.get("matchup_id")]
            by = defaultdict(list)
            for m in rows:
                by[m["matchup_id"]].append(m)
            for pair in by.values():
                if len(pair) != 2 or not (pair[0]["points"] or pair[1]["points"]):
                    continue
                a, b = sorted(pair, key=lambda m: -m["points"])
                games.append({"season": season, "week": w, "conf": conf, "playoff": w >= first_playoff,
                              "w": owner.get(a["roster_id"]), "l": owner.get(b["roster_id"]),
                              "wp": a["points"], "lp": b["points"], "tie": a["points"] == b["points"]})

        # An in-progress season's bracket only holds projected seeds, so ignore it.
        bracket = [] if live else _load(d / "winners_bracket.json") or []
        for m in bracket:
            if m.get("r") == 1:
                for side in ("t1", "t2"):
                    if isinstance(m.get(side), int):
                        playoffs[owner.get(m[side])].add((season, conf))
        final = next((m for m in bracket if m.get("p") == 1), None)
        if final and final.get("w"):
            champions.append({"season": season, "conf": conf, "champion": owner.get(final["w"]),
                              "runner_up": owner.get(final["l"])})

    # Playoff-week games only count when both teams were in the winners bracket;
    # consolation games would otherwise pollute the records.
    in_bracket = lambda g: (g["season"], g["conf"]) in playoffs.get(g["w"], ()) and \
                           (g["season"], g["conf"]) in playoffs.get(g["l"], ())
    games = [g for g in games if g["w"] and g["l"] and (not g["playoff"] or in_bracket(g))]
    regular = [g for g in games if not g["playoff"]]

    stats = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "g": 0})
    h2h = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for g in games:
        i = 2 if g["tie"] else 0
        h2h[g["w"]][g["l"]][i] += 1
        h2h[g["l"]][g["w"]][2 if g["tie"] else 1] += 1
    for g in regular:
        for me, mine, theirs, res in ((g["w"], g["wp"], g["lp"], "w"), (g["l"], g["lp"], g["wp"], "l")):
            s = stats[me]
            s["t" if g["tie"] else res] += 1
            s["pf"] += mine
            s["pa"] += theirs
            s["g"] += 1

    for p in history.get("same_person", []):  # show the name the person is known by now
        if p["main_account"] in names:
            names[p["main_account"]] = p["known_as"]
    formerly = {p["main_account"]: [label.split(" (")[0] for label in p["earlier_accounts"].values()]
                for p in history.get("same_person", [])}
    current = {uid for uid, season, _ in entries if str(season) == state["season"]}
    managers = []
    for uid, s in stats.items():
        titles = [c for c in champions if c["champion"] == uid]
        league = sorted(y for y, who in league_champs.items() if who.lower() == names.get(uid, "").lower())
        managers.append({
            "id": uid, "name": names.get(uid, "Unknown"), "active": uid in current,
            "seasons": len({season for u, season, _ in entries if u == uid}),
            "w": s["w"], "l": s["l"], "t": s["t"], "pct": round((s["w"] + s["t"] / 2) / s["g"], 4),
            "ppg": round(s["pf"] / s["g"], 2), "pf": round(s["pf"], 2),
            "playoffs": len(playoffs.get(uid, ())), "finals": sum(1 for c in champions if uid in (c["champion"], c["runner_up"])),
            "titles": len(titles), "title_years": [f"{c['season']} {c['conf']}" for c in titles],
            "league_titles": league, "formerly": formerly.get(uid, []),
        })
    managers.sort(key=lambda m: (-len(m["league_titles"]), -m["titles"], -m["w"], -m["pct"]))

    def streaks(flag):
        best = []
        runs = defaultdict(lambda: [0, None])
        for g in sorted(regular, key=lambda g: (g["season"], g["week"])):
            if g["tie"]:
                continue
            for uid, hit in ((g["w"], flag == "w"), (g["l"], flag == "l")):
                run = runs[uid]
                if hit:
                    run[0] += 1
                    run[1] = run[1] or (g["season"], g["week"])
                    best.append({"id": uid, "n": run[0], "from": run[1], "to": (g["season"], g["week"])})
                else:
                    runs[uid] = [0, None]
        top = {}
        for b in sorted(best, key=lambda b: -b["n"]):  # keep each manager's longest run starting at a given point
            top.setdefault((b["id"], b["from"]), b)
        return sorted(top.values(), key=lambda b: -b["n"])[:5]

    line = lambda g: {"season": g["season"], "week": g["week"], "conf": g["conf"], "playoff": g["playoff"],
                      "w": g["w"], "l": g["l"], "wp": g["wp"], "lp": g["lp"]}
    sides = [dict(line(g), id=uid, pts=pts, opp=opp) for g in games
             for uid, pts, opp in ((g["w"], g["wp"], g["l"]), (g["l"], g["lp"], g["w"]))]
    decided = [g for g in games if not g["tie"]]
    season_tot = defaultdict(lambda: [0.0, 0, 0, 0])
    for g in regular:
        for uid, pts, won in ((g["w"], g["wp"], not g["tie"]), (g["l"], g["lp"], False)):
            t = season_tot[(uid, g["season"], g["conf"])]
            t[0] += pts
            t[1] += 1
            t[2] += won
    complete = {k: v for k, v in season_tot.items() if str(k[1]) != state["season"]}
    season_rows = [{"id": k[0], "season": k[1], "conf": k[2], "pf": round(v[0], 2), "w": v[2], "l": v[1] - v[2]}
                   for k, v in complete.items()]

    return {
        "names": names, "seasons": sorted({s for _, s, _ in seasons}), "games": len(games),
        "league_champions": league_champs,
        "tournaments": past_tournaments(alias, names, history.get("official_seeds", {})),
        "champions": sorted(champions, key=lambda c: (-c["season"], c["conf"])),
        "managers": managers,
        "high": sorted(sides, key=lambda s: -s["pts"])[:10],
        "low": sorted((s for s in sides if not s["playoff"]), key=lambda s: s["pts"])[:5],
        "blowouts": [line(g) for g in sorted(decided, key=lambda g: g["lp"] - g["wp"])[:5]],
        "closest": [line(g) for g in sorted(decided, key=lambda g: g["wp"] - g["lp"])[:5]],
        "shootouts": [line(g) for g in sorted(games, key=lambda g: -(g["wp"] + g["lp"]))[:5]],
        "win_streaks": streaks("w"), "loss_streaks": streaks("l"),
        "best_seasons": sorted(season_rows, key=lambda r: -r["pf"])[:5],
        "best_records": sorted(season_rows, key=lambda r: (-r["w"], -r["pf"]))[:5],
        "h2h": {a: {b: v for b, v in row.items()} for a, row in h2h.items() if a in current},
        "current": sorted(current, key=lambda u: names.get(u, "").lower()),
    }


if __name__ == "__main__":
    R = build_records()
    n = R["names"]
    print(R["games"], "games across", R["seasons"])
    for c in R["champions"]:
        print(c["season"], c["conf"], "champion", n.get(c["champion"]), "| runner-up", n.get(c["runner_up"]))
    for m in R["managers"][:14]:
        print(f"{m['name']:<18} {m['seasons']}y {m['w']}-{m['l']}-{m['t']} {m['pct']:.3f} ppg {m['ppg']} playoffs {m['playoffs']} finals {m['finals']} titles {m['title_years']} {'ACTIVE' if m['active'] else ''}")
    for k in ("high", "low"):
        print(k, [(n.get(s["id"]), s["pts"], s["season"], s["week"], "PO" if s["playoff"] else "") for s in R[k][:5]])
    for k in ("blowouts", "closest", "shootouts"):
        print(k, [(n.get(g["w"]), g["wp"], n.get(g["l"]), g["lp"], g["season"], g["week"]) for g in R[k][:3]])
    for k in ("win_streaks", "loss_streaks"):
        print(k, [(n.get(s["id"]), s["n"], s["from"], s["to"]) for s in R[k]])
    print("best seasons", [(n.get(r["id"]), r["season"], r["pf"], r["w"], r["l"]) for r in R["best_seasons"]])
    print("best records", [(n.get(r["id"]), r["season"], r["w"], r["l"]) for r in R["best_records"]])
