"""League standings and the league tournament, across both conferences.

Rules, as the commissioner runs them by hand:
- All 20 teams share one league table, ranked by wins, then points for. Each week's
  score counts once toward points for, even in weeks with two games.
- From 2026, weeks 5-14 add a second, cross-conference "league game" each week
  (content/league-schedule-<season>.json). It counts only in the league table.
- After week 14, seeds 1-8 play for the title and seeds 9-16 play a consolation bracket,
  in weeks 15 (round 1), 16 (round 2) and 17 (finals). Seeds 17-20 finish in table order.
- Each round the highest remaining seed plays the lowest remaining seed, and the higher
  score advances. Round 1 losers play for 5th-8th (or 13th-16th) in week 16, the two
  higher seeds for the upper place; round 2 losers play for 3rd (or 11th) in week 17.
- A week's league games are listed by the sum of the two teams' league ranks, lowest
  first; ties go to the game with the higher-ranked team. The first is game of the week.

Replaying these rules on Sleeper scores reproduces every official champion from 2021 to 2025.
"""
from collections import defaultdict

SEED_WEEK = 14
ROUNDS = (15, 16, 17)


def new_team():
    return {"points": {}, "games": []}  # games: (week, 1 | 0.5 | 0, kind)


def add_result(teams, a, b, week, kind):
    pa, pb = teams[a]["points"].get(week), teams[b]["points"].get(week)
    if pa is None or pb is None or not (pa or pb):
        return
    teams[a]["games"].append((week, 1 if pa > pb else 0.5 if pa == pb else 0, kind))
    teams[b]["games"].append((week, 1 if pb > pa else 0.5 if pa == pb else 0, kind))


def teams_from_matchups(leagues):
    """leagues: iterable of (conference, owner_by_roster, {week: [matchup rows]}).
    Returns teams keyed 'Conference:roster_id' with weekly points and conference results."""
    teams = defaultdict(new_team)
    for conf, owner, weeks in leagues:
        for week, rows in weeks.items():
            by = defaultdict(list)
            for m in rows:
                key = f"{conf}:{m['roster_id']}"
                teams[key]["owner"], teams[key]["conf"] = owner.get(m["roster_id"]), conf
                if m.get("points"):
                    teams[key]["points"][week] = m["points"]
                if m.get("matchup_id"):
                    by[m["matchup_id"]].append(key)
            for pair in by.values():
                if len(pair) == 2:
                    add_result(teams, pair[0], pair[1], week, "conference")
    return dict(teams)


def add_league_games(teams, schedule):
    """schedule: {week: [(key_a, key_b), ...]} of cross-conference league games."""
    for week, games in schedule.items():
        for a, b in games:
            add_result(teams, a, b, int(week), "league")


def record(team, through):
    res = [v for w, v, _ in team["games"] if w <= through]
    wins = sum(res)
    ties = sum(1 for v in res if v == 0.5)
    return {"w": int(wins - ties / 2), "l": len(res) - int(wins - ties / 2) - ties, "t": ties,
            "pf": round(sum(v for w, v in team["points"].items() if w <= through), 2)}


def standings(teams, through):
    def key(k):
        t = teams[k]
        return (-sum(v for w, v, _ in t["games"] if w <= through),
                -sum(v for w, v in t["points"].items() if w <= through))
    return sorted(teams, key=key)


def order_games(games, rank):
    """Game-of-the-week order: lowest rank sum first, ties to the higher-ranked team."""
    return sorted(games, key=lambda g: (rank[g[0]] + rank[g[1]], min(rank[g[0]], rank[g[1]])))


def eight_team(teams, seeded, seed_of, weeks_done, top_place, names):
    """One 8-team bracket with placement games. seeded: 8 keys, best seed first."""
    labels = {1: names[0], 2: names[1], 3: names[2]}
    rounds, places = [], {}

    def play(hi, lo, week, label):
        hi, lo = sorted((hi, lo), key=seed_of.get)
        g = {"label": label, "week": week, "hi": hi, "lo": lo, "hi_seed": seed_of[hi], "lo_seed": seed_of[lo],
             "hi_pts": None, "lo_pts": None, "winner": None, "loser": None}
        if weeks_done >= week:
            hp, lp = teams[hi]["points"].get(week, 0), teams[lo]["points"].get(week, 0)
            g.update(hi_pts=hp, lo_pts=lp, winner=hi if hp >= lp else lo, loser=lo if hp >= lp else hi)
        return g

    by_seed = lambda ks: sorted(ks, key=seed_of.get)
    if weeks_done < ROUNDS[0] - 1:
        return {"rounds": [], "places": {}}
    r1 = [play(seeded[i], seeded[7 - i], ROUNDS[0], labels[1]) for i in range(4)]
    rounds.append({"week": ROUNDS[0], "games": r1})
    if weeks_done >= ROUNDS[0]:
        win, lose = by_seed(g["winner"] for g in r1), by_seed(g["loser"] for g in r1)
        r2 = [play(win[0], win[3], ROUNDS[1], labels[2]), play(win[1], win[2], ROUNDS[1], labels[2]),
              play(lose[0], lose[1], ROUNDS[1], f"{top_place + 4}/{top_place + 5} place"),
              play(lose[2], lose[3], ROUNDS[1], f"{top_place + 6}/{top_place + 7} place")]
        rounds.append({"week": ROUNDS[1], "games": r2})
        if weeks_done >= ROUNDS[1]:
            for g, first in ((r2[2], top_place + 4), (r2[3], top_place + 6)):
                places[g["winner"]], places[g["loser"]] = first, first + 1
            win, lose = by_seed(g["winner"] for g in r2[:2]), by_seed(g["loser"] for g in r2[:2])
            r3 = [play(win[0], win[1], ROUNDS[2], labels[3]),
                  play(lose[0], lose[1], ROUNDS[2], f"{top_place + 2}/{top_place + 3} place")]
            rounds.append({"week": ROUNDS[2], "games": r3})
            if weeks_done >= ROUNDS[2]:
                for g, first in ((r3[0], top_place), (r3[1], top_place + 2)):
                    places[g["winner"]], places[g["loser"]] = first, first + 1
    return {"rounds": rounds, "places": places}


def season(teams, weeks_done, schedule=None, order=None):
    """League table, this week's league games, and both brackets as far as they can be settled.
    order: a fixed seed order (the commissioner's published table) instead of the rebuilt one."""
    if schedule:
        add_league_games(teams, schedule)
    through = min(weeks_done, SEED_WEEK)
    order = order or standings(teams, through)
    seed_of = {k: i + 1 for i, k in enumerate(order)}
    title = eight_team(teams, order[:8], seed_of, weeks_done, 1,
                       ("Round 1", "Semifinal", "League championship"))
    consolation = eight_team(teams, order[8:16], seed_of, weeks_done, 9,
                             ("Consolation round 1", "Consolation semifinal", "Consolation final"))
    places = {**title["places"], **consolation["places"]}
    if weeks_done >= ROUNDS[-1]:
        places.update({k: seed_of[k] for k in order[16:]})
    champion = next((k for k, p in places.items() if p == 1), None)
    # Current league table (all games to date, not frozen at week 14) for ranking this week's games.
    live_order = standings(teams, weeks_done)
    rank = {k: i + 1 for i, k in enumerate(live_order)}
    week_next = weeks_done + 1
    upcoming = order_games([tuple(g) for g in (schedule or {}).get(week_next, [])], rank)
    return {
        "provisional": weeks_done < SEED_WEEK, "through": through,
        "table": [{"key": k, "rank": rank[k], **record(teams[k], weeks_done),
                   "conf_record": record({"points": teams[k]["points"],
                                          "games": [g for g in teams[k]["games"] if g[2] == "conference"]}, weeks_done)}
                  for k in live_order],
        "seeds": [{"key": k, "seed": seed_of[k], **record(teams[k], through)} for k in order],
        "title": title["rounds"], "consolation": consolation["rounds"],
        "places": places, "champion": champion,
        "league_games": {"week": week_next, "games": [{"a": a, "b": b, "a_rank": rank[a], "b_rank": rank[b]}
                                                      for a, b in upcoming]},
    }
