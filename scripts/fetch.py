"""ESPN の公開データから試合・順位表・スタメン・選手一覧を集めて data/ に JSON で保存する。

使い方:
  python3 scripts/fetch.py            # 試合一覧・順位表・終わった試合の詳細を更新
  python3 scripts/fetch.py --rosters  # 上に加えて各チームの所属選手も更新（毎日0時用）
"""
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
WEB = "https://site.web.api.espn.com/apis/v2/sports/soccer"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# first: 最初に表示するシーズン（ESPN の season.year。2025 = 25/26）
COMPETITIONS = [
    {"id": "premier", "slug": "eng.1", "name": "プレミアリーグ", "type": "league", "first": 2025},
    {"id": "laliga", "slug": "esp.1", "name": "ラ・リーガ", "type": "league", "first": 2025},
    {"id": "ligue1", "slug": "fra.1", "name": "リーグ・アン", "type": "league", "first": 2025},
    {"id": "ucl", "slug": "uefa.champions", "name": "チャンピオンズリーグ", "type": "ucl", "first": 2025},
    {"id": "unl", "slug": "uefa.nations", "name": "UEFAネーションズリーグ", "type": "unl", "first": 2024},
]

KEY_EVENT_TYPES = {
    "goal": "goal", "goal---header": "goal", "goal---free-kick": "goal", "goal---volley": "goal",
    "penalty---scored": "pen", "own-goal": "og",
    "yellow-card": "yc", "red-card": "rc", "second-yellow-card": "rc2",
    "substitution": "sub",
}

requests_made = 0
changed = False


def get(url, retries=3):
    global requests_made
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            requests_made += 1
            time.sleep(0.15)
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if i == retries - 1:
                print("  失敗:", url, e, file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))


def load(path, default=None):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save(path, data):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    try:
        with open(full, encoding="utf-8") as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    global changed
    changed = True


def season_label(comp, year):
    if comp["type"] == "unl":
        return "%d-%02d" % (year, (year + 1) % 100)
    return "%02d/%02d" % (year % 100, (year + 1) % 100)


def stage_of(comp, event):
    slug = event.get("season", {}).get("slug", "")
    if comp["type"] == "league":
        return "league"
    return "league-phase" if slug == "group-stage" else slug  # 2024-25 のネーションズリーグは呼び方が違う


def remember_team(teams, t):
    teams[t["id"]] = {
        "n": t.get("displayName") or t.get("name"),
        "s": t.get("shortDisplayName") or t.get("displayName"),
        "ab": t.get("abbreviation", ""),
        "logo": t.get("logo") or (t.get("logos") or [{}])[0].get("href", ""),
        "c": t.get("color", ""),
    }


def slim_event(comp, e, teams):
    c = e["competitions"][0]
    st = e["status"]["type"]
    side = {}
    for x in c["competitors"]:
        remember_team(teams, x["team"])
        s = {"id": x["team"]["id"], "sc": x.get("score"), "w": bool(x.get("winner"))}
        if x.get("shootoutScore") is not None:
            s["pen"] = x["shootoutScore"]
        side[x["homeAway"]] = s
    m = {
        "id": e["id"], "d": e["date"], "st": stage_of(comp, e),
        "s": st["state"], "n": st["name"], "sd": st.get("shortDetail", ""),
        "h": side.get("home"), "a": side.get("away"),
    }
    if c.get("group"):
        m["g"] = c["group"]["name"]
    if c.get("leg"):
        m["leg"] = c["leg"]["value"]
    series = c.get("series") or {}
    if series.get("completed"):
        winner = [x["id"] for x in series.get("competitors", []) if x.get("winner")]
        if winner:
            m["adv"] = winner[0]
    return m


def fetch_events(comp, teams):
    """年単位で試合を取得し、シーズンごとに分ける。"""
    by_season = {}
    this_year = datetime.now(timezone.utc).year
    for y in range(comp["first"], this_year + 2):
        d = get("%s/%s/scoreboard?dates=%d&limit=1000" % (SITE, comp["slug"], y))
        for lg in (d or {}).get("leagues", [])[:1]:
            logos = lg.get("logos") or []
            if logos:
                comp["logo"] = logos[0].get("href", "")
        for e in (d or {}).get("events", []):
            sy = e.get("season", {}).get("year")
            if sy is None or sy < comp["first"]:
                continue
            by_season.setdefault(sy, {})[e["id"]] = slim_event(comp, e, teams)
    return by_season


def fetch_standings(comp, year):
    d = get("%s/%s/standings?season=%d" % (WEB, comp["slug"], year))
    groups = []
    for ch in (d or {}).get("children", []):
        rows = []
        for en in ch.get("standings", {}).get("entries", []):
            v = {s["name"]: s.get("displayValue") for s in en.get("stats", [])}
            row = {
                "id": en["team"]["id"], "r": v.get("rank"), "p": v.get("gamesPlayed"),
                "w": v.get("wins"), "d": v.get("ties"), "l": v.get("losses"),
                "gf": v.get("pointsFor"), "ga": v.get("pointsAgainst"),
                "gd": v.get("pointDifferential"), "pts": v.get("points"),
            }
            note = en.get("note")
            if note:
                row["note"] = {"c": "#" + note.get("color", "").lstrip("#"), "t": note.get("description", "")}
            rows.append(row)
        groups.append({"name": ch.get("name", ""), "rows": rows})
    return groups


def slim_summary(comp, year, m, d):
    out = {"id": m["id"], "comp": comp["id"], "y": year, "final": m["s"] == "post"}
    info = d.get("gameInfo", {})
    out["venue"] = (info.get("venue") or {}).get("fullName", "")
    out["att"] = info.get("attendance")
    sides = {}
    for r in d.get("rosters", []):
        players = []
        for p in r.get("roster", []):
            a = p.get("athlete", {})
            pl = {
                "n": a.get("displayName"), "num": p.get("jersey"),
                "pos": (p.get("position") or {}).get("abbreviation", ""),
                "st": bool(p.get("starter")), "fp": p.get("formationPlace"),
            }
            if p.get("subbedIn"):
                pl["in"] = True
            if p.get("subbedOut"):
                pl["out"] = True
            players.append(pl)
        sides[r.get("homeAway")] = {"form": r.get("formation", ""), "players": players}
    out["lineups"] = sides
    stats = {}
    for t in d.get("boxscore", {}).get("teams", []):
        ha = t.get("homeAway") or ("home" if t["team"]["id"] == m["h"]["id"] else "away")
        for s in t.get("statistics", []):
            stats.setdefault(s["name"], {"label": s.get("label", s["name"])})[ha] = s.get("displayValue")
    out["stats"] = stats
    evs = []
    for k in d.get("keyEvents", []):
        kind = KEY_EVENT_TYPES.get(k.get("type", {}).get("type"))
        if not kind or k.get("shootout"):
            continue
        names = [p["athlete"]["displayName"] for p in k.get("participants", []) if p.get("athlete")]
        evs.append({
            "t": kind, "min": k.get("clock", {}).get("displayValue", ""),
            "team": (k.get("team") or {}).get("id"), "p": names,
        })
    out["events"] = evs
    return out


def fetch_roster(comp, team_id, teams):
    d = get("%s/%s/teams/%s/roster" % (SITE, comp["slug"], team_id))
    if not d or not d.get("athletes"):
        return None
    players = []
    for a in d["athletes"]:
        players.append({
            "n": a.get("displayName"), "num": a.get("jersey"),
            "pos": (a.get("position") or {}).get("abbreviation", ""),
            "age": a.get("age"), "nat": a.get("citizenship", ""),
        })
    return {"id": team_id, "comp": comp["id"], "players": players,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main():
    with_rosters = "--rosters" in sys.argv
    teams = load("teams.json", {})
    index = {"comps": []}
    for comp in COMPETITIONS:
        print("■", comp["name"])
        by_season = fetch_events(comp, teams)
        seasons = []
        for year in sorted(by_season):
            matches = sorted(by_season[year].values(), key=lambda m: m["d"])
            save("%s/%d/matches.json" % (comp["id"], year), matches)
            save("%s/%d/standings.json" % (comp["id"], year), fetch_standings(comp, year))
            stages = []
            for m in matches:
                if m["st"] not in stages:
                    stages.append(m["st"])
            seasons.append({"y": year, "label": season_label(comp, year), "stages": stages,
                            "count": len(matches), "done": sum(1 for m in matches if m["s"] == "post")})

            # 終わった試合・試合中の詳細（スタメン・スタッツ）
            for m in matches:
                if m["s"] == "pre" or m["n"] in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_ABANDONED"):
                    continue
                path = "m/%s.json" % m["id"]
                old = load(path)
                if old and old.get("final"):
                    continue
                d = get("%s/%s/summary?event=%s" % (SITE, comp["slug"], m["id"]))
                if d:
                    save(path, slim_summary(comp, year, m, d))
        print("  シーズン:", [s["label"] for s in seasons], "通信回数:", requests_made)

        if with_rosters and seasons:
            latest = by_season[seasons[-1]["y"]].values()
            ids = sorted({m[k]["id"] for m in latest for k in ("h", "a") if m.get(k)})
            for tid in ids:
                r = fetch_roster(comp, tid, teams)
                if r:
                    save("t/%s.json" % tid, r)
            print("  選手一覧:", len(ids), "チーム")

        index["comps"].append(dict({k: comp.get(k, "") for k in ("id", "name", "type", "slug", "logo")},
                                   seasons=seasons))

    save("teams.json", teams)
    # 中身が変わったときだけ更新時刻を進める（変化のない自動実行で記録が増えないように）
    old = load("index.json", {})
    index["updated"] = old.get("updated") if (old.get("comps") == index["comps"] and not changed) else \
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    save("index.json", index)
    print("完了 通信回数:", requests_made)


if __name__ == "__main__":
    main()
