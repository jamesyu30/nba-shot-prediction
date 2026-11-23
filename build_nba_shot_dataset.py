"""Build season-based NBA shot-prediction CSV files from NBA.com via nba_api.

Example:
    python3 build_nba_shot_dataset.py \
        --seasons 2021-22 2022-23 2023-24 2024-25 2025-26 \
        --test-season 2025-26 \
        --data-dir nba_project_data
"""

### requirement: python 3.10
### installed pandas and nba_api, run the next line
### python3 -m pip install pandas nba_api

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import (
    commonplayerinfo,
    commonteamroster,
    leaguedashplayerbiostats,
    shotchartdetail,
)
from nba_api.stats.static import teams

OUTPUT_COLUMNS = [
    "SEASON_1", "SEASON_2", "TEAM_ID", "TEAM_NAME", "PLAYER_ID",
    "PLAYER_NAME", "POSITION_GROUP", "POSITION", "GAME_DATE", "GAME_ID",
    "GAME_EVENT_ID", "HOME_TEAM", "AWAY_TEAM", "EVENT_TYPE", "SHOT_MADE", "ACTION_TYPE",
    "SHOT_TYPE", "BASIC_ZONE", "ZONE_NAME", "ZONE_ABB", "ZONE_RANGE",
    "LOC_X", "LOC_Y", "SHOT_DISTANCE", "QUARTER", "MINS_LEFT",
    "SECS_LEFT", "SEASON", "MADE", "SECONDS_LEFT_IN_PERIOD", "IS_THREE",
    "PLAYER_HEIGHT", "PLAYER_HEIGHT_INCHES", "PLAYER_WEIGHT", "AGE",
    "COUNTRY", "COLLEGE", "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER",
]


def request_with_retries(factory, attempts: int = 4, base_wait: float = 5.0):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return factory()
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                raise
            wait = base_wait * attempt
            print(f"Request failed; retrying in {wait:.0f}s: {exc!r}", flush=True)
            time.sleep(wait)
    raise RuntimeError(last_error)


def position_group(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip().upper().replace(" ", "")
    replacements = {
        "GUARD": "G", "FORWARD": "F", "CENTER": "C",
        "GUARD-FORWARD": "G-F", "FORWARD-GUARD": "F-G",
        "FORWARD-CENTER": "F-C", "CENTER-FORWARD": "C-F",
    }
    text = replacements.get(text, text)
    first = text.split("-")[0]
    if first in {"G", "PG", "SG"}:
        return "G"
    if first in {"F", "SF", "PF"}:
        return "F"
    if first == "C":
        return "C"
    return None


def normalize_position(value: object) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip().upper()
    replacements = {
        "GUARD": "G", "FORWARD": "F", "CENTER": "C",
        "GUARD-FORWARD": "G-F", "FORWARD-GUARD": "F-G",
        "FORWARD-CENTER": "F-C", "CENTER-FORWARD": "C-F",
    }
    return replacements.get(text, text or None)


def download_shots(season: str, raw_dir: Path, pause: float) -> pd.DataFrame:
    season_dir = raw_dir / "shots" / season
    season_dir.mkdir(parents=True, exist_ok=True)
    frames = []

    for number, team in enumerate(teams.get_teams(), start=1):
        team_id = int(team["id"])
        abbreviation = str(team["abbreviation"])
        path = season_dir / f"shots_{season}_{abbreviation}.csv"
        if path.exists() and path.stat().st_size > 0:
            frame = pd.read_csv(path, low_memory=False)
            print(f"Shots {season} [{number:02d}/30] {abbreviation}: cached")
        else:
            print(f"Shots {season} [{number:02d}/30] {abbreviation}: downloading")
            response = request_with_retries(
                lambda: shotchartdetail.ShotChartDetail(
                    team_id=team_id,
                    player_id=0,
                    season_nullable=season,
                    season_type_all_star="Regular Season",
                    context_measure_simple="FGA",
                    timeout=90,
                )
            )
            frame = response.shot_chart_detail.get_data_frame()
            frame["SEASON"] = season
            frame.to_csv(path, index=False)
            time.sleep(pause)
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    duplicate_count = combined.duplicated(["GAME_ID", "GAME_EVENT_ID"]).sum()
    if duplicate_count:
        raise RuntimeError(
            f"{season}: found {duplicate_count} duplicate GAME_ID/GAME_EVENT_ID rows"
        )
    return combined


def download_bio(season: str, raw_dir: Path, pause: float) -> pd.DataFrame:
    path = raw_dir / "bio" / f"bio_{season}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return pd.read_csv(path, low_memory=False)

    print(f"Bio {season}: downloading")
    response = request_with_retries(
        lambda: leaguedashplayerbiostats.LeagueDashPlayerBioStats(
            season=season,
            season_type_all_star="Regular Season",
            per_mode_simple="Totals",
            timeout=90,
        )
    )
    frame = response.league_dash_player_bio_stats.get_data_frame()
    frame["SEASON"] = season
    frame.to_csv(path, index=False)
    time.sleep(pause)
    return frame


def download_rosters(season: str, raw_dir: Path, pause: float) -> pd.DataFrame:
    season_dir = raw_dir / "rosters" / season
    season_dir.mkdir(parents=True, exist_ok=True)
    frames = []

    for number, team in enumerate(teams.get_teams(), start=1):
        team_id = int(team["id"])
        abbreviation = str(team["abbreviation"])
        path = season_dir / f"roster_{season}_{abbreviation}.csv"
        if path.exists() and path.stat().st_size > 0:
            frame = pd.read_csv(path, low_memory=False)
        else:
            print(f"Roster {season} [{number:02d}/30] {abbreviation}: downloading")
            response = request_with_retries(
                lambda: commonteamroster.CommonTeamRoster(
                    team_id=team_id, season=season, timeout=90
                )
            )
            frame = response.common_team_roster.get_data_frame()
            frame["TEAM_ID_LOOKUP"] = team_id
            frame["SEASON"] = season
            frame.to_csv(path, index=False)
            time.sleep(pause)
        if "TEAM_ID_LOOKUP" not in frame.columns:
            frame["TEAM_ID_LOOKUP"] = team_id
        frame["SEASON"] = season
        frames.append(frame)

    return pd.concat(frames, ignore_index=True)


def add_player_info_position_fallbacks(
    rosters: pd.DataFrame,
    shot_player_ids: pd.Series,
    raw_dir: Path,
    pause: float,
) -> pd.DataFrame:
    known_ids = set(rosters.loc[rosters["POSITION"].notna(), "PLAYER_ID"])
    missing_ids = sorted(set(shot_player_ids.dropna().astype(int)) - known_ids)
    fallback_dir = raw_dir / "player_info"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    fallback_rows = []

    for number, player_id in enumerate(missing_ids, start=1):
        path = fallback_dir / f"player_{player_id}.csv"
        if path.exists() and path.stat().st_size > 0:
            info = pd.read_csv(path, low_memory=False)
        else:
            print(
                f"Player position fallback [{number}/{len(missing_ids)}] {player_id}"
            )
            response = request_with_retries(
                lambda: commonplayerinfo.CommonPlayerInfo(
                    player_id=player_id, timeout=90
                )
            )
            info = response.common_player_info.get_data_frame()
            info.to_csv(path, index=False)
            time.sleep(pause)
        if len(info) and pd.notna(info.iloc[0].get("POSITION")):
            fallback_rows.append(
                {
                    "PLAYER_ID": player_id,
                    "POSITION": info.iloc[0]["POSITION"],
                    "TEAM_ID_LOOKUP": 0,
                }
            )

    if fallback_rows:
        rosters = pd.concat([rosters, pd.DataFrame(fallback_rows)], ignore_index=True)
    return rosters


def roster_lookup(rosters: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    lookup = rosters[["PLAYER_ID", "TEAM_ID_LOOKUP", "POSITION"]].copy()
    lookup["POSITION"] = lookup["POSITION"].map(normalize_position)
    lookup = lookup.dropna(subset=["POSITION"])
    by_team = (
        lookup.groupby(["PLAYER_ID", "TEAM_ID_LOOKUP"])["POSITION"]
        .agg(lambda values: values.mode().iloc[0])
        .reset_index()
        .rename(columns={"TEAM_ID_LOOKUP": "TEAM_ID"})
    )
    by_player = (
        lookup.groupby("PLAYER_ID")["POSITION"]
        .agg(lambda values: values.mode().iloc[0])
        .reset_index()
        .rename(columns={"POSITION": "POSITION_PLAYER_FALLBACK"})
    )
    return by_team, by_player


def prepare_season(
    shots: pd.DataFrame,
    bio: pd.DataFrame,
    rosters: pd.DataFrame,
    season: str,
    max_shot_distance: float | None,
) -> pd.DataFrame:
    shots = shots.copy()
    if max_shot_distance is not None:
        shots = shots.loc[shots["SHOT_DISTANCE"] <= max_shot_distance].copy()

    bio_columns = [
        "PLAYER_ID", "TEAM_ID", "PLAYER_HEIGHT", "PLAYER_HEIGHT_INCHES",
        "PLAYER_WEIGHT", "AGE", "COUNTRY", "COLLEGE", "DRAFT_YEAR",
        "DRAFT_ROUND", "DRAFT_NUMBER",
    ]
    bio = bio[bio_columns].drop_duplicates(["PLAYER_ID", "TEAM_ID"])
    bio_fallback = (
        bio.drop(columns="TEAM_ID")
        .drop_duplicates("PLAYER_ID", keep="last")
        .add_suffix("_FALLBACK")
        .rename(columns={"PLAYER_ID_FALLBACK": "PLAYER_ID"})
    )

    merged = shots.merge(
        bio, on=["PLAYER_ID", "TEAM_ID"], how="left", validate="many_to_one"
    ).merge(bio_fallback, on="PLAYER_ID", how="left", validate="many_to_one")
    for column in bio_columns[2:]:
        merged[column] = merged[column].fillna(merged[f"{column}_FALLBACK"])

    positions_by_team, positions_by_player = roster_lookup(rosters)
    merged = merged.merge(
        positions_by_team,
        on=["PLAYER_ID", "TEAM_ID"],
        how="left",
        validate="many_to_one",
    ).merge(
        positions_by_player,
        on="PLAYER_ID",
        how="left",
        validate="many_to_one",
    )
    merged["POSITION"] = merged["POSITION"].fillna(
        merged["POSITION_PLAYER_FALLBACK"]
    )
    merged["POSITION_GROUP"] = merged["POSITION"].map(position_group)

    zone_parts = merged["SHOT_ZONE_AREA"].str.extract(
        r"^(?P<ZONE_NAME>.*?)(?:\((?P<ZONE_ABB>[^()]*)\))?$"
    )
    date = pd.to_datetime(merged["GAME_DATE"].astype(str), format="%Y%m%d")

    output = pd.DataFrame(index=merged.index)
    output["SEASON_1"] = int(season[:4]) + 1
    output["SEASON_2"] = season
    output["TEAM_ID"] = merged["TEAM_ID"]
    output["TEAM_NAME"] = merged["TEAM_NAME"]
    output["PLAYER_ID"] = merged["PLAYER_ID"]
    output["PLAYER_NAME"] = merged["PLAYER_NAME"]
    output["POSITION_GROUP"] = merged["POSITION_GROUP"]
    output["POSITION"] = merged["POSITION"]
    output["GAME_DATE"] = date.dt.strftime("%m-%d-%Y")
    output["GAME_ID"] = merged["GAME_ID"]
    output["GAME_EVENT_ID"] = merged["GAME_EVENT_ID"]
    output["HOME_TEAM"] = merged["HTM"]
    output["AWAY_TEAM"] = merged["VTM"]
    output["EVENT_TYPE"] = merged["EVENT_TYPE"]
    output["SHOT_MADE"] = merged["SHOT_MADE_FLAG"].astype(bool)
    output["ACTION_TYPE"] = merged["ACTION_TYPE"]
    output["SHOT_TYPE"] = merged["SHOT_TYPE"]
    output["BASIC_ZONE"] = merged["SHOT_ZONE_BASIC"]
    output["ZONE_NAME"] = zone_parts["ZONE_NAME"].str.strip()
    output["ZONE_ABB"] = zone_parts["ZONE_ABB"].str.strip()
    output["ZONE_RANGE"] = merged["SHOT_ZONE_RANGE"]
    output["LOC_X"] = -merged["LOC_X"] / 10.0
    output["LOC_Y"] = (merged["LOC_Y"] + 52.5) / 10.0
    output["SHOT_DISTANCE"] = merged["SHOT_DISTANCE"]
    output["QUARTER"] = merged["PERIOD"]
    output["MINS_LEFT"] = merged["MINUTES_REMAINING"]
    output["SECS_LEFT"] = merged["SECONDS_REMAINING"]
    output["SEASON"] = season
    output["MADE"] = merged["SHOT_MADE_FLAG"].astype(int)
    output["SECONDS_LEFT_IN_PERIOD"] = (
        merged["MINUTES_REMAINING"] * 60 + merged["SECONDS_REMAINING"]
    )
    output["IS_THREE"] = merged["SHOT_TYPE"].eq("3PT Field Goal").astype(int)
    for column in [
        "PLAYER_HEIGHT", "PLAYER_HEIGHT_INCHES", "PLAYER_WEIGHT", "AGE",
        "COUNTRY", "COLLEGE", "DRAFT_YEAR", "DRAFT_ROUND", "DRAFT_NUMBER",
    ]:
        output[column] = merged[column]
    for column in [
        "PLAYER_HEIGHT_INCHES", "PLAYER_WEIGHT", "AGE", "DRAFT_YEAR",
        "DRAFT_ROUND", "DRAFT_NUMBER",
    ]:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    return output[OUTPUT_COLUMNS]


def validate(frame: pd.DataFrame, seasons: list[str], name: str) -> None:
    if frame.columns.tolist() != OUTPUT_COLUMNS:
        raise RuntimeError(f"{name}: unexpected column order")
    if not set(frame["SEASON"].unique()).issubset(set(seasons)):
        raise RuntimeError(f"{name}: unexpected season")
    if not frame["MADE"].isin([0, 1]).all():
        raise RuntimeError(f"{name}: MADE must contain only 0/1")
    if not frame["IS_THREE"].isin([0, 1]).all():
        raise RuntimeError(f"{name}: IS_THREE must contain only 0/1")
    if not (
        frame["SECONDS_LEFT_IN_PERIOD"]
        == frame["MINS_LEFT"] * 60 + frame["SECS_LEFT"]
    ).all():
        raise RuntimeError(f"{name}: inconsistent time fields")
    print(f"{name}: {len(frame):,} rows")
    print(f"{name} seasons:\n{frame['SEASON'].value_counts().sort_index()}")
    print(f"{name} POSITION missing: {frame['POSITION'].isna().mean():.2%}")
    print(f"{name} POSITION_GROUP missing: {frame['POSITION_GROUP'].isna().mean():.2%}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", nargs="+", required=True)
    parser.add_argument("--test-season", required=True)
    parser.add_argument("--data-dir", default="nba_project_data")
    parser.add_argument("--pause", type=float, default=1.5)
    parser.add_argument(
        "--max-shot-distance",
        type=float,
        default=None,
        help="Optional compatibility filter. Leave unset to keep legitimate heaves.",
    )
    args = parser.parse_args()
    if args.test_season not in args.seasons:
        raise SystemExit("--test-season must also appear in --seasons")

    root = Path(args.data_dir)
    raw_dir = root / "raw"
    processed_dir = root / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_frames = []

    for season in args.seasons:
        shots = download_shots(season, raw_dir, args.pause)
        bio = download_bio(season, raw_dir, args.pause)
        rosters = download_rosters(season, raw_dir, args.pause)
        rosters = add_player_info_position_fallbacks(
            rosters, shots["PLAYER_ID"], raw_dir, args.pause
        )
        season_frame = prepare_season(
            shots, bio, rosters, season, args.max_shot_distance
        )
        season_path = processed_dir / f"shots_with_player_bio_{season}.csv"
        season_frame.to_csv(season_path, index=False)
        processed_frames.append(season_frame)

    all_data = pd.concat(processed_frames, ignore_index=True)
    train = all_data.loc[all_data["SEASON"] != args.test_season].copy()
    test = all_data.loc[all_data["SEASON"] == args.test_season].copy()
    validate(train, [s for s in args.seasons if s != args.test_season], "TRAIN")
    validate(test, [args.test_season], "TEST")

    train_path = processed_dir / "train_with_player_bio.csv"
    test_path = processed_dir / "test_with_player_bio.csv"
    train.to_csv(train_path, index=False)
    test.to_csv(test_path, index=False)
    print(f"Saved: {train_path.resolve()}")
    print(f"Saved: {test_path.resolve()}")


if __name__ == "__main__":
    main()

# terminal enter this： /usr/local/bin/python3 -u build_nba_shot_dataset.py \
#                       --seasons 2021-22 2022-23 2023-24 2024-25 2025-26 \
#                       --test-season 2025-26 \
#                       --data-dir nba_project_data