"""Build site/index.html, the league hub, from data/raw (run pull_sleeper.py first).

Computes the cross-conference power rankings and the Robber League ledger,
then injects the result into site/template.html as JSON.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import tournament
from records import build_records

ROOT = Path(__file__).resolve().parent.parent
RAW, SITE = ROOT / "data" / "raw", ROOT / "site"
CONFERENCES = {"1388319888270970880": "Armadillo", "1388320595925557248": "Grizzly"}
ROBBER = "1389735668468453376"
GUILLOTINE = "1389743156127334400"
GUILLOTINE_PENALTY = 50  # FAAB, for the week's lowest score among teams that carry on
FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}, "REC_FLEX": {"WR", "TE"}}
STEAL_FOLLOWUP_MS = 15 * 60 * 1000


def load(rel):
    return json.loads((RAW / rel).read_text(encoding="utf-8"))


STATE = load("state.json")
PLAYERS = load("players.json")
WEEK_NOW = STATE["week"]
DONE = list(range(1, WEEK_NOW))  # weeks with final scores


def pname(pid):
    p = PLAYERS.get(pid, {})
    return p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid


def pinfo(pid):
    p = PLAYERS.get(pid, {})
    return {"name": pname(pid), "pos": p.get("position") or "", "nfl": p.get("team") or "FA"}


def optimal(points, slots):
    """Best possible lineup score from a roster's player points."""
    pool = sorted(points.items(), key=lambda kv: -kv[1])
    used, total = set(), 0.0
    fixed = [s for s in slots if s not in FLEX and s != "BN"]
    flex = [s for s in slots if s in FLEX]
    for slot, ok in [(s, {s}) for s in fixed] + [(s, FLEX[s]) for s in flex]:
        for pid, pts in pool:
            if pid not in used and PLAYERS.get(pid, {}).get("position") in ok:
                used.add(pid)
                total += pts
                break
    return round(total, 2)


def load_league(lid):
    d = f"leagues/{lid}"
    league = load(f"{d}/league.json")
    users = {u["user_id"]: u for u in load(f"{d}/users.json")}
    rosters = load(f"{d}/rosters.json")
    teams = {}
    for r in rosters:
        u = users.get(r["owner_id"], {})
        name = u.get("display_name") or f"Roster {r['roster_id']}"
        s = r["settings"]
        teams[r["roster_id"]] = {
            "key": f"{lid}:{r['roster_id']}", "rid": r["roster_id"], "manager": name,
            "team": (u.get("metadata") or {}).get("team_name") or f"Team {name}",
            "avatar": (u.get("metadata") or {}).get("avatar")
                      or (f"https://sleepercdn.com/avatars/thumbs/{u['avatar']}" if u.get("avatar") else None),
            "w": s["wins"], "l": s["losses"], "t": s["ties"],
            "pf": s["fpts"] + s.get("fpts_decimal", 0) / 100,
            "pa": s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100,
            "waiver": s.get("waiver_position"), "weeks": [],
        }
    matchups = {}
    for w in range(1, WEEK_NOW + 1):
        ms = load(f"{d}/matchups_{w:02d}.json")
        matchups[w] = {m["roster_id"]: m for m in ms}
        if w not in DONE:
            continue
        for m in ms:
            opp = next((o for o in ms if o["matchup_id"] == m["matchup_id"] and o["roster_id"] != m["roster_id"]), None)
            pts = m["points"]
            opts = opp["points"] if opp else None
            teams[m["roster_id"]]["weeks"].append({
                "week": w, "pts": pts, "opp": teams[opp["roster_id"]]["key"] if opp else None, "opp_pts": opts,
                "res": None if opp is None else "W" if pts > opts else "L" if pts < opts else "T",
                "best": optimal(m.get("players_points") or {}, league["roster_positions"]),
            })
    return {"league": league, "rosters": rosters, "teams": teams, "matchups": matchups, "dir": d}


def add_all_play(teams):
    """All-play record, expected wins and luck across the given pool of teams."""
    n = len(teams)
    for t in teams:
        t.update(ap_w=0, ap_l=0, ap_t=0, xw=0.0)
    for w in DONE:
        scores = [(t, next(x["pts"] for x in t["weeks"] if x["week"] == w)) for t in teams]
        for t, pts in scores:
            wins = sum(1 for _, o in scores if o < pts)
            ties = sum(1 for _, o in scores if o == pts) - 1
            t["ap_w"] += wins
            t["ap_t"] += ties
            t["ap_l"] += n - 1 - wins - ties
            t["xw"] += (wins + ties / 2) / (n - 1)
    for t in teams:
        g = len(t["weeks"]) or 1
        total = t["ap_w"] + t["ap_l"] + t["ap_t"] or 1
        actual = sum({"W": 1, "T": 0.5}.get(x["res"], 0) for x in t["weeks"])
        t["ap_pct"] = round((t["ap_w"] + t["ap_t"] / 2) / total, 4)
        t["luck"] = round(actual - t["xw"], 2)
        t["xw"] = round(t["xw"], 2)
        t["ppg"] = round(sum(x["pts"] for x in t["weeks"]) / g, 2)
        best = sum(x["best"] for x in t["weeks"])
        t["eff"] = round(100 * sum(x["pts"] for x in t["weeks"]) / best, 1) if best else None
        t["left"] = round(best - sum(x["pts"] for x in t["weeks"]), 1)
    teams.sort(key=lambda t: (-t["ap_pct"], -t["ppg"]))
    for i, t in enumerate(teams, 1):
        t["rank"] = i


def upcoming(lg):
    seen, out = set(), []
    for m in lg["matchups"][WEEK_NOW].values():
        if m["matchup_id"] is None or m["matchup_id"] in seen:
            continue
        seen.add(m["matchup_id"])
        pair = [x["roster_id"] for x in lg["matchups"][WEEK_NOW].values() if x["matchup_id"] == m["matchup_id"]]
        out.append([lg["teams"][r]["key"] for r in pair])
    return out


def awards(teams, week):
    rows = [(t, next(x for x in t["weeks"] if x["week"] == week)) for t in teams]
    games = [(t, x) for t, x in rows if x["res"] == "W"]
    hi = max(rows, key=lambda r: r[1]["pts"])
    lo = min(rows, key=lambda r: r[1]["pts"])
    close = min(games, key=lambda r: r[1]["pts"] - r[1]["opp_pts"])
    blow = max(games, key=lambda r: r[1]["pts"] - r[1]["opp_pts"])
    bench = max(rows, key=lambda r: r[1]["best"] - r[1]["pts"])
    unlucky = max((r for r in rows if r[1]["res"] == "L"), key=lambda r: r[1]["pts"])
    return [
        {"label": "High score", "team": hi[0]["key"], "value": f"{hi[1]['pts']:.2f}"},
        {"label": "Low score", "team": lo[0]["key"], "value": f"{lo[1]['pts']:.2f}"},
        {"label": "Closest win", "team": close[0]["key"], "value": f"by {close[1]['pts'] - close[1]['opp_pts']:.2f}",
         "vs": close[1]["opp"]},
        {"label": "Biggest blowout", "team": blow[0]["key"], "value": f"by {blow[1]['pts'] - blow[1]['opp_pts']:.2f}",
         "vs": blow[1]["opp"]},
        {"label": "Best score in a loss", "team": unlucky[0]["key"], "value": f"{unlucky[1]['pts']:.2f}",
         "vs": unlucky[1]["opp"]},
        {"label": "Most points left on the bench", "team": bench[0]["key"],
         "value": f"{bench[1]['best'] - bench[1]['pts']:.1f}"},
    ]


def game_lines(lg, week):
    """One line per game: winner, loser, scores and each side's top starter."""
    out, seen = [], set()
    for m in lg["matchups"][week].values():
        if m["matchup_id"] is None or m["matchup_id"] in seen:
            continue
        seen.add(m["matchup_id"])
        pair = sorted((x for x in lg["matchups"][week].values() if x["matchup_id"] == m["matchup_id"]),
                      key=lambda x: -x["points"])
        sides = []
        for x in pair:
            pts = x.get("players_points") or {}
            star = max(x["starters"], key=lambda p: pts.get(p, 0))
            sides.append({"team": lg["teams"][x["roster_id"]]["key"], "pts": x["points"],
                          "star": pname(star), "star_pts": pts.get(star, 0)})
        out.append(sides)
    return sorted(out, key=lambda g: g[0]["pts"] - g[1]["pts"])


def preseason_ranks(confs):
    """Week 1 has no standings, so conference teams are ranked by last season's regular-season
    points for (earlier accounts folded in; a manager new to the league ranks last)."""
    history = json.loads((ROOT / "content" / "league-history.json").read_text(encoding="utf-8"))
    alias = {old: p["main_account"] for p in history.get("same_person", []) for old in p["earlier_accounts"]}
    prev = str(int(STATE["season"]) - 1)
    pf = {}
    for x in load("history/index.json"):
        if x["season"] == prev:
            for r in load(f"history/{x['league_id']}/rosters.json"):
                st = r["settings"]
                pf[alias.get(r["owner_id"], r["owner_id"])] = st["fpts"] + st.get("fpts_decimal", 0) / 100
    pts = {f"{lid}:{r['roster_id']}": pf.get(r["owner_id"], -1) for lid, lg in confs.items() for r in lg["rosters"]}
    out = {k: {"lr": i + 1, "pre": True} for i, k in enumerate(sorted(pts, key=lambda k: -pts[k]))}
    for lid in confs:
        for i, k in enumerate(sorted((k for k in pts if k.startswith(f"{lid}:")), key=lambda k: -pts[k])):
            out[k]["cr"] = i + 1
    return out


def ranks_entering(week, confs, rob):
    """Each team's league rank, conference rank and Robber League rank going into `week`
    (standings through the week before, wins then points for), as the league's own tables show them.
    Week 1 uses preseason ranks for the conferences and has no Robber League rank."""
    through = week - 1
    if through == 0:
        return preseason_ranks(confs)
    feed = lambda lid, lg: (lid, {r["roster_id"]: r["owner_id"] for r in lg["rosters"]},
                            {w: load(f"leagues/{lid}/matchups_{w:02d}.json") for w in range(1, through + 1)})
    teams = tournament.teams_from_matchups([feed(lid, lg) for lid, lg in confs.items()])
    tournament.add_league_games(teams, {w: g for w, g in load_schedule(confs).items() if w <= through})
    wl = lambda t: "{w}-{l}".format(**tournament.record(t, through)) + ("-{t}".format(**tournament.record(t, through)) if tournament.record(t, through)["t"] else "")
    out = {k: {"lr": i + 1, "lrec": wl(teams[k])} for i, k in enumerate(tournament.standings(teams, through))}
    for lid in confs:
        own = {k: {"points": t["points"], "games": [g for g in t["games"] if g[2] == "conference"]}
               for k, t in teams.items() if k.startswith(f"{lid}:")}
        for i, k in enumerate(tournament.standings(own, through)):
            out[k].update(cr=i + 1, crec=wl(own[k]))
    rteams = tournament.teams_from_matchups([feed(ROBBER, rob)])
    for i, k in enumerate(tournament.standings(rteams, through)):
        out[k] = {"lr": i + 1, "lrec": wl(rteams[k])}
    return out


def preview(confs, rob):
    """This week's matchups for every league, with each team's ranks and record going into the
    week, most important game first. Cross-conference league games are listed separately because
    they are not on Sleeper; before they start, the first week of them is shown as a look ahead."""
    week = WEEK_NOW
    ranks = ranks_entering(week, confs, rob)
    rank_sorted = lambda pairs: sorted(
        ([dict(key=k, **ranks.get(k, {})) for k in sorted(p, key=lambda k: ranks.get(k, {}).get("lr", 99))] for p in pairs),
        key=lambda g: (g[0].get("lr", 99) + g[1].get("lr", 99), g[0].get("lr", 99)))
    schedule = load_schedule(confs)
    league_week = next((w for w in sorted(schedule) if w >= week), None)
    out = {
        "week": week,
        "league_week": league_week,
        "league": rank_sorted(schedule.get(league_week, [])) if league_week else [],
        "conferences": {CONFERENCES[lid]: rank_sorted(tuple(p) for p in upcoming(lg)) for lid, lg in confs.items()},
        "robber": rank_sorted(tuple(p) for p in upcoming(rob)),
    }
    # Game of the week: the top league game when league games are played this week, otherwise the
    # top conference game across both conferences.
    pool = out["league"] if league_week == week else [g for gs in out["conferences"].values() for g in gs]
    if pool:
        min(pool, key=lambda g: (g[0]["lr"] + g[1]["lr"], g[0]["lr"]))[0]["gotw"] = True
    # Each conference's top game is its Conference Spotlight, unless it is the game of the week.
    for games in out["conferences"].values():
        if games and not games[0][0].get("gotw"):
            games[0][0]["spot"] = True
    return out


def league_lines(confs, week):
    """Cross-conference league games for a week, scored with each team's points from its own
    conference matchup that week."""
    def side(key):
        lid, rid = key.split(":")
        m = confs[lid]["matchups"][week][int(rid)]
        pts = m.get("players_points") or {}
        star = max(m["starters"], key=lambda p: pts.get(p, 0))
        return {"team": key, "pts": m["points"], "star": pname(star), "star_pts": pts.get(star, 0)}
    return [[side(a), side(b)] for a, b in load_schedule(confs).get(week, [])]


def recap(leagues, confs, rob, week):
    """Written recap for a finished week, if content/recaps has one, plus the game lines
    with each team's ranks going into the week, most important game first.

    Order (the league's rule): in league-play weeks, League play leads with the game of the week,
    then each conference opens with its Conference Spotlight (its top-ranked game). Before league
    play, the conference holding the game of the week goes first and the other conference's top
    game is its Spotlight. The Robber League goes last and has neither."""
    path = ROOT / "content" / "recaps" / f"{STATE['season']}-week-{week:02d}.json"
    prose = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    ranks = ranks_entering(week, confs, rob)
    out = []
    league_games = league_lines(confs, week)
    sections = ([("League", None)] if league_games else []) + leagues
    for name, lg in sections:
        games = league_games if lg is None else game_lines(lg, week)
        for g in games:
            for side in g:
                side.update(ranks.get(side["team"], {}))
            g.sort(key=lambda s: s.get("lr", 99))  # higher-ranked team first, as in the commissioner's tables
        games.sort(key=lambda g: (g[0].get("lr", 99) + g[1].get("lr", 99), g[0].get("lr", 99)))
        out.append({"name": name, **prose.get(name, {"headline": f"Week {week} results", "paragraphs": []}),
                    "games": games, "robber": name == "Robber"})
    # Game of the week: the most important league game in league-play weeks, otherwise the most
    # important conference game. Each conference's top game is otherwise its Spotlight.
    ranked = lambda g: "lr" in g[0] and "lr" in g[1]
    pool = [g for L in out if not L["robber"] and (L["name"] == "League" or not league_games)
            for g in L["games"] if ranked(g)]
    if pool:
        min(pool, key=lambda g: (g[0]["lr"] + g[1]["lr"], g[0]["lr"]))[0]["gotw"] = True
    for L in out:
        top = L["games"][0] if L["games"] else None
        if L["name"] not in ("League", "Robber") and top and ranked(top) and not top[0].get("gotw"):
            top[0]["spot"] = True
    # League play first; before it starts, the conference holding the game of the week goes first.
    out.sort(key=lambda L: (L["name"] != "League", L["robber"], not any(g[0].get("gotw") for g in L["games"])))
    return {"week": week, "leagues": out}


def robber_section(lg):
    d, teams, scoring = lg["dir"], lg["teams"], lg["league"]["scoring_settings"]
    key = lambda rid: teams[rid]["key"]

    def nfl_pts(pid, week):
        s = load(f"nfl/stats_{STATE['season']}_{week:02d}.json").get(pid, {})
        return round(sum(s.get(k, 0) * v for k, v in scoring.items()), 2)

    tx = []
    for w in range(1, WEEK_NOW + 1):
        tx += [t for t in load(f"{d}/transactions_{w:02d}.json")
               if t["type"] == "commissioner" and t["status"] == "complete"]
    tx.sort(key=lambda t: t["created"])

    def beat(winner, loser, week):
        return any(x["week"] == week and x["res"] == "W" and x["opp"] == key(loser) for x in teams[winner]["weeks"])

    steals = []
    for t in tx:
        adds, drops = t["adds"] or {}, t["drops"] or {}
        for pid in set(adds) & set(drops):
            thief, victim = adds[pid], drops[pid]
            if thief == victim:
                continue
            week = next((w for w in reversed(DONE) if w <= t["leg"] and beat(thief, victim, w)), t["leg"])
            # The thief has no bench, so the commissioner drops a player to make room.
            released = []
            for f in tx:
                if not 0 <= f["created"] - t["created"] <= STEAL_FOLLOWUP_MS or f is t:
                    continue
                fa, fd = f["adds"] or {}, f["drops"] or {}
                if set(fa) & set(fd):
                    continue
                released += [p for p, r in fd.items() if r == thief]
                released = [p for p in released if not (p in fa and fa[p] == thief)]
            held = [w for w in DONE if w > week and pid in (lg["matchups"][w][thief].get("players") or [])]
            after = [w for w in DONE if w > week]
            loot = round(sum((lg["matchups"][w][thief]["players_points"] or {}).get(pid, 0) for w in held), 2)
            started = sum(1 for w in held if pid in (lg["matchups"][w][thief].get("starters") or []))
            gave = released[0] if released else None
            gave_pts = round(sum(nfl_pts(gave, w) for w in held), 2) if gave else 0.0
            game = next(x for x in teams[thief]["weeks"] if x["week"] == week)
            steals.append({
                "week": week, "thief": key(thief), "victim": key(victim), "player": pinfo(pid), "pid": pid,
                "score": [game["pts"], game["opp_pts"]], "released": pinfo(gave) if gave else None,
                "held_weeks": len(held), "started": started, "loot": loot, "released_pts": gave_pts,
                "net": round(loot - gave_pts, 2), "lost": round(sum(nfl_pts(pid, w) for w in after), 2),
            })
    holder = {p: key(r["roster_id"]) for r in lg["rosters"] for p in (r["players"] or [])}
    for s in steals:
        s["holder"] = holder.get(s["pid"])

    # Completed games with no steal recorded yet.
    ppg = {}
    for r in lg["rosters"]:
        for pid in r["players"] or []:
            played = [nfl_pts(pid, w) for w in DONE
                      if load(f"nfl/stats_{STATE['season']}_{w:02d}.json").get(pid, {}).get("gp")]
            ppg[pid] = (round(sum(played) / len(played), 1) if played else 0.0, len(played))
    owed = []
    for w in DONE:
        for t in teams.values():
            g = next(x for x in t["weeks"] if x["week"] == w)
            if g["res"] != "W" or any(s["week"] == w and s["thief"] == t["key"] for s in steals):
                continue
            loser = next(r for r in lg["rosters"] if key(r["roster_id"]) == g["opp"])
            targets = sorted(loser["players"] or [], key=lambda p: -ppg[p][0])[:5]
            owed.append({"week": w, "winner": t["key"], "loser": g["opp"], "score": [g["pts"], g["opp_pts"]],
                         "targets": [{**pinfo(p), "ppg": ppg[p][0], "games": ppg[p][1]} for p in targets]})

    playing = {x for g in load(f"nfl/schedule_{STATE['season']}.json") if g["week"] == WEEK_NOW
               for x in (g["home"], g["away"])}
    bench = []
    for r in lg["rosters"]:
        for pid in set(r["players"] or []) - set(r["starters"] or []) - set(r.get("reserve") or []):
            p = PLAYERS.get(pid, {})
            bye = bool(p.get("team")) and p["team"] not in playing
            bench.append({"team": key(r["roster_id"]), **pinfo(pid), "injury": p.get("injury_status") or "",
                          "bye": bye, "flag": not bye and not p.get("injury_status")})

    summary = {}
    for t in teams.values():
        made = [s for s in steals if s["thief"] == t["key"]]
        lost = [s for s in steals if s["victim"] == t["key"]]
        summary[t["key"]] = {"steals": len(made), "robbed": len(lost),
                             "gained": round(sum(s["net"] for s in made), 1),
                             "lost": round(sum(s["lost"] for s in lost), 1)}
        summary[t["key"]]["net"] = round(summary[t["key"]]["gained"] - summary[t["key"]]["lost"], 1)
    return {"steals": steals, "owed": owed, "bench": bench, "summary": summary}


def guillotine_section():
    """APFN Guillotine League, by the league's rules doc: head-to-head results do not count. After
    each week the surviving team with the lowest season points total is chopped and its roster
    dropped to free agency, and the lowest score of the week among teams that carry on costs
    $50 FAAB. One team goes per week until two remain; they meet head to head in week 17.
    Chops are worked out from scores and checked against the league's own roster drops."""
    lg = load_league(GUILLOTINE)
    d, teams = lg["dir"], lg["teams"]
    key = lambda rid: teams[rid]["key"]
    budget = lg["league"]["settings"].get("waiver_budget", 1000)

    def star(m):
        pts = m.get("players_points") or {}
        best = max(m["starters"], key=lambda p: pts.get(p, 0)) if m.get("starters") else None
        return (pname(best), pts.get(best, 0)) if best else ("", 0)

    # The league's own chops: a commissioner move that drops a whole roster.
    dropped, released = {}, {}
    for w in range(1, WEEK_NOW + 1):
        for t in load(f"{d}/transactions_{w:02d}.json"):
            drops = t.get("drops") or {}
            if t["type"] == "commissioner" and len(drops) >= 5 and len(set(drops.values())) == 1:
                dropped[next(iter(drops.values()))] = w
                released[next(iter(drops.values()))] = list(drops)

    alive, total, weeks, final = set(teams), defaultdict(float), [], None
    for w in DONE:
        ms = lg["matchups"][w]
        if len(alive) <= 2:  # the final: head to head on the week's score
            a, b = sorted(alive, key=lambda r: -ms[r]["points"])
            final = {"week": w, "champion": key(a), "runner_up": key(b),
                     "scores": [ms[a]["points"], ms[b]["points"]]}
            break
        for r in alive:
            total[r] += ms[r]["points"]
        by_total = sorted(alive, key=lambda r: total[r])
        chopped, rest = by_total[0], by_total[1:]
        penalty = min(rest, key=lambda r: ms[r]["points"])
        rows = [{"team": key(r), "pts": ms[r]["points"], "total": round(total[r], 2),
                 "star": star(ms[r])[0], "star_pts": star(ms[r])[1],
                 "chopped": r == chopped, "penalty": r == penalty} for r in sorted(alive, key=lambda r: -total[r])]
        weeks.append({"week": w, "teams": len(alive), "rows": rows, "chopped": key(chopped), "penalty": key(penalty),
                      "cushion": round(total[rest[0]] - total[chopped], 2),
                      "sleeper": "done" if dropped.get(chopped) else "pending",
                      "sleeper_chopped": [key(r) for r, cw in dropped.items() if cw == w and r in teams]})
        alive = set(rest)

    rosters = {r["roster_id"]: r for r in lg["rosters"]}
    on_block = sorted(alive, key=lambda r: total[r])
    block = [{"team": key(r), "total": round(total[r], 2), "last": lg["matchups"][DONE[-1]][r]["points"] if DONE else 0,
              "cushion": round(total[r] - total[on_block[0]], 2),
              "faab": budget - (rosters[r]["settings"].get("waiver_budget_used") or 0),
              "penalties": sum(GUILLOTINE_PENALTY for x in weeks if x["penalty"] == key(r))} for r in on_block]

    bids = []
    for w in range(1, WEEK_NOW + 1):
        for t in load(f"{d}/transactions_{w:02d}.json"):
            bid = (t.get("settings") or {}).get("waiver_bid", 0)
            if t["type"] == "waiver" and t["status"] == "complete" and bid > 0:
                for pid in t.get("adds") or {}:
                    bids.append({"week": w, "team": key(t["roster_ids"][0]), "player": pname(pid), "bid": bid})
    # A penalty shows in Sleeper as FAAB spent beyond the bids a team has won.
    won = defaultdict(int)
    for b in bids:
        won[b["team"]] += b["bid"]
    penalties = []
    for r in teams:
        owed = [x["week"] for x in weeks if x["penalty"] == key(r)]
        if owed:
            extra = (rosters[r]["settings"].get("waiver_budget_used") or 0) - won[key(r)]
            penalties.append({"team": key(r), "weeks": owed, "owed": GUILLOTINE_PENALTY * len(owed),
                              "applied": max(0, extra), "ok": extra >= GUILLOTINE_PENALTY * len(owed),
                              "over": extra > GUILLOTINE_PENALTY * len(owed)})
    bids.sort(key=lambda b: -b["bid"])

    # Weekly standings in the league's own format: every team by season points (chopped teams at
    # the bottom, most recent first) with FAAB as it stood at the chop, before that week's waivers.
    # FAAB comes from what Sleeper has actually taken, less bids won since and penalties since.
    used = {key(r): rosters[r]["settings"].get("waiver_budget_used") or 0 for r in teams}
    applied = {p["team"]: p["ok"] for p in penalties}
    out_at, total_at = {}, {}
    for x in weeks:
        chopped_row = next(r for r in x["rows"] if r["chopped"])
        out_at[x["chopped"]], total_at[x["chopped"]] = x["week"], chopped_row["total"]
    for x in weeks:
        w = x["week"]
        def faab(k):
            later_bids = sum(b["bid"] for b in bids if b["team"] == k and b["week"] >= w)
            later_pen = GUILLOTINE_PENALTY * sum(1 for y in weeks if y["penalty"] == k and y["week"] >= w) if applied.get(k) else 0
            return budget - max(0, used[k] - later_bids - later_pen)
        gone = sorted((k for k, cw in out_at.items() if cw < w), key=lambda k: -out_at[k])
        x["table"] = [{"team": r["team"], "total": r["total"], "faab": faab(r["team"]), "chopped": r["chopped"],
                       "penalty": r["penalty"]} for r in x["rows"]]
        x["table"] += [{"team": k, "total": total_at[k], "faab": faab(k), "out": out_at[k]} for k in gone]
        rid = next(r for r in teams if key(r) == x["chopped"])
        x["returned"] = [pinfo(pid) for pid in released.get(rid) or rosters[rid].get("players") or []]

    history = json.loads((ROOT / "content" / "league-history.json").read_text(encoding="utf-8"))
    alias = {old: p["known_as"] for p in history.get("same_person", []) for old in p["earlier_accounts"]}
    # Past champions, replayed from the scores by the same rules. Sleeper's own "league winner" field
    # comes from its head-to-head playoffs, which mean nothing here, so it is not used.
    champions = []
    for x in load("history/guillotine_index.json") if (RAW / "history" / "guillotine_index.json").exists() else []:
        h = f"history/{x['league_id']}"
        past = load(f"{h}/league.json")
        users = {u["user_id"]: u["display_name"] for u in load(f"{h}/users.json")}
        name = {r["roster_id"]: alias.get(r["owner_id"]) or users.get(r["owner_id"]) for r in load(f"{h}/rosters.json")}
        left, tot, result = set(name), defaultdict(float), None
        for w in range(past["settings"].get("start_week", 1), 19):
            ms = {m["roster_id"]: m["points"] for m in load(f"{h}/matchups_{w:02d}.json")}
            if not any(ms.get(r) for r in left):
                break
            if len(left) == 2:
                a, b = sorted(left, key=lambda r: -ms.get(r, 0))
                result = {"champion": name[a], "runner_up": name[b], "week": w, "scores": [ms.get(a, 0), ms.get(b, 0)]}
                break
            for r in left:
                tot[r] += ms.get(r, 0)
            left.discard(min(left, key=lambda r: tot[r]))
        champions.append({"season": x["season"], "name": x["name"], "teams": past["total_rosters"],
                          **(result or {"champion": None})})

    return {
        "teams": [{"key": t["key"], "manager": t["manager"], "team": t["team"], "avatar": t["avatar"], "conf": "Guillotine"}
                  for t in teams.values()],
        "penalty": GUILLOTINE_PENALTY, "budget": budget, "start": len(teams),
        "weeks": weeks, "final": final, "alive": len(alive), "block": block,
        "pending": [x for x in weeks if x["sleeper"] == "pending"], "penalties": penalties,
        "bids": bids[:12], "champions": sorted(champions, key=lambda c: c["season"]),
    }


def weeks_final():
    """Weeks whose NFL games have all finished (can be ahead of Sleeper's week counter)."""
    games = defaultdict(list)
    for g in load(f"nfl/schedule_{STATE['season']}.json"):
        games[g["week"]].append(g["status"])
    return [w for w, st in sorted(games.items()) if w <= WEEK_NOW and st and all(s == "complete" for s in st)]


def robber_waivers(rob):
    """Apply the house rule to the Robber League waiver order and keep a record of it.

    Rule: after each week, that week's losers move ahead of its winners, each group
    keeping its current order. The order is snapshotted once, when the week's games end,
    so later waiver claims don't disturb it. content/robber-waivers.json is committed by
    the daily build."""
    path = ROOT / "content" / "robber-waivers.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    name = {r["roster_id"]: rob["teams"][r["roster_id"]]["manager"] for r in rob["rosters"]}
    now = [name[r["roster_id"]] for r in sorted(rob["rosters"], key=lambda r: r["settings"].get("waiver_position") or 99)]
    finals = weeks_final()
    if finals and str(finals[-1]) not in store["weeks"]:
        week = finals[-1]
        rows = load(f"leagues/{ROBBER}/matchups_{week:02d}.json")
        by = defaultdict(list)
        for m in rows:
            by[m["matchup_id"]].append(m)
        losers = set()
        for pair in by.values():
            if len(pair) == 2 and pair[0]["points"] != pair[1]["points"]:
                losers.add(name[min(pair, key=lambda m: m["points"])["roster_id"]])
        if losers:
            store["weeks"][str(week)] = {"source": "automatic", "before": now,
                                         "after": [n for n in now if n in losers] + [n for n in now if n not in losers]}
            path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    week = max(store["weeks"], key=int)
    entry = store["weeks"][week]
    return {"week": int(week), "source": entry["source"], "before": entry["before"], "after": entry["after"],
            "now": now, "matches": now == entry["after"]}


def load_schedule(confs):
    """Cross-conference league games by week, as roster keys, from content/league-schedule-<season>.json."""
    key_of = {t["manager"].lower(): t["key"] for lg in confs.values() for t in lg["teams"].values()}
    path = ROOT / "content" / f"league-schedule-{STATE['season']}.json"
    schedule = {}
    if path.exists():
        for week, games in json.loads(path.read_text(encoding="utf-8"))["weeks"].items():
            for a, b in games:
                if a.lower() in key_of and b.lower() in key_of:
                    schedule.setdefault(int(week), []).append((key_of[a.lower()], key_of[b.lower()]))
    return schedule


def league_season(confs):
    """League table, league games and the league tournament for the current season."""
    feed = [(lid, {r["roster_id"]: r["owner_id"] for r in lg["rosters"]},
             {w: load(f"leagues/{lid}/matchups_{w:02d}.json") for w in range(1, WEEK_NOW + 1)})
            for lid, lg in confs.items()]
    teams = tournament.teams_from_matchups(feed)
    key_of = {t["manager"].lower(): t["key"] for lg in confs.values() for t in lg["teams"].values()}
    path = ROOT / "content" / f"league-schedule-{STATE['season']}.json"
    schedule, unknown = {}, []
    if path.exists():
        for week, games in json.loads(path.read_text(encoding="utf-8"))["weeks"].items():
            for a, b in games:
                if a.lower() in key_of and b.lower() in key_of:
                    schedule.setdefault(int(week), []).append((key_of[a.lower()], key_of[b.lower()]))
                else:
                    unknown += [n for n in (a, b) if n.lower() not in key_of]
    if unknown:
        print("league schedule: unknown managers", sorted(set(unknown)))
    done = DONE[-1] if DONE else 0
    result = tournament.season(teams, done, schedule)
    result["has_schedule"] = bool(schedule)
    # Every game this week, ranked by the commissioner's rule (sum of league ranks, ties to the
    # higher-ranked team). Cross-conference league games come first once they start.
    rank = {t["key"]: t["rank"] for t in result["table"]}
    conference = [tuple(p) for lg in confs.values() for p in upcoming(lg) if all(k in rank for k in p)]
    games = [{"kind": "League game", "a": g["a"], "b": g["b"]} for g in result["league_games"]["games"]]
    games += [{"kind": "Conference game", "a": a, "b": b} for a, b in tournament.order_games(conference, rank)]
    for g in games:
        g["a"], g["b"] = sorted((g["a"], g["b"]), key=rank.get)
        g.update(a_rank=rank[g["a"]], b_rank=rank[g["b"]])
    result["week_games"] = {"week": WEEK_NOW, "games": games}
    return result


def commissioner(leagues, robber):
    """What the commissioner acts on each week. Everything here is public Sleeper data;
    the PIN on the tab only keeps it out of everyone else's way."""
    playing = {x for g in load(f"nfl/schedule_{STATE['season']}.json") if g["week"] == WEEK_NOW
               for x in (g["home"], g["away"])}
    out_statuses = {"Out", "IR", "PUP", "Sus", "NA", "DNR"}
    lineups, moves = [], []
    for name, lg in leagues:
        slots = [s for s in lg["league"]["roster_positions"] if s != "BN"]
        who = {rid: t["manager"] for rid, t in lg["teams"].items()}
        for r in lg["rosters"]:
            issues = []
            for slot, pid in zip(slots, (r["starters"] or []) + [None] * len(slots)):
                if not pid or pid == "0":
                    issues.append({"level": "problem", "text": f"Empty {slot} slot"})
                    continue
                p = PLAYERS.get(pid, {})
                status = p.get("injury_status") or ""
                if p.get("team") and p["team"] not in playing:
                    issues.append({"level": "problem", "text": f"{pname(pid)} ({p['team']}) is on bye"})
                elif status in out_statuses:
                    issues.append({"level": "problem", "text": f"{pname(pid)} is listed {status}"})
                elif status == "Doubtful":
                    issues.append({"level": "watch", "text": f"{pname(pid)} is doubtful"})
            if issues:
                lineups.append({"league": name, "manager": who[r["roster_id"]], "issues": issues})

        for w in (WEEK_NOW, WEEK_NOW - 1):
            path = RAW / lg["dir"] / f"transactions_{w:02d}.json"
            for t in json.loads(path.read_text(encoding="utf-8")) if path.exists() else []:
                if t["status"] != "complete":
                    continue
                parts = [f"{who.get(rid, '?')} adds {pname(pid)}" for pid, rid in (t.get("adds") or {}).items()]
                parts += [f"{who.get(rid, '?')} drops {pname(pid)}" for pid, rid in (t.get("drops") or {}).items()]
                if t["type"] == "commissioner" and set(t.get("adds") or {}) & set(t.get("drops") or {}):
                    pid = next(iter(set(t["adds"]) & set(t["drops"])))
                    parts = [f"{pname(pid)} moved from {who.get(t['drops'][pid], '?')} to {who.get(t['adds'][pid], '?')}"]
                moves.append({"league": name, "type": t["type"].replace("_", " "), "at": t["created"],
                              "text": "; ".join(parts) or "Draft pick or budget change"})

    waiver = []
    if DONE:
        teams = {t["key"]: t for t in robber["teams"]}
        for t in robber["teams"]:
            g = next((x for x in t["weeks"] if x["week"] == DONE[-1] and x["res"] == "W"), None)
            if g:
                loser = teams[g["opp"]]
                waiver.append({"winner": t["manager"], "loser": loser["manager"], "winner_pos": t["waiver"],
                               "loser_pos": loser["waiver"], "ok": (loser["waiver"] or 99) < (t["waiver"] or 99)})
    pin = json.loads((ROOT / "content" / "commissioner.json").read_text(encoding="utf-8"))
    return {
        "week": WEEK_NOW, "last_done": DONE[-1] if DONE else None,
        "lineups": sorted(lineups, key=lambda x: (x["league"], x["manager"].lower())),
        "owed": robber["owed"], "bench": [b for b in robber["bench"] if b["flag"]],
        "waiver": sorted(waiver, key=lambda x: x["loser_pos"] or 99),
        "moves": sorted(moves, key=lambda m: -m["at"])[:40],
        "lock": {"salt": pin["salt"], "sha256": pin["pin_sha256"]},
    }


def compute():
    confs = {lid: load_league(lid) for lid in CONFERENCES}
    pool = []
    for lid, lg in confs.items():
        for t in lg["teams"].values():
            t["conf"] = CONFERENCES[lid]
            pool.append(t)
    add_all_play(pool)

    names = list(CONFERENCES.values())
    a, b = ([t for t in pool if t["conf"] == n] for n in names)
    cross = {n: 0.0 for n in names}
    for w in DONE:
        for ta in a:
            for tb in b:
                pa = next(x["pts"] for x in ta["weeks"] if x["week"] == w)
                pb = next(x["pts"] for x in tb["weeks"] if x["week"] == w)
                cross[names[0]] += 1 if pa > pb else 0.5 if pa == pb else 0
                cross[names[1]] += 1 if pb > pa else 0.5 if pa == pb else 0

    rob = load_league(ROBBER)
    rob_teams = list(rob["teams"].values())
    for t in rob_teams:
        t["conf"] = "Robber"
    add_all_play(rob_teams)

    data = {
        "season": STATE["season"], "week_now": WEEK_NOW, "done": DONE,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "teams": pool, "conferences": names,
        "playoff_teams": next(iter(confs.values()))["league"]["settings"]["playoff_teams"],
        "cross": cross, "conf_ppg": {n: round(sum(t["ppg"] for t in pool if t["conf"] == n) / 10, 2) for n in names},
        "awards": awards(pool, DONE[-1]) if DONE else [],
        # Every finished week, newest first, so past recaps stay readable on the site.
        "recaps": [recap([(CONFERENCES[lid], lg) for lid, lg in confs.items()] + [("Robber", rob)], confs, rob, w)
                   for w in reversed(DONE)],
        "upcoming": {CONFERENCES[lid]: upcoming(lg) for lid, lg in confs.items()},
        "records": build_records(),
        "robber": {"teams": rob_teams, "upcoming": upcoming(rob),
                   "playoff_teams": rob["league"]["settings"]["playoff_teams"], **robber_section(rob)},
    }
    data["guillotine"] = G = guillotine_section()
    # Each recap week gets a Guillotine section after the Robber League.
    for R in data["recaps"]:
        gw = next((x for x in G["weeks"] if x["week"] == R["week"]), None)
        if gw:
            path = ROOT / "content" / "recaps" / f"{STATE['season']}-week-{R['week']:02d}.json"
            prose = json.loads(path.read_text(encoding="utf-8")).get("Guillotine", {}) if path.exists() else {}
            out_name = next(t["manager"] for t in G["teams"] if t["key"] == gw["chopped"])
            R["leagues"].append({"name": "Guillotine", "headline": prose.get("headline", f"{out_name} gets the chop"),
                                 "paragraphs": prose.get("paragraphs", []), "games": [], "robber": False,
                                 "guillotine": gw})
    data["league"] = league_season(confs)
    data["preview"] = preview(confs, rob)
    data["robber"]["waiver_rule"] = robber_waivers(rob)
    data["commish"] = commissioner([(CONFERENCES[lid], lg) for lid, lg in confs.items()] + [("Robber", rob)],
                                   data["robber"])
    return data


def main():
    data = compute()
    html = (SITE / "template.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (SITE / "index.html").write_text(html.replace("/*__DATA__*/null", blob), encoding="utf-8")
    (SITE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("built site/index.html through week", DONE[-1] if DONE else 0,
          "| steals", len(data["robber"]["steals"]), "| owed", len(data["robber"]["owed"]))


if __name__ == "__main__":
    main()
