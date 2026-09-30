# Sleeper API reference

Working notes from research on 2026-09-30. "Verified" means the call was made live that day.
Items marked *unverified* come from docs or prior knowledge and still need a check against a real league.

## Basics

- Base URL: `https://api.sleeper.app/v1` — read-only, GET only, JSON, no auth or API key.
- Rate limit: stay under 1000 calls/minute or risk an IP block (HTTP 429).
- Responses are CDN-cached (`cache-control: s-maxage=60`), so polling faster than once a minute gains nothing. Verified.
- CORS is open (`access-control-allow-origin: *`), so a browser can call it directly. Verified.
- Terms: free for non-commercial use; commercial use requires a license from Sleeper. Trending data requires attribution.
- Nothing can be written. Lineups, waivers, trades, chat and settings changes all happen in the Sleeper app only.

## Documented endpoints (docs.sleeper.com)

| Purpose | Path |
|---|---|
| User by username or id | `/user/<username_or_id>` |
| User's leagues for a season | `/user/<user_id>/leagues/nfl/<season>` |
| League | `/league/<league_id>` |
| Rosters | `/league/<league_id>/rosters` |
| League users | `/league/<league_id>/users` |
| Matchups for a week | `/league/<league_id>/matchups/<week>` |
| Playoff brackets | `/league/<league_id>/winners_bracket`, `/losers_bracket` |
| Transactions for a week | `/league/<league_id>/transactions/<week>` |
| Traded picks | `/league/<league_id>/traded_picks` |
| League drafts | `/league/<league_id>/drafts` |
| User drafts | `/user/<user_id>/drafts/nfl/<season>` |
| Draft, picks, traded picks | `/draft/<draft_id>`, `/draft/<draft_id>/picks`, `/draft/<draft_id>/traded_picks` |
| NFL state (current week/season) | `/state/nfl` |
| All players | `/players/nfl` |
| Trending adds/drops | `/players/nfl/trending/<add\|drop>?lookback_hours=24&limit=25` |
| Avatars | `https://sleepercdn.com/avatars/<id>` and `/avatars/thumbs/<id>` |

### Things that matter when modelling the data

- **Identity**: `user_id` is permanent; usernames and display names change. `roster_id` (1..N) is the team slot inside one league-season; `owner_id` on a roster is the user. `co_owners` may exist.
- **Season chaining**: each season is a separate `league_id`. Walk `previous_league_id` backwards to build full history. A team's `roster_id` usually persists across seasons but the owner can change, so key history on `user_id`.
- **Matchups**: two entries sharing a `matchup_id` play each other. `matchup_id: null` means bye or eliminated. `points` is the total; `custom_points` is a commissioner override. `players_points` (player_id to points) and `starters_points` (ordered like `starters`) are present. Verified.
- **Scoring recompute**: `sum(stat[k] * scoring_settings[k])` matched Sleeper's `players_points` exactly on 779 of 779 player-weeks across two APFN leagues (2026 weeks 1-3). Verified.
- **Settings-copy link**: a league created by copying another has `metadata.copy_from_league_id` while `previous_league_id` is null. APFN Armadillo was copied from "Eagle Conference" (2021-2025, chained by `previous_league_id`), so older history lives there.
- **Commissioner moves** show up as transactions with `type: commissioner`; a forced player move is an add and a drop of the same player in one transaction.
- **Bench** = `players` minus `starters`. `reserve` is IR, `taxi` is taxi squad.
- **Roster settings**: `wins`, `losses`, `ties`, `fpts` + `fpts_decimal`, `fpts_against` + `_decimal`, `ppts` (max potential points), `waiver_position`, `waiver_budget_used`, `total_moves`.
- **Transactions**: `type` is `trade`, `waiver`, `free_agent` (also `commissioner`). `adds`/`drops` map player_id to roster_id. `draft_picks` and `waiver_budget` carry traded picks and FAAB. `settings.waiver_bid` holds the FAAB bid. Failed waiver claims appear with `status: failed`.
- **Brackets**: `r` round, `m` match id, `t1`/`t2` roster ids, `w`/`l` winner/loser, `t1_from`/`t2_from` reference earlier matches, `p` is the final placement the match decides (p=1 is the championship).
- **Playoff weeks**: `league.settings.playoff_week_start`; regular-season records should exclude weeks from there on.
- **League object**: `settings` (playoff teams, waiver type, trade deadline, divisions, `league_average_match` for the median game, `best_ball`, `type` 0 redraft / 1 keeper / 2 dynasty), `scoring_settings` (stat key to points), `roster_positions`, `metadata` (division names, etc.).

### State endpoint (verified)

```json
{"week":4,"leg":4,"season":"2026","season_type":"regular","league_season":"2026",
 "previous_season":"2025","season_start_date":"2026-09-09","display_week":4,
 "league_create_season":"2026","season_has_scores":true}
```

### Players endpoint (verified)

- 12,229 players, **about 14.7 MB** (docs still say 5 MB). Fetch at most once a day and cache.
- Keyed by `player_id` (string). Team defenses use the team abbreviation as the id, e.g. `"BUF"`, position `DEF`.
- Useful fields: `full_name`, `position`, `fantasy_positions`, `team`, `status`, `injury_status`, `injury_body_part`, `depth_chart_position`, `depth_chart_order`, `years_exp`, `age`, `birth_date`, `search_rank`.
- Cross-reference ids for joining other data sources: `gsis_id` (nflverse), `espn_id`, `yahoo_id`, `sportradar_id`, `rotowire_id`, `fantasy_data_id`, `stats_id`.

## Undocumented endpoints (verified working, no auth; may change without notice)

| Purpose | URL |
|---|---|
| All player stats for a week | `api.sleeper.app/v1/stats/nfl/regular/<season>/<week>` |
| All player stats for a season | `api.sleeper.app/v1/stats/nfl/regular/<season>` |
| All projections for a week | `api.sleeper.app/v1/projections/nfl/regular/<season>/<week>` |
| Stats with player info, filterable | `api.sleeper.com/stats/nfl/<season>/<week>?season_type=regular&position[]=QB&order_by=pts_ppr` |
| Projections with player info | `api.sleeper.com/projections/nfl/<season>/<week>?season_type=regular&position[]=QB&order_by=pts_ppr` |
| Season projections + ADP | `api.sleeper.com/projections/nfl/<season>?season_type=regular&position[]=RB&order_by=adp_ppr` |
| One player's weekly log | `api.sleeper.com/stats/nfl/player/<player_id>?season_type=regular&season=<season>&grouping=week` |
| NFL schedule | `api.sleeper.com/schedule/nfl/regular/<season>` |

- Stats use about 260 keys (`pass_yd`, `rush_td`, `rec`, `bonus_rec_te`, `idp_tkl_solo`, `pts_allow_7_13`, ...). These are the **same keys as `league.scoring_settings`**, so any league's custom scoring can be recomputed as `sum(stat[k] * scoring[k])`.
- Pre-computed `pts_std`, `pts_half_ppr`, `pts_ppr` and positional ranks are included.
- Season projections include ADP variants: `adp_ppr`, `adp_half_ppr`, `adp_std`, `adp_2qb`, `adp_dynasty*`, `adp_rookie`, `adp_idp`.
- Returned 404: `/players/nfl/research/...` (ownership %) and `/players/nfl/<team>/depth_chart`.

## GraphQL (`POST https://sleeper.com/graphql`)

- This is what the Sleeper web and mobile apps use. Undocumented.
- Public queries work without a token (verified): `get_player_news` (player news with analysis text) and `scores` (live NFL game state: quarter, possession, down and distance, scores by quarter, weather, stadium).
- League-scoped queries return `Unauthorized` without a user token (verified with `league_transactions_filtered`). Using a logged-in user's token is outside the public API terms; avoid building on it.

## Not available through any public route

- League chat messages, polls, reactions.
- Pending trades and pending waiver claims.
- Dues and payment status.
- Commissioner-entered history for seasons played on other platforms (the in-app League History editor).
- Any write action.
