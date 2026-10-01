"""Write last week's recap with Claude, if nobody has written one yet.

Optional step. It does nothing unless ANTHROPIC_API_KEY is set and
content/recaps/<season>-week-<NN>.json is missing, and it never fails the build:
without a recap file the hub's Recap tab simply shows the final scores.

A hand-written or hand-edited recap file always wins, because an existing file is never
overwritten.
"""
import json
import os
import re
import sys

import build_site as B

MODEL = "claude-opus-5-5"
LEAGUES = {**B.CONFERENCES, B.ROBBER: "Robber"}
BYLINE = "Written automatically from the week's Sleeper data."

SYSTEM = """You write the weekly recap for a fantasy football league called APFN, read by the \
twenty friends who play in it. There are two ten-team conferences, Armadillo and Grizzly, and a \
separate ten-team Robber League where each week's winner steals one player from the team they beat.

Write one story per league from the facts you are given: a headline, then the games one at a time.
For each game, write one or two paragraphs that open with the Sleeper name of a manager in that game,
then a final paragraph that is a single pithy, punchy one-liner about the matchup. The one-liner names
no manager outside that game. A league-wide note can close the story as its last paragraph.

Rules:
- Every number, name and result must come from the facts. You may add or subtract two numbers that are in the facts; never estimate, and never bring in anything about real NFL games that is not there.
- Refer to managers by their Sleeper names exactly as given. Avoid he/she for managers; use their names.
- Lead with the most interesting game, not the first one in the list. Good material: close games, points left on the bench that would have changed a result, unbeaten or winless teams, a team whose record is much better or worse than its all-play rank, all-time records under threat, and in the Robber League who now gets to steal from whom and how past steals have worked out.
- Voice (the commissioner's call): witty, irreverent and fun to read, the real flavor of the league. Mature, R-rated humor and profanity are fine; this is a grown-up league. Build jokes on team names whenever you can. Point out lineup mistakes when they are sensible (a benched player who would have changed the result, a starter who scored next to nothing). Roast the football, the lineups, the scores and the team names, never the person: no jokes about anyone's real life, looks, job, family, health, money or identity.
- No emoji.
- Headlines are one sentence-case line with no full stop."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": list(LEAGUES.values()),
    "properties": {name: {
        "type": "object", "additionalProperties": False, "required": ["headline", "paragraphs"],
        "properties": {"headline": {"type": "string"},
                       "paragraphs": {"type": "array", "items": {"type": "string"}}},
    } for name in LEAGUES.values()},
}


def facts(week):
    data = B.compute()
    teams = {t["key"]: t for t in data["teams"] + data["robber"]["teams"]}
    who = lambda key: teams[key]["manager"]
    out = {"week": week, "leagues": {}}
    for lid, name in LEAGUES.items():
        lg = B.load_league(lid)
        games, seen = [], set()
        for m in lg["matchups"][week].values():
            if m["matchup_id"] is None or m["matchup_id"] in seen:
                continue
            seen.add(m["matchup_id"])
            sides = []
            for x in sorted((x for x in lg["matchups"][week].values() if x["matchup_id"] == m["matchup_id"]),
                            key=lambda x: -x["points"]):
                pts, t = x.get("players_points") or {}, teams[lg["teams"][x["roster_id"]]["key"]]
                starters = sorted(((B.pname(p), pts.get(p, 0)) for p in x["starters"]), key=lambda s: -s[1])
                bench = sorted(((B.pname(p), pts.get(p, 0)) for p in x["players"] if p not in x["starters"]),
                               key=lambda s: -s[1])
                sides.append({
                    "manager": t["manager"], "team_name": t["team"], "points": x["points"],
                    "record_now": f"{t['w']}-{t['l']}", "top_starters": starters[:3], "worst_starters": starters[-2:],
                    "best_bench": bench[:2], "power_rank": t["rank"], "all_play": f"{t['ap_w']}-{t['ap_l']}",
                    "luck_wins_above_expected": t["luck"],
                    "points_left_on_bench": round(next(w["best"] - w["pts"] for w in t["weeks"] if w["week"] == week), 1),
                })
            games.append({"winner": sides[0], "loser": sides[1], "margin": round(sides[0]["points"] - sides[1]["points"], 2)})
        out["leagues"][name] = {"games": games}

    rec = data["records"]
    n = rec["names"]
    out["conference_awards_this_week"] = [dict(a, team=who(a["team"]), vs=who(a["vs"]) if a.get("vs") else None)
                                          for a in data["awards"]]
    out["all_time"] = {
        "note": "Conference games since 2021. Robber League excluded.",
        "highest_scores": [{"manager": n.get(s["id"]), "points": s["pts"], "season": s["season"], "week": s["week"]}
                           for s in rec["high"][:3]],
        "longest_win_streaks": [{"manager": n.get(s["id"]), "games": s["n"]} for s in rec["win_streaks"][:3]],
        "longest_losing_streaks": [{"manager": n.get(s["id"]), "games": s["n"]} for s in rec["loss_streaks"][:3]],
    }
    rob = data["robber"]
    out["robber"] = {
        "steals_so_far": [{"after_week": s["week"], "thief": who(s["thief"]), "robbed": who(s["victim"]),
                           "player": s["player"]["name"], "released": s["released"] and s["released"]["name"],
                           "points_for_thief_since": s["loot"], "net_vs_released_player": s["net"],
                           "weeks_held": s["held_weeks"]} for s in rob["steals"]],
        "steals_now_owed": [{"week": o["week"], "winner": who(o["winner"]), "loser": who(o["loser"]),
                             "losers_best_players": [p["name"] for p in o["targets"][:4]]} for o in rob["owed"]],
        "balance_by_manager": {who(k): v for k, v in rob["summary"].items()},
    }
    return out


def ask_claude(payload, example):
    import anthropic

    client = anthropic.Anthropic()
    prompt = "Facts for this week, as JSON:\n\n" + json.dumps(payload, ensure_ascii=False)
    if example:
        prompt = ("An earlier recap, to show the voice and length. Do not reuse its content:\n\n"
                  + json.dumps(example, ensure_ascii=False) + "\n\n" + prompt)
    request = dict(
        model=MODEL, max_tokens=16000, system=SYSTEM, thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        # Server-side fallback: if a safety classifier declines, the API reruns on another model.
        response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request)
    except anthropic.BadRequestError as e:
        print("recap: request with fallbacks was rejected, retrying without:", e.message)
        response = client.messages.create(**request)
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"model stopped early: {response.stop_reason}")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def unverified_numbers(recap, payload):
    """Decimal figures in the prose that are not in the facts. Sums and differences land here too."""
    known = set(re.findall(r"\d+\.\d+", json.dumps(payload)))
    known |= {f"{float(x):.2f}" for x in known} | {f"{float(x):.1f}" for x in known}
    text = " ".join(p for lg in recap.values() for p in lg["paragraphs"])
    return sorted(set(re.findall(r"\d+\.\d+", text)) - known)


def main():
    if not B.DONE:
        return print("recap: no finished week yet")
    week = B.DONE[-1]
    folder = B.ROOT / "content" / "recaps"
    path = folder / f"{B.STATE['season']}-week-{week:02d}.json"
    if path.exists():
        return print("recap: already written for week", week)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return print("recap: no ANTHROPIC_API_KEY, leaving week", week, "as scores only")
    earlier = sorted(folder.glob("*.json"))
    example = json.loads(earlier[-1].read_text(encoding="utf-8")) if earlier else None
    payload = facts(week)
    try:
        recap = ask_claude(payload, example)
    except Exception as e:  # never break the site build over the recap
        return print("recap: not written:", type(e).__name__, e, file=sys.stderr)
    odd = unverified_numbers(recap, payload)
    if odd:
        print("recap: figures not found verbatim in the facts (check they are sums or differences):", ", ".join(odd))
    for league in recap.values():
        league["byline"] = BYLINE
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("recap: wrote", path.name)


if __name__ == "__main__":
    main()
