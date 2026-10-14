"""ESPN の公開データから試合・順位表・スタメン・選手一覧を集めて data/ に JSON で保存する。

使い方:
  python3 scripts/fetch.py            # 試合一覧・順位表・終わった試合の詳細を更新
  python3 scripts/fetch.py --rosters  # 上に加えて各チームの所属選手も更新（毎日0時用）
"""
import glob
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
WEB = "https://site.web.api.espn.com/apis/v2/sports/soccer"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
NAMES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "names")

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


def wikidata_ja(names):
    """英語名から Wikidata の日本語名を探す（サッカー選手に限定し、表示名と同じ長さのものだけ採用）。"""
    found = {}
    names = [n for n in names if n and '"' not in n and "\\" not in n]
    for i in range(0, len(names), 200):
        chunk = names[i:i + 200]
        q = 'SELECT ?en ?ja WHERE { VALUES ?en { %s } ?p rdfs:label ?en; wdt:P106 wd:Q937857; ' \
            'rdfs:label ?ja FILTER(lang(?ja)="ja") }' % " ".join('"%s"@en' % n for n in chunk)
        req = urllib.request.Request(
            "https://query.wikidata.org/sparql", data=urllib.parse.urlencode({"query": q}).encode(),
            headers={"Accept": "application/sparql-results+json", "User-Agent": "soccer-app/1.0 (github.com/gtkg07-lab/soccer)"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                rows = json.load(r)["results"]["bindings"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            print("  Wikidata 失敗:", e, file=sys.stderr)
            continue
        for b in rows:
            en, ja = b["en"]["value"], re.sub(r"\s*[(（].*?[)）]\s*$", "", b["ja"]["value"]).strip()
            if re.fullmatch(r"[ァ-ヴー・＝=\s]+", ja) and len(en.split()) == len(re.split("[・ ]", ja)):
                found.setdefault(en, ja.replace("=", "＝"))
        time.sleep(1)
    return found


def build_names(lookup_new):
    """names/ の辞書をまとめて data/names_ja.json にする。lookup_new なら未訳の選手を Wikidata で探す。"""
    def read(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    teams = {k: v for k, v in read(os.path.join(NAMES, "teams_ja.json")).items() if not k.startswith("_")}
    players = {}
    for path in sorted(glob.glob(os.path.join(NAMES, "players_ja_*.json"))):
        players.update(read(path))
    if lookup_new:
        seen = set()
        for path in glob.glob(os.path.join(ROOT, "t", "*.json")):
            seen.update(p["n"] for p in read(path)["players"])
        for path in glob.glob(os.path.join(ROOT, "m", "*.json")):
            for side in read(path).get("lineups", {}).values():
                seen.update(p["n"] for p in side["players"])
        missing = sorted(n for n in seen if n and n not in players)
        if missing:
            auto_path = os.path.join(NAMES, "players_ja_auto.json")
            auto = read(auto_path) if os.path.exists(auto_path) else {}
            auto.update(wikidata_ja(missing))
            with open(auto_path, "w", encoding="utf-8") as f:
                json.dump(auto, f, ensure_ascii=False, indent=0, sort_keys=True)
            players.update(auto)
            print("  未訳の選手:", len(missing), "人 → Wikidataで", len([n for n in missing if n in auto]), "人見つかった")
    save("names_ja.json", {"teams": teams, "players": players,
                           "countries": read(os.path.join(NAMES, "countries_ja.json"))})


YT_DAILY_LIMIT = 95   # YouTube Data API の無料枠は1日10,000ユニット＝検索100回
YT_PER_RUN = 8        # 15分ごとの1回の実行で使う検索回数の上限
LONG_WORDS = re.compile(r"extended|long|ロング|拡大", re.I)
HL_WORDS = re.compile(r"highlight|ハイライト|resumen|résumé|resume|summary|extended|sintesi|samenvatting|melhores momentos", re.I)
SKIP_WORDS = re.compile(r"full match|full game|フルマッチ|live|ライブ|en directo|preview|プレビュー|reaction|fifa|efootball|pes |#shorts", re.I)
# 公式とみなすチャンネル（リーグ・大会・放送局）。これ以外はクラブ公式（チャンネル名にチーム名を含む）のみ採用
OFFICIAL_CHANNELS = ("premier league", "laliga", "ligue 1", "uefa", "champions league", "wowow", "dazn", "u-next", "unext",
                     "sky sports", "tnt sports", "cbs sports", "bein", "canal+", "nbc sports", "espn", "movistar",
                     "prime video", "abema", "sport tv", "rtve", "tf1", "bbc sport", "itv")
FAN_WORDS = re.compile(r"fan|news|talk|daily|all about|nation|tv\b|zone|world|hub|insider|addict", re.I)


def norm(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s or "").lower() if not unicodedata.combining(c))


def team_words(team, ja):
    """動画タイトルにそのチームが入っているか判定するための単語（英語名の4文字以上の語・日本語の短縮名）。"""
    words = {w for w in re.split(r"[\s\-./&]+", norm(team.get("n", "")) + " " + norm(team.get("s", ""))) if len(w) >= 4}
    words -= {"club", "united", "city", "real", "sporting", "athletic", "atletico", "saint", "paris"} if len(words) > 1 else set()
    if len(team.get("ab", "")) >= 3:
        words.add(norm(team["ab"]))
    if ja:
        words |= {norm(ja[0]), norm(ja[1])}
    return words


def is_club_channel(ch, team, ja):
    """チャンネル名がチーム名で始まり、ファン運営らしい言葉を含まなければクラブ公式とみなす。"""
    heads = [norm(team.get("n", "")), norm(team.get("s", "")), norm(team.get("ab", ""))] + [norm(x) for x in (ja or [])]
    ch = re.sub(r"^(fc|afc|cf|as|rc|ss|sl|sc|ac|ud|cd|sv|vfb|ogc|1\.)\s+", "", ch)
    return any(len(x) >= 3 and ch.startswith(x) for x in heads) and not FAN_WORDS.search(ch)


def update_highlights(teams):
    """終わった試合のハイライト動画を YouTube で探して data/highlights.json に保存する。"""
    key = os.environ.get("YT_API_KEY")
    if not key:
        return
    hl = load("highlights.json", {})
    # 無料枠は太平洋時間の0時にリセットされる
    day = (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")
    quota = hl.get("_quota", {})
    if quota.get("day") != day:
        quota = {"day": day, "used": 0}
    budget = min(YT_PER_RUN, YT_DAILY_LIMIT - quota["used"])
    if budget <= 0:
        return
    names_ja = load("names_ja.json", {}).get("teams", {})
    now = datetime.now(timezone.utc)
    cands = []
    for comp in COMPETITIONS:
        for path in glob.glob(os.path.join(ROOT, comp["id"], "*", "matches.json")):
            for m in load(os.path.relpath(path, ROOT), []):
                if m["s"] != "post" or not m.get("h") or not m.get("a"):
                    continue
                ko = datetime.strptime(m["d"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                if now - ko < timedelta(hours=3):
                    continue
                e = hl.get(m["id"], {})
                if e.get("h") and e.get("l"):
                    continue
                tries = e.get("n", 0)
                if tries >= 3 or (e.get("c") and now - datetime.fromisoformat(e["c"]) < timedelta(hours=6 * tries)):
                    continue
                cands.append((ko, m))
    cands.sort(key=lambda x: x[0], reverse=True)  # 新しい試合から
    for ko, m in cands[:budget]:
        th, ta = teams.get(m["h"]["id"], {}), teams.get(m["a"]["id"], {})
        jh, ja_ = names_ja.get(m["h"]["id"]), names_ja.get(m["a"]["id"])
        # 1回目は英語、2回目は日本語（WOWOW・DAZN・U-NEXT など日本の放送局向け）で探す
        if hl.get(m["id"], {}).get("n", 0) % 2 == 1 and jh and ja_:
            query = "%s vs %s ハイライト" % (jh[0], ja_[0])
        else:
            query = "%s vs %s highlights" % (th.get("n", ""), ta.get("n", ""))
        params = {
            "part": "snippet", "type": "video", "maxResults": 15, "regionCode": "JP", "key": key,
            "q": query,
            "publishedAfter": ko.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "publishedBefore": (ko + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        d = get("https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params), retries=1)
        quota["used"] += 1
        if d is None:
            break  # 枠切れなどのエラー。今回はここまで
        wh, wa = team_words(th, jh), team_words(ta, ja_)
        e = dict(hl.get(m["id"], {}), c=now.isoformat(timespec="seconds"), n=hl.get(m["id"], {}).get("n", 0) + 1)
        picks = {}
        for it in d.get("items", []):
            title, ch = html.unescape(it["snippet"]["title"]), html.unescape(it["snippet"]["channelTitle"])
            t, c = norm(title), norm(ch)
            if SKIP_WORDS.search(title) or not HL_WORDS.search(title):
                continue
            if not (any(w in t for w in wh) and any(w in t for w in wa)):
                continue
            # 放送局・リーグの公式を優先（0）、次にクラブ公式（1）。それ以外は採用しない
            rank = 0 if any(o in c for o in OFFICIAL_CHANNELS) else \
                1 if is_club_channel(c, th, jh) or is_club_channel(c, ta, ja_) else None
            if rank is None:
                continue
            kind = "l" if LONG_WORDS.search(title) else "h"
            if kind not in picks or rank < picks[kind][0]:
                picks[kind] = (rank, {"id": it["id"]["videoId"], "t": title, "ch": ch})
        for kind, (_, vid) in picks.items():
            e.setdefault(kind, vid)
        hl[m["id"]] = e
    hl["_quota"] = quota
    save("highlights.json", hl)
    print("  YouTube 検索:", quota["used"], "/", YT_DAILY_LIMIT, "（本日分）")


AF = "https://v3.football.api-sports.io"
AF_DAILY_LIMIT = 90   # API-Football 無料プランは1日100回
AF_LEAGUES = {"premier": 39, "laliga": 140, "ligue1": 61, "ucl": 2, "unl": 5}


def af_get(path, key):
    req = urllib.request.Request(AF + path, headers={"x-apisports-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print("  API-Football 失敗:", path, e, file=sys.stderr)
        return None
    if d.get("errors"):
        print("  API-Football エラー:", path, d["errors"], file=sys.stderr)
        return None
    time.sleep(6.5)  # 無料プランは1分間に10回まで
    return d.get("response", [])


def surname(name):
    parts = [p for p in re.split(r"[\s.]+", norm(name).strip()) if p]
    return parts[-1] if parts else ""


def update_photos(teams):
    """API-Football の所属選手データから顔写真を探し、ESPN の選手と背番号・名字で結びつけて data/photos.json に保存する。"""
    key = os.environ.get("AF_KEY")
    if not key:
        return
    ph = load("photos.json", {"teams": {}, "players": {}})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quota = ph.get("_quota", {})
    if quota.get("day") != today:
        quota = {"day": today, "used": 0}

    def call(path):
        if quota["used"] >= AF_DAILY_LIMIT:
            return None
        quota["used"] += 1
        return af_get(path, key)

    # ① ESPN のチームを API-Football のチームIDに対応づける（無料プランで見られる2024年の大会参加チームから）
    espn_ids = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(ROOT, "t", "*.json")))
    unmatched = [i for i in espn_ids if i not in ph["teams"]]
    if unmatched and not ph.get("_leagues_done"):
        for lid in AF_LEAGUES.values():
            for r in call("/teams?league=%d&season=2024" % lid) or []:
                ph.setdefault("_af_teams", {})[str(r["team"]["id"])] = r["team"]["name"]
        ph["_leagues_done"] = True
    with open(os.path.join(NAMES, "af_teams.json"), encoding="utf-8") as f:
        fixed = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    for tid, af_id in fixed.items():  # 手で指定した対応を優先
        if ph["teams"].get(tid, {}).get("af") != af_id:
            ph["teams"][tid] = {"af": af_id, "checked": ""}
    unmatched = [i for i in espn_ids if i not in ph["teams"]]
    national = {m[k]["id"] for p in glob.glob(os.path.join(ROOT, "unl", "*", "matches.json"))
                for m in load(os.path.relpath(p, ROOT), []) for k in ("h", "a") if m.get(k)}
    by_name = {norm(n): i for i, n in ph.get("_af_teams", {}).items()}
    for tid in unmatched:
        t = teams.get(tid, {})
        cand = [c for c in (norm(t.get("n", "")), norm(t.get("s", ""))) if c]
        af_id = next((by_name[c] for c in cand if c in by_name), None)
        if af_id is None:
            af_id = next((i for n, i in by_name.items() if any(c in n or n in c for c in cand)), None)
        if af_id is None:
            # 正式名 → 短縮名の順に検索。女子・ユースを除き、代表とクラブの区別も合うものだけ使う
            res = None
            for word in dict.fromkeys(re.sub(r"[^a-z ]", " ", c).strip()[:25] for c in cand):
                if len(word) < 3:
                    continue
                res = call("/teams?search=%s" % urllib.parse.quote(re.sub(r"\s+", " ", word)))
                if res is None:
                    break
                res = [r for r in res if not re.search(r" W$| U\d\d|women|femin|reserve| II$| B$", r["team"]["name"], re.I)
                       and bool(r["team"].get("national")) == (tid in national)]
                if res:
                    break
            if res is None:
                continue
            af_id = str(res[0]["team"]["id"]) if res else ""
            if res:
                ph.setdefault("_af_teams", {})[af_id] = res[0]["team"]["name"]
        ph["teams"][tid] = {"af": af_id, "checked": ""}

    # ② 確認が古い順にチームの所属選手（写真付き）を取り、ESPN の選手一覧と照合する
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    for checked, tid in sorted((v["checked"], k) for k, v in ph["teams"].items() if v.get("af")):
        if checked and checked > week_ago:
            continue  # 1週間以内に確認済み
        res = call("/players/squads?team=%s" % ph["teams"][tid]["af"])
        if res is None:
            break
        squad = res[0]["players"] if res else []
        for p in load("t/%s.json" % tid, {}).get("players", []):
            sn = surname(p["n"])
            same_num = [s for s in squad if p.get("num") and str(s.get("number")) == str(p["num"])]
            hit = next((s for s in same_num if surname(s["name"]) == sn or norm(s["name"]) in norm(p["n"])), None)
            if hit is None:
                by_sn = [s for s in squad if surname(s["name"]) == sn]
                hit = by_sn[0] if len(by_sn) == 1 else None
            if hit and hit.get("photo"):
                ph["players"][p["n"]] = hit["photo"]
        ph["teams"][tid]["checked"] = today
    ph["_quota"] = quota
    save("photos.json", ph)
    print("  顔写真:", len(ph["players"]), "人 / API-Football 本日", quota["used"], "回")


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
    build_names(lookup_new=with_rosters)
    update_highlights(teams)
    if with_rosters:
        update_photos(teams)
    # 中身が変わったときだけ更新時刻を進める（変化のない自動実行で記録が増えないように）
    old = load("index.json", {})
    index["updated"] = old.get("updated") if (old.get("comps") == index["comps"] and not changed) else \
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    save("index.json", index)
    print("完了 通信回数:", requests_made)


if __name__ == "__main__":
    main()
