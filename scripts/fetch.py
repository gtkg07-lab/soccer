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
        except (OSError, ValueError) as e:
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


SUMMARY_VERSION = 2  # 2: 選手ごとの成績・出場時間・簡易評価を追加
STAT_KEYS = {"totalGoals": "g", "goalAssists": "a", "totalShots": "sh", "shotsOnTarget": "sot", "saves": "sv",
             "goalsConceded": "gc", "yellowCards": "yc", "redCards": "rc", "ownGoals": "og",
             "foulsCommitted": "fc", "foulsSuffered": "fs", "offsides": "off"}


def minute_of(clock):
    """"83'" や "90'+4'" を分（整数）にする。"""
    m = re.match(r"(\d+)", clock or "")
    return int(m.group(1)) if m else None


def role_of(pos):
    """ポジションの略号を GK / DF / MF / FW に分ける。"""
    if pos == "G":
        return "G"
    if re.match(r"^(CD|SW|LB|RB|LWB|RWB|D)", pos or ""):
        return "D"
    if re.match(r"^(F|CF|LF|RF|LW|RW|ST|S)", pos or ""):
        return "F"
    return "M"


def rating(pl, role, result, clean):
    """アプリ独自の簡易評価（6.0基準・3.0〜10.0）。ESPN で取れる数字だけで計算する。
    計算式は画面の「評価について」にも載せている。"""
    st, mins = pl.get("s", {}), pl.get("min", 0)
    if mins < 10 and not any(st.get(k) for k in ("g", "a", "rc", "og")):
        return None  # 出場10分未満で目立った記録が無い選手は評価しない
    w = min(mins, 90) / 90  # 出場時間が短いほど、勝敗・無失点などの影響を小さくする
    r = 6.0
    r += 1.0 * st.get("g", 0) + 0.7 * st.get("a", 0)
    r += 0.2 * max(st.get("sot", 0) - st.get("g", 0), 0) + 0.05 * max(st.get("sh", 0) - st.get("sot", 0), 0)
    r += 0.25 * st.get("sv", 0)
    r -= {"G": 0.4, "D": 0.25, "M": 0.1, "F": 0.0}[role] * st.get("gc", 0)
    if clean and mins >= 60:
        r += {"G": 0.8, "D": 0.6, "M": 0.2, "F": 0.0}[role]
    r -= 0.3 * st.get("yc", 0) + 1.5 * st.get("rc", 0) + 1.0 * st.get("og", 0)
    r -= 0.05 * st.get("fc", 0) + 0.05 * st.get("off", 0)
    r += 0.05 * st.get("fs", 0)
    r += {"w": 0.3, "d": 0.0, "l": -0.2}[result] * w
    return round(max(3.0, min(10.0, r)), 1)


def slim_summary(comp, year, m, d):
    out = {"id": m["id"], "comp": comp["id"], "y": year, "final": m["s"] == "post", "v": SUMMARY_VERSION}
    info = d.get("gameInfo", {})
    out["venue"] = (info.get("venue") or {}).get("fullName", "")
    out["att"] = info.get("attendance")
    sides = {}
    period = (d.get("header", {}).get("competitions") or [{}])[0].get("status", {}).get("period", 2)
    full = 120 if (period or 2) > 2 else 90
    for r in d.get("rosters", []):
        players = []
        for p in r.get("roster", []):
            a = p.get("athlete", {})
            pl = {
                "n": a.get("displayName"), "num": p.get("jersey"),
                "pos": (p.get("position") or {}).get("abbreviation", ""),
                "st": bool(p.get("starter")), "fp": p.get("formationPlace"),
            }
            if a.get("id"):
                pl["id"] = a["id"]
            if p.get("subbedIn"):
                pl["in"] = True
            if p.get("subbedOut"):
                pl["out"] = True
            st = {STAT_KEYS[x["name"]]: int(x.get("value") or 0) for x in p.get("stats", [])
                  if x.get("name") in STAT_KEYS and x.get("value")}
            if st:
                pl["s"] = st
            # 出場時間：交代の時刻から計算（延長戦なら120分）
            subs = [minute_of(x.get("clock", {}).get("displayValue")) for x in p.get("plays", []) if x.get("substitution")]
            subs = [x for x in subs if x is not None]
            if pl["st"]:
                pl["min"] = min(subs[0], full) if p.get("subbedOut") and subs else full
            elif p.get("subbedIn") and subs:
                pl["min"] = max(full - subs[0], 1) if not p.get("subbedOut") or len(subs) < 2 else subs[1] - subs[0]
            players.append(pl)
        sides[r.get("homeAway")] = {"form": r.get("formation", ""), "players": players}
    out["lineups"] = sides
    # 簡易評価（終わった試合のみ）
    if m["s"] == "post" and m.get("h") and m.get("a"):
        hs, as_ = to_int(m["h"].get("sc")) or 0, to_int(m["a"].get("sc")) or 0
        for ha, mine, opp in (("home", hs, as_), ("away", as_, hs)):
            res = "w" if mine > opp else "l" if mine < opp else "d"
            for pl in sides.get(ha, {}).get("players", []):
                if pl.get("min"):
                    r = rating(pl, role_of(pl["pos"] if pl["st"] else ""), res, opp == 0)
                    if r is not None:
                        pl["r"] = r
        rated = [pl for side in sides.values() for pl in side["players"] if pl.get("r") is not None]
        if rated:
            max(rated, key=lambda pl: pl["r"])["motm"] = True
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
            "id": a.get("id"), "n": a.get("displayName"), "num": a.get("jersey"),
            "pos": (a.get("position") or {}).get("abbreviation", ""),
            "age": a.get("age"), "nat": a.get("citizenship", ""), "dob": (a.get("dateOfBirth") or "")[:10],
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
        except (OSError, ValueError, KeyError) as e:
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


# 日本の放送局の公式チャンネル（ここの動画を最優先で使う）
JP_CHANNELS = {
    "UCJQj2lbG_3w8UrncJd7JZXw": "WOWOWサッカー official",  # チャンピオンズリーグ
    "UCoFLB_Gw_AoxUuuzKjXrc_Q": "DAZN Japan",              # ラ・リーガ・リーグアン・ネーションズリーグ
    "UCMjvvElkdLRTgcTKklAUkSw": "U-NEXT フットボール",      # プレミアリーグ・ラ・リーガ
}
JP_SKIP = re.compile(r"ピッチレベル|ゴール集|プレビュー|インタビュー|会見|LIVE|ライブ|配信|見どころ|予想|デイリー|#shorts"
                     r"|女子|WSL|Women|ユース|UYL|U-?\d\d", re.I)  # 女子・ユースは同名クラブがあるので除外
JP_LONG = re.compile(r"ロングハイライト|フルハイライト|拡大版", re.I)
JP_DATE = re.compile(r"(20\d\d)/(\d{1,2})/(\d{1,2})")  # 「2025/11/8」のような試合日
JP_SEASON = re.compile(r"(20\d\d)[-/](\d\d)")  # 「2025-26」「25/26」のようなシーズン表記
JP_LEG = re.compile(r"([12])(?:st|nd)\s*leg", re.I)
# タイトルに入る大会名（試合の大会と一致する動画だけを使う。AFCチャンピオンズリーグなどの取り違え防止）
JP_COMP = {"premier": re.compile(r"プレミアリーグ"), "laliga": re.compile(r"ラ・?リーガ"), "ligue1": re.compile(r"リーグ・?アン"),
           "ucl": re.compile(r"(?<!AFC)(?<!AFC )(UEFA)?チャンピオンズリーグ(?!エリート)|(?<![A-Za-z])CL\s?\d{4}"), "unl": re.compile(r"ネーションズリーグ")}
JP_SEG = re.compile(r"[【】｜|／]")
JP_VS = re.compile(r"(\S+?)\s*(?:\s[vV][sS]?\.?\s|×)\s*(\S+)")


def jp_pairs(title):
    """タイトルから「チームA vs チームB」を取り出す（【】で囲まれていない書き方にも対応）。"""
    out = []
    for seg in JP_SEG.split(title):
        m = JP_VS.search(seg.strip())
        if m:
            out.append((m.group(1).split()[-1], m.group(2).split()[0]))
    return out


def jpnorm(s):
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "").lower()
    for a, b in (("ヴァ", "バ"), ("ヴィ", "ビ"), ("ヴェ", "ベ"), ("ヴォ", "ボ"), ("ヴ", "ブ")):
        s = s.replace(a, b)
    return re.sub(r"[\s・･=＝ー\-./／]|\bf\.?c\b|\bc\.?f\b", "", s)


def name_sim(a, b):
    """2つのチーム名の近さ（共通する一番長い部分の長さ ÷ 短い方の長さ）。"""
    import difflib
    a, b = jpnorm(a), jpnorm(b)
    if not a or not b:
        return 0
    m = difflib.SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return m.size / min(len(a), len(b)) if m.size >= 2 else 0


def update_jp_highlights(teams, full=False):
    """WOWOW・DAZN Japan・U-NEXT の新着動画一覧（1回1ユニットで安い）を見て、タイトルのチーム名と試合日で照合する。
    見つかった動画は、すでにある他のチャンネルの動画より優先して差し替える。"""
    key = os.environ.get("YT_API_KEY")
    if not key:
        return
    hl = load("highlights.json", {})
    names_ja = load("names_ja.json", {}).get("teams", {})
    matches = []
    for comp in COMPETITIONS:
        for path in glob.glob(os.path.join(ROOT, comp["id"], "*", "matches.json")):
            for m in load(os.path.relpath(path, ROOT), []):
                if m["s"] == "post" and m.get("h") and m.get("a"):
                    ko = datetime.strptime(m["d"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                    matches.append((ko, m, comp["id"], int(path.split(os.sep)[-2])))
    def cands(tid):
        t, ja = teams.get(tid, {}), names_ja.get(tid) or []
        return [x for x in [*ja, t.get("n"), t.get("s")] if x]
    stop = datetime(2025, 7, 1, tzinfo=timezone.utc)
    found = 0
    for ch_id, ch_name in JP_CHANNELS.items():
        token, pages = None, 0
        while True:
            params = {"part": "snippet", "maxResults": 50, "playlistId": "UU" + ch_id[2:], "key": key}
            if token:
                params["pageToken"] = token
            d = get("https://www.googleapis.com/youtube/v3/playlistItems?" + urllib.parse.urlencode(params), retries=1)
            pages += 1
            if not d:
                break
            oldest = None
            for it in d.get("items", []):
                sn = it["snippet"]
                title = html.unescape(sn.get("title", ""))
                pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
                oldest = pub
                if "ハイライト" not in title or JP_SKIP.search(title):
                    continue
                kind = "l" if JP_LONG.search(title) else "h"
                leg = JP_LEG.search(title)
                # ロングハイライトは試合の数週間後に出ることがあるので、60日まで照合する
                # シーズンが書いてあれば、そのシーズンの試合と照合（数か月後に公開される動画にも対応）
                window = timedelta(days=60 if kind == "l" else 5)
                dm = JP_DATE.search(title)
                day = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), tzinfo=timezone.utc) if dm else None
                sm = JP_SEASON.search(title)
                season = int(sm.group(1)) if sm and kind == "l" else None
                best = None
                for a, b in jp_pairs(title):
                    for ko, m, cid, y in matches:
                        if not JP_COMP[cid].search(title) or pub < ko - timedelta(hours=3):
                            continue
                        if day is not None:
                            # 試合日が書いてあれば、その日（日本時間なので前後1日）の試合だけ
                            if abs((ko + timedelta(hours=9)).date() - day.date()) > timedelta(days=1):
                                continue
                        elif (y != season) if season else (pub > ko + window):
                            continue
                        if leg and m.get("leg") and str(m["leg"]) != leg.group(1):
                            continue  # 第1戦・第2戦を取り違えない
                        ha = min(max(name_sim(a, c) for c in cands(m["h"]["id"])), max(name_sim(b, c) for c in cands(m["a"]["id"])))
                        ah = min(max(name_sim(a, c) for c in cands(m["a"]["id"])), max(name_sim(b, c) for c in cands(m["h"]["id"])))
                        # ホーム・アウェイの順に書かれているものを優先し、同点なら公開日に近い試合
                        score = (max(ha, ah), ha >= ah, ko)
                        if score[0] >= 0.6 and (best is None or score > best[0]):
                            best = (score, m)
                if not best:
                    continue
                m = best[1]
                e = hl.setdefault(m["id"], {})
                cur = e.get(kind)
                if not cur or not cur.get("jp"):
                    e[kind] = {"id": sn["resourceId"]["videoId"], "t": title, "ch": ch_name, "jp": True}
                    found += 1
            token = d.get("nextPageToken")
            # 普段は最新の50本だけ。full のときは 2025年7月まで遡る
            if not token or not full or (oldest and oldest < stop):
                break
            time.sleep(0.2)
    save("highlights.json", hl)
    print("  日本の公式ハイライト:", found, "件を反映")


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
                if now - ko < timedelta(hours=12):
                    continue  # 日本の放送局の動画を待つ（キーワード検索は見つからなかったときの予備）
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
    except (OSError, ValueError) as e:
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
                # API-Football の写真が見つかったら Wikipedia の写真とクレジットは外す（API-Football を優先）
                ph.get("wiki", {}).pop(p["n"], None)
                ph.get("credit", {}).pop(p["n"], None)
        ph["teams"][tid]["checked"] = today
    ph["_quota"] = quota
    save("photos.json", ph)
    print("  顔写真:", len(ph["players"]), "人 / API-Football 本日", quota["used"], "回")


def update_wiki_photos():
    """API-Football で写真が見つからない選手を、Wikidata（Wikimedia Commons）の写真で補う。
    同名の別人を避けるため、サッカー選手であることと生まれ年（年齢から計算）が合うことを確認する。"""
    ph = load("photos.json", {"teams": {}, "players": {}})
    wiki = ph.setdefault("wiki", {})
    tried = set(ph.get("_wiki_tried", []))
    ages = {}
    for path in glob.glob(os.path.join(ROOT, "t", "*.json")):
        for p in load(os.path.relpath(path, ROOT), {}).get("players", []):
            if p.get("age"):
                ages[p["n"]] = p["age"]
    missing = sorted(n for n in ages if n not in ph["players"] and n not in wiki and n not in tried)
    year = datetime.now(timezone.utc).year
    found = 0
    for i in range(0, len(missing), 150):
        chunk = [n for n in missing[i:i + 150] if '"' not in n and "\\" not in n]
        q = 'SELECT ?en ?p ?img ?born WHERE { VALUES ?en { %s } ?p rdfs:label ?en; wdt:P106 wd:Q937857; wdt:P18 ?img. ' \
            'OPTIONAL { ?p wdt:P569 ?born } }' % " ".join('"%s"@en' % n for n in chunk)
        req = urllib.request.Request(
            "https://query.wikidata.org/sparql", data=urllib.parse.urlencode({"query": q}).encode(),
            headers={"Accept": "application/sparql-results+json", "User-Agent": "soccer-app/1.0 (github.com/gtkg07-lab/soccer)"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                rows = json.load(r)["results"]["bindings"]
        except (OSError, ValueError, KeyError) as e:
            print("  Wikidata 写真 失敗:", e, file=sys.stderr)
            continue
        cands = {}
        for b in rows:
            born = b.get("born", {}).get("value", "")[:4]
            cands.setdefault(b["en"]["value"], {})[b["p"]["value"]] = (born, b["img"]["value"])
        for n in chunk:
            # 年齢から考えられる生まれ年（誕生日前後で ±1）に合う人が1人だけなら採用
            ok = [img for born, img in cands.get(n, {}).values()
                  if born.isdigit() and abs((year - int(born)) - ages[n]) <= 1]
            if len(ok) == 1:
                wiki[n] = ok[0].replace("http://", "https://") + "?width=160"
                found += 1
            tried.add(n)
        time.sleep(1)
    ph["_wiki_tried"] = sorted(tried)

    # 2段目：英語版 Wikipedia（表記ゆれはリダイレクトで吸収される）。50人ずつまとめて問い合わせ、説明文の生まれ年で本人確認
    wp_tried = set(ph.get("_wp_tried", []))
    todo = sorted(x for x in ages if x not in ph["players"] and x not in wiki and x not in wp_tried)
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        params = {"action": "query", "format": "json", "redirects": 1, "prop": "pageimages|description",
                  "piprop": "thumbnail", "pithumbsize": 200, "titles": "|".join(chunk)}
        url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
        d = None
        for attempt in range(3):  # 混んでいると断られる（429）ので待って再挑戦
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "soccer-app/1.0 (github.com/gtkg07-lab/soccer)"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    d = json.load(r)["query"]
                break
            except (OSError, ValueError, KeyError) as e:
                print("  Wikipedia 失敗:", e, file=sys.stderr)
                time.sleep(30 * (attempt + 1))
        if d is None:
            continue  # 次回また試す
        # 元の名前 → 正規化・リダイレクト後のページ名
        alias = {x["from"]: x["to"] for x in d.get("normalized", [])}
        redir = {x["from"]: x["to"] for x in d.get("redirects", [])}
        pages = {pg.get("title"): pg for pg in d.get("pages", {}).values()}
        for n in chunk:
            t = alias.get(n, n)
            pg = pages.get(redir.get(t, t), {})
            born = re.search(r"footballer.*born (\d{4})", pg.get("description", ""))
            thumb = (pg.get("thumbnail") or {}).get("source")
            if born and thumb and abs((year - int(born.group(1))) - ages[n]) <= 1:
                wiki[n] = thumb
                found += 1
            wp_tried.add(n)
        time.sleep(3)
    ph["_wp_tried"] = sorted(wp_tried)
    save("photos.json", ph)
    print("  Wikipedia の写真:", found, "人追加（合計", len(wiki), "人）")


def commons_file(url):
    """写真のURLから Wikimedia Commons のファイル名を取り出す（Commons 以外＝自由ライセンスでない可能性があるものは None）。"""
    url = urllib.parse.unquote(url.split("?")[0])
    if "Special:FilePath/" in url:
        return url.split("Special:FilePath/", 1)[1].replace("_", " ")
    m = re.search(r"/wikipedia/commons/thumb/[0-9a-f]/[0-9a-f]{2}/([^/]+)/", url)
    return m.group(1).replace("_", " ") if m else None


def update_photo_credits():
    """Wikipedia の写真ごとに撮影者とライセンスを取得して保存する（CC ライセンスの表示義務のため）。
    取得できない・自由ライセンスでない写真は使わない。"""
    ph = load("photos.json", {"teams": {}, "players": {}})
    wiki, credit = ph.get("wiki", {}), ph.setdefault("credit", {})
    for n in [n for n in credit if n not in wiki]:
        del credit[n]
    todo = {}
    for n, url in wiki.items():
        if n in credit:
            continue
        f = commons_file(url)
        if f is None:
            credit[n] = None  # Commons 以外の写真は使わない
        else:
            todo.setdefault(f, []).append(n)
    files = sorted(todo)
    for i in range(0, len(files), 50):
        chunk = files[i:i + 50]
        params = {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "extmetadata|url",
                  "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl", "titles": "|".join("File:" + f for f in chunk)}
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
        d = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "soccer-app/1.0 (github.com/gtkg07-lab/soccer)"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    d = json.load(r)["query"]
                break
            except (OSError, ValueError, KeyError) as e:
                print("  Commons 失敗:", e, file=sys.stderr)
                time.sleep(30 * (attempt + 1))
        if d is None:
            continue
        norm_map = {x["from"]: x["to"] for x in d.get("normalized", [])}
        pages = {p["title"]: p for p in d.get("pages", {}).values()}
        for f in chunk:
            p = pages.get(norm_map.get("File:" + f, "File:" + f), {})
            info = (p.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            lic = meta.get("LicenseShortName", {}).get("value", "")
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            ok = lic and re.search(r"CC|Public domain|PD|GFDL|Attribution", lic, re.I) and not re.search(r"fair use|non-free", lic, re.I)
            for n in todo[f]:
                credit[n] = {"a": html.unescape(artist)[:120] or "不明", "l": lic, "u": meta.get("LicenseUrl", {}).get("value", ""),
                             "f": info.get("descriptionurl", "")} if ok else None
        time.sleep(3)
    for n, c in list(credit.items()):  # クレジットを確認できない写真は外す
        if c is None:
            wiki.pop(n, None)
            del credit[n]
    save("photos.json", ph)
    print("  写真のクレジット:", len(credit), "件")


UA = {"User-Agent": "soccer-app/1.0 (github.com/gtkg07-lab/soccer)"}
YOUTH = re.compile(r"under-\d+|U-?\d\d\b|youth|juvenil|primavera|academy|reserves?\b| B$| II$|atl[eè]tic$|castilla|jong |amateur", re.I)


def sparql(q):
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://query.wikidata.org/sparql", data=urllib.parse.urlencode({"query": q}).encode(),
                                         headers=dict(UA, Accept="application/sparql-results+json"))
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["results"]["bindings"]
        except (OSError, ValueError, KeyError) as e:
            print("  Wikidata 失敗:", e, file=sys.stderr)
            time.sleep(20 * (attempt + 1))
    return None


def all_roster_players():
    """各チームの選手一覧から {ESPNの選手ID: 選手情報} を作る。所属はクラブを優先し、代表は natTeam に入れる。"""
    national = {m[k]["id"] for p in glob.glob(os.path.join(ROOT, "unl", "*", "matches.json"))
                for m in load(os.path.relpath(p, ROOT), []) for k in ("h", "a") if m.get(k)}
    ps = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "t", "*.json"))):
        tid = os.path.basename(path)[:-5]
        for p in load(os.path.relpath(path, ROOT), {}).get("players", []):
            if not p.get("id"):
                continue
            e = ps.setdefault(p["id"], {})
            if tid in national:
                e["natTeam"] = tid
                if "team" not in e:
                    e.update(p, team=tid)
            elif e.get("team") is None or e.get("team") in national:
                e.update(p, team=tid)
    return ps


def resolve_wikidata(ps, wd):
    """ESPNの選手を Wikidata の項目に対応づける。①ESPN ID ②名前＋生年月日 ③英語版Wikipediaのページ＋生年月日。"""
    todo = [i for i in ps if i not in wd]
    for i in range(0, len(todo), 400):
        rows = sparql('SELECT ?id ?p WHERE { VALUES ?id { %s } ?p wdt:P3681 ?id }' % " ".join('"%s"' % x for x in todo[i:i + 400]))
        for b in rows or []:
            wd[b["id"]["value"]] = b["p"]["value"].rsplit("/", 1)[1]
        time.sleep(1)
    todo = [i for i in ps if i not in wd and ps[i].get("dob")]
    for i in range(0, len(todo), 150):
        chunk = [x for x in todo[i:i + 150] if '"' not in ps[x]["n"]]
        rows = sparql('SELECT ?en ?p ?born WHERE { VALUES ?en { %s } ?p rdfs:label ?en; wdt:P106 wd:Q937857; wdt:P569 ?born }'
                      % " ".join('"%s"@en' % ps[x]["n"] for x in chunk))
        hits = {}
        for b in rows or []:
            hits.setdefault((b["en"]["value"], b["born"]["value"][:10]), set()).add(b["p"]["value"].rsplit("/", 1)[1])
        for x in chunk:
            q = hits.get((ps[x]["n"], ps[x]["dob"]))
            if q and len(q) == 1:
                wd[x] = q.pop()
        time.sleep(1)
    # ③ 表記ゆれ（アクセント記号など）は Wikipedia のリダイレクトで吸収する
    todo = [i for i in ps if i not in wd and ps[i].get("dob")]
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "format": "json", "redirects": 1, "prop": "pageprops", "ppprop": "wikibase_item",
             "titles": "|".join(ps[x]["n"] for x in chunk)})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                d = json.load(r)["query"]
        except (OSError, ValueError, KeyError) as e:
            print("  Wikipedia 失敗:", e, file=sys.stderr)
            time.sleep(30)
            continue
        alias = {x["from"]: x["to"] for x in d.get("normalized", [])}
        redir = {x["from"]: x["to"] for x in d.get("redirects", [])}
        pages = {pg.get("title"): pg for pg in d.get("pages", {}).values()}
        cand = {}
        for x in chunk:
            t = alias.get(ps[x]["n"], ps[x]["n"])
            q = pages.get(redir.get(t, t), {}).get("pageprops", {}).get("wikibase_item")
            if q:
                cand[x] = q
        if cand:
            rows = sparql('SELECT ?p ?born WHERE { VALUES ?p { %s } ?p wdt:P569 ?born }' % " ".join("wd:" + q for q in set(cand.values())))
            born = {b["p"]["value"].rsplit("/", 1)[1]: b["born"]["value"][:10] for b in rows or []}
            for x, q in cand.items():
                if born.get(q) == ps[x]["dob"]:
                    wd[x] = q
        time.sleep(3)
    return wd


def to_int(v):
    """Wikidata の数値（「不明」を表す特殊な値のこともある）を整数にする。数値でなければ None。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def wikidata_careers(qids):
    """所属チームの履歴（期間・出場数・ゴール数）を Wikidata からまとめて取る。チーム名は日本語があれば日本語。"""
    out = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), 60):
        rows = sparql('''SELECT ?p ?team ?ja ?en ?start ?end ?apps ?goals ?nat WHERE {
  VALUES ?p { %s }
  ?p p:P54 ?st. ?st ps:P54 ?team; wikibase:rank ?rank. FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?st pq:P1350 ?apps } OPTIONAL { ?st pq:P1351 ?goals }
  OPTIONAL { ?team rdfs:label ?ja FILTER(lang(?ja) = "ja") }
  OPTIONAL { ?team rdfs:label ?en FILTER(lang(?en) = "en") }
  OPTIONAL { ?team wdt:P31 ?nat FILTER(?nat IN (wd:Q6979593, wd:Q1194951, wd:Q20922793)) }
}''' % " ".join("wd:" + q for q in qids[i:i + 60]))
        if rows is None:
            continue
        seen = set()
        for b in rows:
            g = lambda k: b.get(k, {}).get("value", "")
            q = g("p").rsplit("/", 1)[1]
            key = (q, g("team"), g("start"), g("end"))
            if key in seen:
                continue
            seen.add(key)
            en = g("en")
            kind = "nat" if (g("nat") or re.search(r"national", en, re.I)) else "youth" if YOUTH.search(en) else "club"
            if kind == "nat" and YOUTH.search(en):
                kind = "natY"
            out.setdefault(q, []).append({
                "c": g("ja") or en, "en": en, "k": kind, "from": g("start")[:4], "to": g("end")[:4],
                "apps": to_int(g("apps")), "goals": to_int(g("goals"))})
        time.sleep(2)
    for q in out:
        out[q].sort(key=lambda c: (c["from"] or "9999", c["to"] or "9999"))
    return out


ESPN_KEYS = {"starts": "starts", "STRT": "starts", "totalGoals": "totalGoals", "G": "totalGoals",
             "goalAssists": "goalAssists", "A": "goalAssists"}


def espn_rows(names, values):
    """ESPN の成績の行から先発・ゴール・アシストだけを取り出す（項目名が略称のこともある）。"""
    row = {}
    for k, v in zip(names, values):
        if k in ESPN_KEYS:
            row[ESPN_KEYS[k]] = to_int(v) or 0
    return row


def espn_player_stats(pid):
    """ESPN の選手データ：今季の全大会の成績と、所属リーグのシーズン別成績。"""
    base = "https://site.web.api.espn.com/apis/common/v3/sports/soccer/athletes/%s" % pid
    res = {}
    ov = get(base + "/overview")
    if ov and ov.get("statistics"):
        names = ov["statistics"].get("names", [])
        res["now"] = [dict(espn_rows(names, s.get("stats", [])), comp=s.get("displayName", ""))
                      for s in ov["statistics"].get("splits", [])]
    st = get(base + "/stats")
    if st and st.get("categories"):
        cat = st["categories"][0]
        names = cat.get("names", [])
        lg = next((o["displayValue"] for f in st.get("filters", []) if f["name"] == "league"
                   for o in f.get("options", []) if o["value"] == f.get("value")), "")
        res["league"] = {"name": lg, "seasons": [
            dict(espn_rows(names, s.get("stats", [])), season=s.get("season", {}).get("displayName", ""))
            for s in cat.get("statistics", [])]}
    return res


def update_players(full=False):
    """選手の詳細ページ用データ data/p/{ESPNの選手ID}.json を作る。
    Wikidata（所属履歴）は毎回全員分。ESPN（成績）は最近試合に出た選手と、更新が古い順に一定数。"""
    ps = all_roster_players()
    wd = load("players_wd.json", {})
    resolve_wikidata(ps, wd)
    save("players_wd.json", wd)
    careers = wikidata_careers([q for q in wd.values() if q])
    # 最近（2日以内）の試合に出た選手は成績が変わっているので優先
    recent = set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ")
    by_name = {p["n"]: i for i, p in ps.items()}
    for path in glob.glob(os.path.join(ROOT, "*", "*", "matches.json")):
        for m in load(os.path.relpath(path, ROOT), []):
            if m["s"] == "post" and m["d"] >= cutoff:
                for side in load("m/%s.json" % m["id"], {}).get("lineups", {}).values():
                    recent.update(by_name[p["n"]] for p in side["players"] if p["n"] in by_name and (p["st"] or p.get("in")))
    olds = sorted(ps, key=lambda i: load("p/%s.json" % i, {}).get("espnAt", ""))
    espn_todo = list(dict.fromkeys(list(recent) + olds))[: (len(ps) if full else len(recent) + 700)]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index = {}
    for pid, p in ps.items():
        old = load("p/%s.json" % pid, {})
        q = wd.get(pid)
        rec = {k: p.get(k) for k in ("id", "n", "num", "pos", "age", "nat", "dob", "team", "natTeam")}
        rec.update(wd=q, career=careers.get(q, old.get("career", [])) if q else [],
                   espn=old.get("espn"), espnAt=old.get("espnAt", ""))
        if pid in espn_todo:
            if len(index) % 500 == 0:
                print("    …", len(index), "人目", flush=True)
            s = espn_player_stats(pid)
            if s:
                rec["espn"], rec["espnAt"] = s, now
        save("p/%s.json" % pid, rec)
        index[p["n"]] = pid
    save("players_index.json", index)
    print("  選手詳細:", len(ps), "人（Wikidata対応", sum(1 for i in ps if wd.get(i)), "人、ESPN更新", len(espn_todo), "人）")


def display_coach_name(c):
    """表示用の名前。API-Football の短い名前（"J. Klopp" のような頭文字は避ける）か、本名の最初と最後。"""
    if c.get("n") and not re.match(r"^[A-Z]\.\s", c["n"]):
        return c["n"]
    parts = (c.get("full") or "").split()
    return " ".join([parts[0], parts[-1]]) if len(parts) > 2 else (c.get("full") or c.get("n") or "")


def coach_ja(coaches):
    """監督の日本語名を探す。英語版 Wikipedia のページ（表記ゆれはリダイレクトで吸収）→ Wikidata の日本語名。
    サッカー選手・監督として登録されている人だけを採用する。戻り値は {本名: 日本語名}。"""
    titles = {}
    for c in coaches:
        for t in dict.fromkeys([c.get("full"), display_coach_name(c)]):
            if t:
                titles.setdefault(t, set()).add(c["full"])
    qid_of = {}
    tl = sorted(titles)
    for i in range(0, len(tl), 50):
        chunk = tl[i:i + 50]
        url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "format": "json", "redirects": 1, "prop": "pageprops", "ppprop": "wikibase_item",
             "titles": "|".join(chunk)})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                d = json.load(r)["query"]
        except (OSError, ValueError, KeyError) as e:
            print("  Wikipedia 失敗:", e, file=sys.stderr)
            time.sleep(30)
            continue
        alias = {x["from"]: x["to"] for x in d.get("normalized", [])}
        redir = {x["from"]: x["to"] for x in d.get("redirects", [])}
        pages = {pg.get("title"): pg for pg in d.get("pages", {}).values()}
        for t in chunk:
            a = alias.get(t, t)
            q = pages.get(redir.get(a, a), {}).get("pageprops", {}).get("wikibase_item")
            if q:
                for full in titles[t]:
                    qid_of.setdefault(full, q)
        time.sleep(3)
    found = {}
    qs = sorted(set(qid_of.values()))
    for i in range(0, len(qs), 150):
        rows = sparql('SELECT ?p ?ja WHERE { VALUES ?p { %s } ?p wdt:P106 ?job. FILTER(?job IN (wd:Q628099, wd:Q937857)) '
                      '?p rdfs:label ?ja FILTER(lang(?ja) = "ja") }' % " ".join("wd:" + q for q in qs[i:i + 150]))
        ja_of = {b["p"]["value"].rsplit("/", 1)[1]: re.sub(r"\s*[(（].*?[)）]\s*$", "", b["ja"]["value"]).strip() for b in rows or []}
        for full, q in qid_of.items():
            if q in ja_of:
                found[full] = ja_of[q].replace("=", "＝")
        time.sleep(1)
    return found


def normalize_coaches(lst):
    """API-Football の監督の在任期間を整える。
    ・前任者の在任中（退任の30日以上前）に始まった退任日なしの記録は、下部組織などの誤登録とみなして除く
    ・退任日が無い監督は、次の監督の就任日で区切る（est: 推定）"""
    lst = sorted(lst, key=lambda c: c["from"] or "")
    def minus30(d):
        return (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    keep = [c for c in lst if c["to"] or not any(
        k is not c and k["to"] and k["from"] <= c["from"] and c["from"] < minus30(k["to"]) for k in lst)]
    for i, c in enumerate(keep):
        if not c["to"]:
            later = [k for k in keep[i + 1:] if k["from"] > c["from"]]
            if later:
                c["to"], c["est"] = later[0]["from"], True
    return keep


def update_coaches(teams):
    """API-Football の監督データ（就任日・退任日つき）を、チームごとに週1回取り直して data/coaches.json に保存する。
    写真の取得と同じ無料枠（1日90回まで）を分け合う。"""
    key = os.environ.get("AF_KEY")
    if not key:
        return
    ph = load("photos.json", {"teams": {}, "players": {}})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quota = ph.get("_quota", {})
    if quota.get("day") != today:
        quota = {"day": today, "used": 0}
    co = load("coaches.json", {"teams": {}, "ja": {}})
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    order = sorted((co["teams"].get(tid, {}).get("checked", ""), tid) for tid, v in ph["teams"].items() if v.get("af"))
    for checked, tid in order:
        if checked and checked > week_ago:
            continue
        if quota["used"] >= AF_DAILY_LIMIT:
            break
        quota["used"] += 1
        af = ph["teams"][tid]["af"]
        res = af_get("/coachs?team=%s" % af, key)
        if res is None:
            break
        lst = []
        for c in res:
            full = " ".join(x for x in (c.get("firstname"), c.get("lastname")) if x) or c.get("name")
            for job in c.get("career", []):
                if str(job.get("team", {}).get("id")) == str(af):
                    lst.append({"id": c.get("id"), "n": c.get("name"), "full": full, "nat": c.get("nationality") or "",
                                "photo": c.get("photo") or "", "from": job.get("start") or "", "to": job.get("end") or ""})
        for c in lst:
            c["dn"] = display_coach_name(c)
        co["teams"][tid] = {"checked": today, "list": normalize_coaches(lst)}
    # 日本語名（まだ無い人だけ）
    todo = {c["full"]: c for t in co["teams"].values() for c in t["list"] if c["full"] not in co["ja"]}
    if todo:
        co["ja"].update(coach_ja(list(todo.values())))
    with open(os.path.join(NAMES, "coaches_ja.json"), encoding="utf-8") as f:  # 手で指定した名前を優先
        co["ja"].update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    ph["_quota"] = quota
    save("photos.json", ph)
    save("coaches.json", co)
    print("  監督:", len(co["teams"]), "チーム / API-Football 本日", quota["used"], "回")


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
                if old and old.get("final") and old.get("v") == SUMMARY_VERSION:
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
    update_jp_highlights(teams, full="--full-highlights" in sys.argv)
    update_highlights(teams)
    if with_rosters:
        update_coaches(teams)
        update_photos(teams)
        update_wiki_photos()
        update_photo_credits()
        update_players(full="--full-players" in sys.argv)
    # 中身が変わったときだけ更新時刻を進める（変化のない自動実行で記録が増えないように）
    old = load("index.json", {})
    index["updated"] = old.get("updated") if (old.get("comps") == index["comps"] and not changed) else \
        datetime.now(timezone.utc).isoformat(timespec="seconds")
    save("index.json", index)
    print("完了 通信回数:", requests_made)


if __name__ == "__main__":
    main()
