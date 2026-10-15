'use strict';

// ===== 定数 =====
const STAGE = {
  'league': 'リーグ戦',
  'league-phase': 'リーグフェーズ',
  'knockout-round-playoffs': 'プレーオフ',
  'round-of-16': 'ラウンド16',
  'quarterfinals': '準々決勝',
  'semifinals': '準決勝',
  '3rd-place-match': '3位決定戦',
  'final': '決勝',
  'relegation-playoffs': '昇降格プレーオフ',
};
const SHORT = { premier: 'プレミア', laliga: 'ラ・リーガ', ligue1: 'リーグ・アン', ucl: 'CL', unl: 'ネーションズL' };
const FLOW = {
  ucl: [
    { label: 'リーグ<br>フェーズ', stages: ['league-phase'], desc: '36チームが1つの順位表で8試合ずつ戦う。1〜8位はラウンド16へ、9〜24位はプレーオフへ、25位以下は敗退。' },
    { label: 'プレーオフ', stages: ['knockout-round-playoffs'], desc: '9〜24位がホーム&アウェイで対戦し、勝者8チームがラウンド16へ。' },
    { label: 'ラウンド<br>16', stages: ['round-of-16'], desc: 'ここからホーム&アウェイの2試合合計で勝ち上がりを決める。' },
    { label: '準々決勝', stages: ['quarterfinals'], desc: '2試合合計で勝ち上がり。' },
    { label: '準決勝', stages: ['semifinals'], desc: '2試合合計で勝ち上がり。' },
    { label: '決勝', stages: ['final'], desc: '中立地での1試合で優勝が決まる。' },
  ],
  unl: [
    { label: 'リーグ<br>フェーズ', stages: ['league-phase'], desc: '強さ順にリーグA〜Dに分かれ、各グループで総当たり。成績で上のリーグへの昇格・下への降格が決まる。' },
    { label: '準々決勝', stages: ['quarterfinals'], desc: 'リーグA各グループの1・2位がホーム&アウェイで対戦。' },
    { label: 'ファイナルズ', stages: ['semifinals', '3rd-place-match', 'final'], desc: '勝ち上がった4チームで準決勝・3位決定戦・決勝を行う。' },
    { label: '昇降格<br>PO', stages: ['relegation-playoffs'], desc: '上のリーグの下位と下のリーグの上位が入れ替えをかけて対戦。' },
  ],
};
const STATS = [
  ['possessionPct', '支配率', 'pct'],
  ['totalShots', 'シュート'],
  ['shotsOnTarget', '枠内シュート'],
  ['blockedShots', 'ブロックされたシュート'],
  ['wonCorners', 'コーナーキック'],
  ['totalPasses', 'パス'],
  ['accuratePasses', 'パス成功'],
  ['passRate', 'パス成功率', 'rate', 'accuratePasses', 'totalPasses'],
  ['totalCrosses', 'クロス'],
  ['totalTackles', 'タックル'],
  ['interceptions', 'インターセプト'],
  ['totalClearance', 'クリア'],
  ['saves', 'セーブ'],
  ['foulsCommitted', 'ファウル'],
  ['offsides', 'オフサイド'],
  ['yellowCards', 'イエローカード'],
  ['redCards', 'レッドカード'],
];
const POS_GROUP = { G: 'ゴールキーパー', D: 'ディフェンダー', M: 'ミッドフィルダー', F: 'フォワード' };
const YT_ICON = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M23 7.2a3 3 0 0 0-2.1-2.1C19 4.6 12 4.6 12 4.6s-7 0-8.9.5A3 3 0 0 0 1 7.2 31 31 0 0 0 .5 12a31 31 0 0 0 .5 4.8 3 3 0 0 0 2.1 2.1c1.9.5 8.9.5 8.9.5s7 0 8.9-.5a3 3 0 0 0 2.1-2.1 31 31 0 0 0 .5-4.8 31 31 0 0 0-.5-4.8zM9.7 15.1V8.9L15.5 12z"/></svg>';

// ===== データ読み込み =====
const cache = {};
function load(path) {
  if (!cache[path]) {
    cache[path] = fetch('data/' + path, { cache: 'no-cache' })
      .then(r => (r.ok ? r.json() : null))
      .then(d => (d && path.endsWith('matches.json') ? linkLegs(d) : d))
      .catch(() => null);
  }
  return cache[path];
}

// ホーム&アウェイの2試合制：同じ段階・同じ2チームの試合を組にして、もう一方の試合と2試合合計を付ける
function linkLegs(list) {
  const ties = new Map();
  for (const m of list) {
    if (!m.leg || !m.h || !m.a) continue;
    const key = `${m.st}|${[m.h.id, m.a.id].sort().join('-')}`;
    if (!ties.has(key)) ties.set(key, []);
    ties.get(key).push(m);
  }
  for (const legs of ties.values()) {
    if (legs.length !== 2) continue;
    legs.sort((a, b) => a.leg - b.leg);
    const [l1, l2] = legs;
    l1.other = l2; l2.other = l1;
    if (l1.s === 'post' && l2.s !== 'pre') {
      // 2試合合計（第2戦のホーム・アウェイの向きで数える）
      const h = +l2.h.sc + (l1.h.id === l2.h.id ? +l1.h.sc : +l1.a.sc);
      const a = +l2.a.sc + (l1.a.id === l2.a.id ? +l1.a.sc : +l1.h.sc);
      l2.agg = { h, a };
    }
  }
  return list;
}
// 第1戦は赤、第2戦は青の印
const legBadge = m => (m.leg ? `<span class="leg leg${m.leg}">第${m.leg}戦</span>` : '');
let INDEX = null, TEAMS = {}, NAMES = { teams: {}, players: {} }, PHOTOS = {}, CREDIT = {};
// 日本語名があれば使う（en は YouTube 検索用に英語名を残す）
const team = id => {
  const t = TEAMS[id] || { n: '未定', s: '未定', logo: '' };
  const ja = NAMES.teams[id];
  return ja ? { ...t, en: t.n, n: ja[0], s: ja[1] } : { ...t, en: t.n };
};
const pn = name => NAMES.players[name] || name || '';
// 顔写真（無ければ背番号の丸）
const avatar = (p, cls = 'av') => PHOTOS[p.n]
  ? `<img class="${cls}" data-n="${esc(p.n)}" src="${esc(PHOTOS[p.n])}" alt="" loading="lazy" onerror="this.className='${cls} none';this.removeAttribute('src')">`
  : `<span class="${cls} none"></span>`;
const country = en => (NAMES.countries || {})[en] || en || '';
// フォーメーション図では長いカタカナ名を最後の部分（姓）だけにする
const pnShort = name => { const j = pn(name); return j.includes('・') ? j.split('・').pop() : j; };
const comp = id => INDEX.comps.find(c => c.id === id);
function currentSeason(c) {
  const ss = c.seasons;
  for (let i = ss.length - 1; i >= 0; i--) if (ss[i].done > 0) return ss[i];
  return ss[0];
}

// ===== 表示の部品 =====
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const logo = (id, cls = '') => {
  const t = team(id);
  return t.logo ? `<img class="logo ${cls}" src="${esc(t.logo)}" alt="" loading="lazy">` : `<span class="logo ${cls}"></span>`;
};
const JST = { timeZone: 'Asia/Tokyo' };
const dayKey = iso => new Date(iso).toLocaleDateString('sv-SE', JST); // YYYY-MM-DD（日本時間）
const todayKey = () => new Date().toLocaleDateString('sv-SE', JST);
const fmtDay = iso => new Date(iso).toLocaleDateString('ja-JP', { ...JST, month: 'numeric', day: 'numeric', weekday: 'short' });
const fmtTime = iso => new Date(iso).toLocaleTimeString('ja-JP', { ...JST, hour: '2-digit', minute: '2-digit' });
const fmtFull = iso => new Date(iso).toLocaleString('ja-JP', { ...JST, year: 'numeric', month: 'numeric', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit' });

function statusText(m) {
  if (m.n === 'STATUS_POSTPONED') return '延期';
  if (m.n === 'STATUS_CANCELED') return '中止';
  if (m.s === 'in') return m.sd || '試合中';
  if (m.s === 'post') return m.n === 'STATUS_FINAL_PEN' || m.h.pen != null ? 'PK' : m.n === 'STATUS_FINAL_AET' ? '延長' : '終了';
  return fmtTime(m.d);
}
function stageText(m) {
  let s = STAGE[m.st] || m.st;
  if (m.g) s = groupJa(m.g);
  return s;
}
function groupJa(g) {
  const x = /Group ([A-D])(\d)/.exec(g || '');
  return x ? `リーグ${x[1]} グループ${x[2]}` : (g || '').replace('Group', 'グループ');
}

function matchRow(m, cid, y, opts = {}) {
  const pre = m.s === 'pre';
  const hw = m.s === 'post' && (m.h.w || (m.adv && m.adv === m.h.id));
  const aw = m.s === 'post' && (m.a.w || (m.adv && m.adv === m.a.id));
  const live = m.s === 'in';
  const pen = m.h.pen != null ? `<small>PK ${m.h.pen}-${m.a.pen}</small>` : '';
  const sub = opts.compTag ? SHORT[cid] : opts.stage ? stageText(m) : '';
  return `<a class="match" href="#/m/${cid}/${y}/${m.id}">
    <div class="time ${live ? 'live' : ''}">${esc(statusText(m))}${sub || m.leg ? `<div class="tag">${esc(sub)}${(opts.compTag || opts.stage) && m.leg ? ` ${legBadge(m)}` : ''}</div>` : ''}</div>
    <div class="team home ${hw ? 'win' : ''}"><span>${esc(team(m.h.id).s)}</span>${logo(m.h.id)}</div>
    <div class="score ${pre ? 'pre' : ''}">${pre ? 'vs' : `${m.h.sc}-${m.a.sc}`}${pen}${m.agg ? `<small>合計 ${m.agg.h}-${m.agg.a}</small>` : ''}</div>
    <div class="team ${aw ? 'win' : ''}">${logo(m.a.id)}<span>${esc(team(m.a.id).s)}</span></div>
  </a>`;
}

function matchesByDay(list, render) {
  let html = '', last = '';
  const today = todayKey();
  for (const x of list) {
    const k = dayKey(x.m.d);
    if (k !== last) {
      if (last) html += '</div>';
      const isToday = k === today;
      html += `<div class="card" ${isToday ? 'id="today"' : ''} data-day="${k}"><div class="daylabel ${isToday ? 'today' : ''}">${fmtDay(x.m.d)}${isToday ? '（今日）' : ''}</div>`;
      last = k;
    }
    html += render(x);
  }
  return html + (last ? '</div>' : '');
}

function tabs(items, active) {
  return `<div class="tabs">${items.map(([k, l]) => `<button data-tab="${k}" class="${k === active ? 'on' : ''}">${l}</button>`).join('')}</div>`;
}
function bindTabs(root, handler) {
  root.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => handler(b.dataset.tab)));
}

const app = document.getElementById('app');
const setNav = active => {
  document.getElementById('compnav').innerHTML =
    `<a href="#/" class="${active === 'home' ? 'on' : ''}">ホーム</a>` +
    INDEX.comps.map(c => `<a href="#/c/${c.id}" class="${active === c.id ? 'on' : ''}">${esc(c.name)}</a>`).join('');
};

// ===== ホーム =====
async function viewHome() {
  setNav('home');
  const all = [];
  await Promise.all(INDEX.comps.map(async c => {
    const s = currentSeason(c);
    const ms = (await load(`${c.id}/${s.y}/matches.json`)) || [];
    ms.forEach(m => all.push({ m, cid: c.id, y: s.y }));
  }));
  all.sort((a, b) => a.m.d.localeCompare(b.m.d));
  const now = Date.now(), today = todayKey();
  const live = all.filter(x => x.m.s === 'in');
  const todays = all.filter(x => dayKey(x.m.d) === today && x.m.s !== 'in');
  const recent = all.filter(x => x.m.s === 'post' && dayKey(x.m.d) < today && now - new Date(x.m.d) < 3 * 864e5);
  const next = all.filter(x => x.m.s === 'pre' && dayKey(x.m.d) > today).slice(0, 15);
  const row = x => matchRow(x.m, x.cid, x.y, { compTag: true });
  const section = (title, list, byDay) => list.length
    ? `<h2>${title}</h2>` + (byDay ? matchesByDay(list, row) : `<div class="card">${list.map(row).join('')}</div>`)
    : '';

  app.innerHTML =
    `<h2>大会</h2><div class="comps">${INDEX.comps.map(c => {
      const s = currentSeason(c);
      return `<a class="card compcard" href="#/c/${c.id}">${c.logo ? `<img class="logo lg" src="${esc(c.logo)}" alt="">` : ''}${esc(c.name)}<span class="sub">${s.label}シーズン</span></a>`;
    }).join('')}</div>` +
    section('<span style="color:var(--live)">●</span> 試合中', live) +
    section('今日の試合', todays) +
    section('最近の結果', recent.reverse(), true) +
    section('これからの試合', next, true) +
    (live.length + todays.length + recent.length + next.length ? '' : '<p class="empty">直近の試合はありません</p>') +
    footer();
}

// ===== 大会 =====
async function viewComp(cid, y, tab) {
  const c = comp(cid);
  if (!c) return viewHome();
  setNav(cid);
  const season = c.seasons.find(s => String(s.y) === String(y)) || currentSeason(c);
  y = season.y;
  const [ms, st] = await Promise.all([load(`${cid}/${y}/matches.json`), load(`${cid}/${y}/standings.json`)]);
  const matches = ms || [];
  const tabList = c.type === 'league'
    ? [['table', '順位表'], ['games', '日程・結果']]
    : [['table', c.type === 'unl' ? 'グループ' : '順位表'], ['ko', '勝ち上がり'], ['games', '日程・結果']];
  if (!tab || !tabList.some(t => t[0] === tab)) tab = tabList[0][0];

  let body = '';
  if (tab === 'table') body = standingsHtml(c, st || []);
  if (tab === 'ko') body = knockoutHtml(c, y, matches);
  if (tab === 'games') body = gamesHtml(c, y, matches);

  app.innerHTML = `
    <div class="pagehead">${c.logo ? `<img class="logo lg" src="${esc(c.logo)}" alt="">` : ''}
      <div><h1>${esc(c.name)}</h1>
      <select id="season">${c.seasons.map(s => `<option value="${s.y}" ${s.y === y ? 'selected' : ''}>${s.label}シーズン</option>`).join('')}</select></div>
    </div>
    ${flowHtml(c, matches)}
    ${tabList.length > 1 ? tabs(tabList, tab) : ''}
    <div id="body">${body}</div>${footer()}`;
  document.getElementById('season').addEventListener('change', e => { location.hash = `#/c/${cid}/${e.target.value}/${tab}`; });
  bindTabs(app, t => { history.replaceState(history.state, '', `#/c/${cid}/${y}/${t}`); viewComp(cid, y, t); });
  if (tab === 'games') {
    const target = document.getElementById('today') ||
      [...app.querySelectorAll('[data-day]')].find(el => el.dataset.day >= todayKey());
    if (target) target.scrollIntoView({ block: 'start' });
  }
}

function flowHtml(c, matches) {
  const total = matches.length, done = matches.filter(m => m.s === 'post').length;
  if (c.type === 'league') {
    const pct = total ? Math.round(done / total * 100) : 0;
    const first = matches[0], last = matches[matches.length - 1];
    return `<div class="card" style="margin-top:12px;padding:12px 12px 4px">
      <div style="display:flex;justify-content:space-between;font-size:13px"><b>シーズンの進み具合</b><span>${done} / ${total} 試合（${pct}%）</span></div>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <div class="explain" style="padding:4px 0 10px">${first ? `${fmtDay(first.d)} 開幕 → ${fmtDay(last.d)} 最終節。` : ''}全チームがホーム&アウェイで2回ずつ対戦（総当たり）。勝ち3点・引き分け1点で順位を決める。</div>
    </div>`;
  }
  const steps = FLOW[c.type];
  let nowIdx = -1;
  const info = steps.map(s => {
    const ms = matches.filter(m => s.stages.includes(m.st));
    const d = ms.filter(m => m.s === 'post').length;
    const state = ms.length && d === ms.length ? 'done' : d > 0 || ms.some(m => m.s === 'in') ? 'now' : '';
    return { s, ms, state };
  });
  // 進行中が無ければ「次に始まる段階」を現在とする
  if (!info.some(x => x.state === 'now')) {
    const i = info.findIndex(x => x.state !== 'done');
    if (i > 0 && info[i - 1].state === 'done' && i < info.length) info[i].state = info[i].ms.length ? 'now' : '';
  }
  nowIdx = info.findIndex(x => x.state === 'now');
  const cur = info[nowIdx >= 0 ? nowIdx : info.every(x => x.state === 'done') ? info.length - 1 : 0];
  const final = matches.find(m => m.st === 'final' && m.s === 'post');
  const champ = final && (final.adv || (final.h.w ? final.h.id : final.a.w ? final.a.id : null));
  return `<div class="card" style="margin-top:12px">
    <div class="flow">${info.map(x => {
      const range = x.ms.length ? fmtDay(x.ms[0].d).replace(/\(.\)/, '') + '〜' : '未定';
      return `<div class="step ${x.state}"><div class="dot">${x.state === 'done' ? '✓' : ''}</div><b>${x.s.label}</b><small>${range}</small></div>`;
    }).join('')}</div>
    ${champ ? `<a class="champ" href="#/t/${champ}">🏆 優勝 ${logo(champ)} ${esc(team(champ).n)}</a>` : `<div class="explain"><b style="color:var(--text)">${cur.s.label.replace('<br>', '')}</b>：${cur.s.desc}</div>`}
  </div>`;
}

function standingsHtml(c, groups) {
  if (!groups.length) return '<p class="empty">順位表はまだありません</p>';
  const notes = new Map();
  const table = g => `<table class="st"><thead><tr><th>#</th><th style="text-align:left">チーム</th><th>試</th><th class="hide-sm">勝</th><th class="hide-sm">分</th><th class="hide-sm">負</th><th>差</th><th>点</th></tr></thead><tbody>
    ${[...g.rows].sort((a, b) => (+a.r || 99) - (+b.r || 99)).map(r => {
      if (r.note) notes.set(r.note.t, r.note.c);
      return `<tr><td class="rk" style="border-left-color:${r.note ? esc(r.note.c) : 'transparent'}">${esc(r.r)}</td>
        <td class="tm"><a href="#/t/${r.id}">${logo(r.id)}<span>${esc(team(r.id).s)}</span></a></td>
        <td>${esc(r.p)}</td><td class="hide-sm">${esc(r.w)}</td><td class="hide-sm">${esc(r.d)}</td><td class="hide-sm">${esc(r.l)}</td>
        <td>${esc(r.gd)}</td><td class="pts">${esc(r.pts)}</td></tr>`;
    }).join('')}</tbody></table>`;
  let html = '';
  if (c.type === 'unl') {
    let lastLeague = '';
    for (const g of groups) {
      const lg = (/Group ([A-D])/.exec(g.name) || [])[1];
      if (lg && lg !== lastLeague) { html += `<h2>リーグ${lg}</h2>`; lastLeague = lg; }
      html += `<h3>${esc(groupJa(g.name))}</h3><div class="card">${table(g)}</div>`;
    }
  } else {
    html = groups.map(g => `<div class="card">${table(g)}</div>`).join('');
  }
  const legend = [...notes].map(([t, col]) => `<span><i style="background:${esc(col)}"></i>${esc(noteJa(t))}</span>`).join('');
  return (legend ? `<div class="legend">${legend}</div>` : '') + html;
}
function noteJa(t) {
  return t
    .replace(/Champions League( Qualifying)?/gi, (_, q) => q ? 'CL予選' : 'CL出場')
    .replace(/Europa League( Qualifying)?/gi, (_, q) => q ? 'EL予選' : 'EL出場')
    .replace(/Conference League( Qualifying)?/gi, (_, q) => q ? 'ECL予選' : 'ECL出場')
    .replace(/Qualifies for round of 16/gi, 'ラウンド16進出')
    .replace(/Knockout phase playoffs? - unseeded/gi, 'プレーオフ進出（ノーシード）')
    .replace(/Knockout phase playoffs? - seeded/gi, 'プレーオフ進出（シード）')
    .replace(/Qualifies for (the )?(knockout round )?playoffs?/gi, 'プレーオフ進出')
    .replace(/Qualifies for QFs/gi, '準々決勝進出')
    .replace(/Relegation or playoffs/gi, '降格または入れ替え戦')
    .replace(/(Relegation|Promotion) playoffs?/gi, '入れ替え戦')
    .replace(/Relegation/gi, '降格').replace(/Promotion/gi, '昇格').replace(/Eliminated/gi, '敗退')
    .replace(/;/g, '／');
}

function knockoutHtml(c, y, matches) {
  const order = Object.keys(STAGE).filter(s => s !== 'league' && s !== 'league-phase');
  const stages = order.filter(s => matches.some(m => m.st === s));
  if (!stages.length) return '<p class="empty">ノックアウトステージはまだ始まっていません</p>';
  return stages.map(s => {
    const ties = new Map();
    for (const m of matches.filter(m => m.st === s)) {
      const key = [m.h.id, m.a.id].sort().join('-');
      if (!ties.has(key)) ties.set(key, []);
      ties.get(key).push(m);
    }
    return `<div class="round"><h2>${STAGE[s]}</h2><div class="card">${[...ties.values()].map(legs => tieHtml(c, y, legs)).join('')}</div></div>`;
  }).join('');
}
function tieHtml(c, y, legs) {
  legs.sort((a, b) => a.d.localeCompare(b.d));
  const A = legs[0].h.id, B = legs[0].a.id;
  let ga = 0, gb = 0, played = 0;
  for (const m of legs) if (m.s === 'post') {
    played++;
    const [x, z] = m.h.id === A ? [m.h.sc, m.a.sc] : [m.a.sc, m.h.sc];
    ga += +x; gb += +z;
  }
  const lastLeg = legs[legs.length - 1];
  let adv = lastLeg.adv;
  if (!adv && lastLeg.s === 'post') {
    if (legs.length === 1) adv = lastLeg.h.w ? lastLeg.h.id : lastLeg.a.w ? lastLeg.a.id : null;
    else if (ga !== gb) adv = ga > gb ? A : B;
    else if (lastLeg.h.w || lastLeg.a.w) adv = lastLeg.h.w ? lastLeg.h.id : lastLeg.a.id;
  }
  const row = (id, g) => `<div class="row ${adv === id ? 'adv' : ''}">${logo(id)}<span><a href="#/t/${id}">${esc(team(id).s)}</a></span><b class="agg">${played ? g : ''}</b>${adv === id ? '<em class="badge">勝ち上がり</em>' : ''}</div>`;
  return `<div class="tie">${row(A, ga)}${row(B, gb)}
    <div class="legs">${legs.map(m => `<a href="#/m/${c.id}/${y}/${m.id}">${legBadge(m)} ${fmtDay(m.d)} ${m.s === 'pre' ? fmtTime(m.d) : `${esc(team(m.h.id).ab)} ${m.h.sc}-${m.a.sc} ${esc(team(m.a.id).ab)}${m.h.pen != null ? `（PK ${m.h.pen}-${m.a.pen}）` : ''}`}</a>`).join('')}</div></div>`;
}

function gamesHtml(c, y, matches) {
  if (!matches.length) return '<p class="empty">日程はまだ発表されていません</p>';
  return matchesByDay(matches.map(m => ({ m })), x => matchRow(x.m, c.id, y, { stage: c.type !== 'league' }));
}

// ===== 試合詳細 =====
async function viewMatch(cid, y, id, tab) {
  const c = comp(cid);
  setNav(cid);
  const [ms, d] = await Promise.all([load(`${cid}/${y}/matches.json`), load(`m/${id}.json`)]);
  const m = (ms || []).find(x => x.id === id);
  if (!m) { app.innerHTML = '<p class="empty">試合が見つかりません</p>'; return; }
  const H = team(m.h.id), A = team(m.a.id);
  const ev = (d && d.events) || [];
  const scorers = side => ev.filter(e => ['goal', 'pen', 'og'].includes(e.t) && e.team === side)
    .map(e => `<a href="#/pn/${encodeURIComponent(e.p[0] || '')}">${esc(pn(e.p[0]))}</a> ${esc(e.min)}${e.t === 'pen' ? '(PK)' : e.t === 'og' ? '(OG)' : ''}`).join('<br>');
  const q = encodeURIComponent;
  const season = c.seasons.find(s => s.y === +y);
  // 見つかった動画があれば直接、無ければ YouTube の検索結果を開く
  const hl = ((await load('highlights.json')) || {})[id] || {};
  const co = (await load('coaches.json')) || { teams: {}, ja: {} };
  matchCoaches = co;
  const ch = coachAt(co, m.h.id, m.d.slice(0, 10)), ca = coachAt(co, m.a.id, m.d.slice(0, 10));
  const coachLine = ch || ca ? `<div class="scorers coachline"><div>${ch ? `監督 ${esc(coachName(co, ch))}` : ''}</div><div>${ca ? `監督 ${esc(coachName(co, ca))}` : ''}</div></div>` : '';
  const vlink = (v, query, label) => v
    ? `<a href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener" title="${esc(v.t)}">${YT_ICON}${label}</a>`
    : `<a class="search" href="https://www.youtube.com/results?search_query=${q(query)}" target="_blank" rel="noopener">${YT_ICON}${label}を検索</a>`;
  const vids = m.s === 'post' ? `<div class="videos">
      ${vlink(hl.h, `${H.en} vs ${A.en} highlights ${season ? season.label : ''}`, 'ハイライト')}
      ${vlink(hl.l, `${H.en} vs ${A.en} extended highlights`, 'ロングハイライト')}
    </div>${hl.h || hl.l ? `<div class="note">${esc([hl.h, hl.l].filter(Boolean).map(v => v.ch).filter((v, i, a) => a.indexOf(v) === i).join('・'))} の動画</div>` : '<div class="note">動画が見つかるまでは YouTube の検索結果が開きます</div>'}` : '';
  // 2試合制：もう一方の試合へのリンクと、第2戦なら2試合合計・勝ち上がり
  const o = m.other;
  const advId = m.adv || (m.agg && m.s === 'post' && m.agg.h !== m.agg.a ? (m.agg.h > m.agg.a ? m.h.id : m.a.id) : null);
  const legBox = o ? `<div class="legbox">
      ${m.agg ? `<div><b>2試合合計 ${m.agg.h} - ${m.agg.a}</b>${m.h.pen != null ? `（PK ${m.h.pen}-${m.a.pen}）` : ''}${advId ? ` ・ <b>${esc(team(advId).s)}</b> が勝ち上がり` : m.s === 'in' ? '（試合中）' : ''}</div>` : ''}
      <a href="#/m/${cid}/${y}/${o.id}">${legBadge(o)} ${fmtDay(o.d)} ${o.s === 'pre' ? `${fmtTime(o.d)} キックオフ` : `${esc(team(o.h.id).s)} ${o.h.sc}-${o.a.sc} ${esc(team(o.a.id).s)}`} ›</a>
    </div>` : '';
  const big = m.s === 'pre'
    ? `<div class="big" style="font-size:20px">${fmtTime(m.d)}<small>キックオフ</small></div>`
    : `<div class="big">${m.h.sc} - ${m.a.sc}<small style="${m.s === 'in' ? 'color:var(--live);font-weight:700' : ''}">${m.h.pen != null ? `PK戦 ${m.h.pen}-${m.a.pen}` : esc(statusText(m))}</small></div>`;

  const tabList = [['stats', 'スタッツ'], ['lineup', 'スタメン'], ['timeline', '経過']];
  if (!tab) tab = 'stats';
  let body = '';
  if (!d) body = `<p class="empty">${m.s === 'pre' ? 'スタメンやスタッツは試合が始まると表示されます' : '詳細データはまだありません'}</p>`;
  else if (tab === 'stats') body = statsHtml(d);
  else if (tab === 'lineup') body = lineupHtml(m, d);
  else body = timelineHtml(m, d);

  app.innerHTML = `
    <button class="back" onclick="document.getElementById('backbtn').click()">‹ 戻る</button>
    <div class="card mhead">
      <div class="meta"><a href="#/c/${cid}/${y}">${esc(c.name)}</a> · ${esc(stageText(m))} ${legBadge(m)}</div>
      <div class="meta">${fmtFull(m.d)}${d && d.venue ? ` · ${esc(d.venue)}` : ''}</div>
      <div class="teams">
        <a href="#/t/${m.h.id}">${logo(m.h.id, 'xl')}${esc(H.s)}</a>${big}<a href="#/t/${m.a.id}">${logo(m.a.id, 'xl')}${esc(A.s)}</a>
      </div>
      ${d ? `<div class="scorers"><div>${scorers(m.h.id)}</div><div>${scorers(m.a.id)}</div></div>` : ''}
      ${coachLine}
      ${legBox}
      ${vids}
    </div>
    ${d ? tabs(tabList, tab) : ''}<div id="body">${body}</div>${footer()}`;
  bindTabs(app, t => { history.replaceState(history.state, '', `#/m/${cid}/${y}/${id}/${t}`); viewMatch(cid, y, id, t); });
  bindLineupSwitch(m, d);
}

function statsHtml(d) {
  const s = d.stats || {};
  const num = v => parseFloat(v) || 0;
  const rows = STATS.map(([k, label, kind, a, b]) => {
    let h, w;
    if (kind === 'rate') {
      if (!s[a] || !s[b]) return '';
      const r = side => (num(s[b][side]) ? Math.round(num(s[a][side]) / num(s[b][side]) * 100) : 0);
      h = r('home'); w = r('away');
    } else {
      if (!s[k]) return '';
      h = num(s[k].home); w = num(s[k].away);
    }
    const unit = kind ? '%' : '';
    const max = Math.max(h + w, 1);
    return `<div class="stat"><div class="lbl"><b>${h}${unit}</b><span>${label}</span><b>${w}${unit}</b></div>
      <div class="bars"><div class="h"><i style="width:${h / max * 100}%"></i></div><div class="a"><i style="width:${w / max * 100}%"></i></div></div></div>`;
  }).join('');
  return rows ? `<div class="card">${rows}</div>` : '<p class="empty">スタッツはありません</p>';
}

// ポジションの略号から「縦の位置（深さ）」と「左右」を推定して、フォーメーション通りに並べる
function depthOf(pos) {
  if (pos === 'G') return 0;
  if (/WB$/.test(pos)) return 1.5;
  if (/^(CD|SW|LB|RB|D)/.test(pos)) return 1;
  if (/^DM/.test(pos)) return 2;
  if (/^AM/.test(pos)) return 4;
  if (/^(F|CF|LF|RF|LW|RW|ST|S)/.test(pos)) return 5;
  return 3;
}
function sideOf(pos) {
  if (/-L$/.test(pos)) return 1;
  if (/-R$/.test(pos)) return 3;
  if (/^L/.test(pos)) return 0;
  if (/^R/.test(pos)) return 4;
  return 2;
}
function formationRows(players, form) {
  const starters = players.filter(p => p.st);
  const gk = starters.filter(p => p.pos === 'G');
  const out = starters.filter(p => p.pos !== 'G')
    .sort((a, b) => depthOf(a.pos) - depthOf(b.pos) || Math.abs(sideOf(a.pos) - 2) - Math.abs(sideOf(b.pos) - 2));
  let lines = (form || '').split('-').map(Number).filter(Boolean);
  if (lines.reduce((x, y) => x + y, 0) !== out.length) {
    lines = [];
    const groups = {};
    out.forEach(p => { groups[depthOf(p.pos)] = (groups[depthOf(p.pos)] || 0) + 1; });
    Object.keys(groups).sort().forEach(k => lines.push(groups[k]));
  }
  const rows = [gk];
  let i = 0;
  for (const n of lines) { rows.push(out.slice(i, i + n).sort((a, b) => sideOf(a.pos) - sideOf(b.pos))); i += n; }
  return rows;
}
let lineupSide = 'home';
let matchCoaches = { teams: {}, ja: {} }; // 試合詳細で読み込んだ監督データ（スタメン表示でも使う）
// 簡易評価の表示（色は評価の高さで変える）
const plink = p => p.id ? `#/p/${p.id}` : `#/pn/${encodeURIComponent(p.n)}`;
const rateClass = r => r >= 8 ? 'r8' : r >= 7 ? 'r7' : r >= 6.5 ? 'r65' : r >= 6 ? 'r6' : r >= 5 ? 'r5' : 'r4';
const rateChip = p => p.r != null ? `<span class="rate ${rateClass(p.r)}">${p.motm ? '★' : ''}${p.r.toFixed(1)}</span>` : '';
const RATE_NOTE = `<details class="ratenote"><summary>評価について（アプリ独自の簡易評価）</summary><div class="explain" style="padding:8px 0 0">
  ESPN で取れる記録だけを使った、このアプリ独自の評価です（FotMob や SofaScore の評価とは異なります）。6.0 を基準に、
  ゴール +1.0、アシスト +0.7、枠内シュート +0.2、枠外シュート +0.05、セーブ +0.25、
  失点（GK −0.4・DF −0.25・MF −0.1）、無失点で60分以上出場（GK +0.8・DF +0.6・MF +0.2）、
  イエロー −0.3、レッド −1.5、オウンゴール −1.0、ファウル −0.05、被ファウル +0.05、オフサイド −0.05、
  勝利 +0.3・敗戦 −0.2（出場時間に比例）で計算し、3.0〜10.0 に収めます。
  出場10分未満で目立った記録が無い選手は評価しません。★はその試合の最高評価。パスや守備の記録が無いため、守備的な選手は 6 点前後に集まりやすくなります。
</div></details>`;

function lineupHtml(m, d) {
  const side = d.lineups[lineupSide] ? lineupSide : 'home';
  const L = d.lineups[side];
  if (!L || !L.players.length) return '<p class="empty">スタメンはまだ発表されていません</p>';
  const tid = side === 'home' ? m.h.id : m.a.id;
  const ev = d.events || [];
  const marks = name => {
    let s = '';
    const mine = ev.filter(e => e.p[0] === name);
    mine.forEach(e => {
      if (e.t === 'goal' || e.t === 'pen') s += '⚽';
      if (e.t === 'og') s += '⚽(OG)';
      if (e.t === 'yc') s += '<i class="yc"></i>';
      if (e.t === 'rc' || e.t === 'rc2') s += '<i class="rc"></i>';
    });
    return s;
  };
  const rows = formationRows(L.players, L.form);
  let dots = '';
  rows.forEach((row, ri) => {
    const yPos = 90 - ri * (78 / Math.max(rows.length - 1, 1)); // 上下の端で写真が切れないよう余白をとる
    row.forEach((p, pi) => {
      const x = (pi + 1) / (row.length + 1) * 100;
      dots += `<a class="pl" href="${plink(p)}" style="left:${x}%;top:${yPos}%"><div class="face">${PHOTOS[p.n] ? avatar(p, "pf") : `<div class="num">${esc(p.num)}</div>`}${p.out ? '<i class="subout">↓</i>' : ''}<span class="ic">${marks(p.n)}</span></div>${rateChip(p)}<div class="nm">${esc(p.num)} ${esc(pnShort(p.n))}</div></a>`;
    });
  });
  const bench = L.players.filter(p => !p.st);
  return `<div class="tabs" id="side"><button data-side="home" class="${side === 'home' ? 'on' : ''}">${esc(team(m.h.id).s)}</button><button data-side="away" class="${side === 'away' ? 'on' : ''}">${esc(team(m.a.id).s)}</button></div>
    <div class="formation">${logo(tid)}<b>${esc(L.form || '')}</b></div>
    ${(c => c ? `<div class="lcoach">${coachAvatar(c)}<span><span class="x">監督</span> <b>${esc(coachName(matchCoaches, c))}</b></span></div>` : '')(coachAt(matchCoaches, tid, m.d.slice(0, 10)))}
    <div class="pitch">${dots}</div>
    ${L.players.some(p => p.r != null) ? RATE_NOTE : ''}
    <h3>ベンチ</h3><div class="card plist">${bench.map(p => `<a class="p" href="${plink(p)}"><span class="no">${esc(p.num)}</span>${avatar(p)}<span class="grow">${esc(pn(p.n))} ${marks(p.n)}${p.in ? `<span class="x"> ↑途中出場${p.min ? `（${p.min}分）` : ''}</span>` : ''}</span>${rateChip(p)}</a>`).join('')}</div>`;
}
function bindLineupSwitch(m, d) {
  app.querySelectorAll('#side button').forEach(b => b.addEventListener('click', () => {
    lineupSide = b.dataset.side;
    document.getElementById('body').innerHTML = lineupHtml(m, d);
    bindLineupSwitch(m, d);
  }));
}

function timelineHtml(m, d) {
  const ev = (d.events || []);
  if (!ev.length) return '<p class="empty">記録はありません</p>';
  const icon = { goal: '⚽', pen: '⚽ PK', og: '⚽ OG', yc: '<i class="yc"></i>', rc: '<i class="rc"></i>', rc2: '<i class="yc"></i><i class="rc"></i>', sub: '🔄' };
  // 交代は participants の1人目が入った選手、2人目が出た選手
  const txt = e => e.t === 'sub' ? `${icon.sub} ↑${esc(pn(e.p[0]))}${e.p[1] ? ` <span class="x">↓${esc(pn(e.p[1]))}</span>` : ''}` : `${icon[e.t]} ${esc(pn(e.p[0]))}`;
  return `<div class="card tl">${ev.map(e => {
    const home = e.team === m.h.id;
    return `<div class="e"><div class="l">${home ? txt(e) : ''}</div><div class="m">${esc(e.min)}</div><div>${home ? '' : txt(e)}</div></div>`;
  }).join('')}</div>`;
}

// ===== チーム =====
// ===== 監督 =====
// 就任日・退任日から「その日に指揮していた監督」や「シーズンごとの監督」を求める
const coachName = (co, c) => co.ja[c.full] || c.dn || c.full || c.n;
function coachesBetween(co, tid, start, end) {
  const list = ((co.teams || {})[tid] || {}).list || [];
  return list.filter(c => c.from && c.from <= end && (!c.to || c.to >= start));
}
function coachAt(co, tid, date) {
  const hit = coachesBetween(co, tid, date, date);
  return hit.length ? hit[hit.length - 1] : null;
}
const ym = d => d ? (d.length === 4 ? `${d}年` : `${+d.slice(0, 4)}年${+d.slice(5, 7)}月`) : '';
// 就任・退任の表示（年だけ分かっている日付は「〇年」）
const cFrom = c => ym(c.fromY || c.from), cTo = c => ym(c.toY || c.to);
const coachAvatar = c => c.photo ? `<img class="av" src="${esc(c.photo)}" alt="" loading="lazy" onerror="this.className='av none';this.removeAttribute('src')">` : '<span class="av none"></span>';

function coachHtml(co, id, all) {
  const list = ((co.teams || {})[id] || {}).list;
  if (!list) return '';
  const today = new Date().toISOString().slice(0, 10);
  const now = coachAt(co, id, today);
  // シーズンごと：そのシーズンにこのチームが戦った最初と最後の試合の間に指揮した監督
  const seasons = new Map();
  for (const x of all) {
    const c = comp(x.cid), s = c.seasons.find(v => v.y === x.y);
    const key = `${x.y}`;
    const e = seasons.get(key) || { label: s ? s.label : x.y, start: x.m.d.slice(0, 10), end: x.m.d.slice(0, 10) };
    if (x.m.d.slice(0, 10) < e.start) e.start = x.m.d.slice(0, 10);
    if (x.m.d.slice(0, 10) > e.end) e.end = x.m.d.slice(0, 10);
    seasons.set(key, e);
  }
  const rows = [...seasons.entries()].sort((a, b) => b[0] - a[0]).map(([, e]) => {
    const cs = coachesBetween(co, id, e.start, e.end > today ? today : e.end);
    const names = cs.length
      ? cs.map(c => `${esc(coachName(co, c))}${cs.length > 1 ? `<span class="x">（${c.from >= e.start ? cFrom(c) : ''}〜${c.to && c.to <= e.end ? cTo(c) : ''}）</span>` : ''}`).join(' → ')
      : '<span class="x">不明</span>';
    return `<div class="p"><span class="x yr">${esc(e.label)}</span><span class="grow">${names}</span></div>`;
  }).join('');
  return `<h2>監督</h2>
    ${now ? `<div class="card plist"><div class="p">${coachAvatar(now)}<span class="grow"><b>${esc(coachName(co, now))}</b><br><span class="x">${[country(now.nat), now.from ? `${cFrom(now)}就任` : ''].filter(Boolean).map(esc).join(' · ')}</span></span></div></div>` : ''}
    ${rows ? `<h3>シーズンごとの監督</h3><div class="card plist">${rows}</div>` : ''}`;
}

async function viewTeam(id, tab) {
  setNav(null);
  const t = team(id);
  const roster = await load(`t/${id}.json`);
  const all = [];
  await Promise.all(INDEX.comps.flatMap(c => c.seasons.map(async s => {
    const ms = (await load(`${c.id}/${s.y}/matches.json`)) || [];
    ms.filter(m => m.h.id === id || m.a.id === id).forEach(m => all.push({ m, cid: c.id, y: s.y }));
  })));
  all.sort((a, b) => a.m.d.localeCompare(b.m.d));
  if (!tab) tab = 'players';
  const co = (await load('coaches.json')) || { teams: {}, ja: {} };

  let body = '';
  if (tab === 'coach') {
    body = coachHtml(co, id, all) || '<p class="empty">監督のデータはまだありません</p>';
  } else if (tab === 'players') {
    if (!roster) body = '<p class="empty">選手一覧はまだありません</p>';
    else body = Object.entries(POS_GROUP).map(([k, label]) => {
      const ps = roster.players.filter(p => (p.pos || '').startsWith(k)).sort((a, b) => (+a.num || 99) - (+b.num || 99));
      return ps.length ? `<h3>${label}</h3><div class="card plist">${ps.map(p => `<a class="p" href="${p.id ? `#/p/${p.id}` : `#/pn/${encodeURIComponent(p.n)}`}"><span class="no">${esc(p.num || '')}</span>${avatar(p)}<span class="grow">${esc(pn(p.n))}</span><span class="x">${esc(country(p.nat))}${p.age ? ` · ${p.age}歳` : ''}</span></a>`).join('')}</div>` : '';
    }).join('');
  } else {
    const next = all.filter(x => x.m.s !== 'post');
    const done = all.filter(x => x.m.s === 'post').reverse();
    const row = x => matchRow(x.m, x.cid, x.y, { compTag: true });
    body = (next.length ? `<h2>これからの試合</h2><div class="card">${next.slice(0, 10).map(x => `<div class="daylabel">${fmtDay(x.m.d)}</div>${row(x)}`).join('')}</div>` : '') +
      (done.length ? `<h2>結果</h2><div class="card">${done.map(x => `<div class="daylabel">${fmtDay(x.m.d)}</div>${row(x)}`).join('')}</div>` : '');
    if (!body) body = '<p class="empty">試合はありません</p>';
  }
  app.innerHTML = `
    <button class="back" onclick="document.getElementById('backbtn').click()">‹ 戻る</button>
    <div class="pagehead">${logo(id, 'xl')}<div><h1>${esc(t.n)}</h1><div class="sub">${esc(t.en)}${roster ? ` · ${roster.players.length}人` : ''}</div></div></div>
    ${tabs([['players', '選手'], ['coach', '監督'], ['games', '試合']], tab)}<div id="body">${body}</div>${footer()}`;
  bindTabs(app, k => { history.replaceState(history.state, '', `#/t/${id}/${k}`); viewTeam(id, k); });
}

function footer() {
  return '<p class="foot">データ：ESPN ／ 写真：API-Football・Wikimedia Commons（<a href="#/credits">写真のクレジット</a>）<br>時刻はすべて日本時間</p>';
}

// 写真の撮影者・ライセンス（Wikimedia Commons の写真は表示が必要）
const photoIsAF = name => (PHOTOS[name] || '').includes('api-sports.io');
// ===== 選手 =====
const POS_JA = { G: 'ゴールキーパー', D: 'ディフェンダー', M: 'ミッドフィルダー', F: 'フォワード' };
const COMP_JA = [
  [/Champions League/i, 'チャンピオンズリーグ'], [/Europa League/i, 'ヨーロッパリーグ'], [/Conference League/i, 'カンファレンスリーグ'],
  [/UEFA Super Cup/i, 'UEFAスーパーカップ'], [/Nations League/i, 'ネーションズリーグ'], [/World Cup Qualifying/i, 'W杯予選'],
  [/World Cup/i, 'W杯'], [/European Championship Qualifying|Euro Qualifying/i, 'EURO予選'], [/European Championship|EURO/i, 'EURO'],
  [/International Friendly/i, '国際親善試合'], [/Friendly/i, '親善試合'],
  [/English Premier League|^Premier League/i, 'プレミアリーグ'], [/Carabao|League Cup/i, 'カラバオ杯'], [/FA Cup/i, 'FAカップ'],
  [/Community Shield/i, 'コミュニティ・シールド'], [/LALIGA|La Liga|Spanish/i, 'ラ・リーガ'], [/Copa del Rey/i, 'コパ・デル・レイ'],
  [/Supercopa/i, 'スーペルコパ'], [/Ligue 1/i, 'リーグ・アン'], [/Coupe de France/i, 'クープ・ドゥ・フランス'],
  [/Trophée des Champions/i, 'トロフェ・デ・シャンピオン'], [/Serie A/i, 'セリエA'], [/Bundesliga/i, 'ブンデスリーガ'], [/Copa Am[eé]rica/i, 'コパ・アメリカ'],
];
function compJa(name) {
  const n = (name || '').replace(/^\d{4}(-\d{2})?\s+/, '');
  for (const [re, ja] of COMP_JA) if (re.test(n)) return ja || n;
  return n;
}
async function viewPlayerByName(name) {
  const idx = (await load('players_index.json')) || {};
  if (idx[name]) { history.replaceState(history.state, '', `#/p/${idx[name]}`); return viewPlayer(idx[name]); }
  setNav(null);
  app.innerHTML = `<button class="back" onclick="document.getElementById('backbtn').click()">‹ 戻る</button>
    <div class="card phead">${avatar({ n: name }, 'av xl')}<div><h1>${esc(pn(name))}</h1><div class="sub">${esc(name)}</div></div></div>
    <p class="empty">この選手は現在の所属チームの選手一覧に無いため、詳しいデータはありません</p>${footer()}`;
}
async function viewPlayer(id) {
  setNav(null);
  const p = await load(`p/${id}.json`);
  if (!p) { app.innerHTML = '<p class="empty">選手が見つかりません</p>'; return; }
  const t = team(p.team), nt = p.natTeam ? team(p.natTeam) : null;
  const c = !photoIsAF(p.n) ? CREDIT[p.n] : null;
  const credit = PHOTOS[p.n] ? `<div class="note">${c ? `写真：${esc(c.a)}／<a href="${esc(c.u || c.f)}" target="_blank" rel="noopener">${esc(c.l)}</a>（<a href="${esc(c.f)}" target="_blank" rel="noopener">Wikimedia Commons</a>）` : '写真：API-Football'}</div>` : '';
  const dob = p.dob ? `${p.dob.replace(/^(\d+)-0?(\d+)-0?(\d+)$/, '$1年$2月$3日')}生まれ` : '';
  const head = `<div class="card phead">${avatar(p, 'av xl')}<div>
      <h1>${esc(pn(p.n))}</h1><div class="sub">${esc(p.n)}</div>
      <div class="pinfo"><a href="#/t/${p.team}">${logo(p.team)}${esc(t.n)}</a>${nt && p.natTeam !== p.team ? `<a href="#/t/${p.natTeam}">${logo(p.natTeam)}${esc(nt.n)}代表</a>` : ''}</div>
      <div class="sub">${esc(country(p.nat))}${p.pos ? ` · ${POS_JA[p.pos[0]] || p.pos}` : ''}${p.num ? ` · 背番号${esc(p.num)}` : ''}${p.age ? ` · ${p.age}歳` : ''}</div>
      ${dob ? `<div class="sub">${dob}</div>` : ''}${credit}</div></div>`;

  // 今季（ESPN・全大会）
  const now = ((p.espn || {}).now || []).filter(r => !/Club Friendly/i.test(r.comp));
  const sum = k => now.filter(r => !/Friendly/i.test(r.comp)).reduce((a, r) => a + (r[k] || 0), 0);
  const nowHtml = now.length ? `<div class="card"><table class="st"><thead><tr><th style="text-align:left">大会</th><th>先発</th><th>ゴール</th><th>アシスト</th></tr></thead><tbody>
      ${now.map(r => `<tr><td class="tm">${esc(compJa(r.comp))}</td><td>${r.starts}</td><td>${r.totalGoals}</td><td>${r.goalAssists}</td></tr>`).join('')}
      <tr class="total"><td class="tm">合計（親善試合を除く）</td><td>${sum('starts')}</td><td>${sum('totalGoals')}</td><td>${sum('goalAssists')}</td></tr></tbody></table></div>` : '<p class="empty">今季の記録はまだありません</p>';

  // 通算（Wikidata：クラブ別の出場数・ゴール数）
  const career = p.career || [];
  const clubs = career.filter(x => x.k === 'club'), nat = career.filter(x => x.k === 'nat'), youth = career.filter(x => x.k === 'youth' || x.k === 'natY');
  const tot = (list, k) => list.reduce((a, x) => a + (x[k] || 0), 0);
  const lg = (p.espn || {}).league;
  // ESPN のシーズン別データは項目名が略称（STRT・G・A）のことがある
  const lgSeasons = lg ? lg.seasons.map(s => ({ season: s.season, starts: s.starts ?? s.STRT ?? 0, totalGoals: s.totalGoals ?? s.G ?? 0, goalAssists: s.goalAssists ?? s.A ?? 0 }))
    .filter(s => s.starts || s.totalGoals || s.goalAssists) : [];
  const lgSum = k => lgSeasons.reduce((a, s) => a + (s[k] || 0), 0);
  const totals = `<div class="tiles">
      ${clubs.length ? `<div class="tile"><b>${tot(clubs, 'apps')}</b><span>クラブ通算出場</span></div><div class="tile"><b>${tot(clubs, 'goals')}</b><span>クラブ通算ゴール</span></div>` : ''}
      ${nat.length ? `<div class="tile"><b>${tot(nat, 'apps')}</b><span>代表出場</span></div><div class="tile"><b>${tot(nat, 'goals')}</b><span>代表ゴール</span></div>` : ''}
      ${lgSeasons.length ? `<div class="tile"><b>${lgSum('goalAssists')}</b><span>${esc(compJa(lg.name))}通算アシスト</span></div>` : ''}
    </div>`;
  const yr = x => `${x.from || '?'}〜${x.to || ''}`;
  const row = x => `<div class="p"><span class="x yr">${yr(x)}</span><span class="grow">${esc(x.c)}</span><span class="x">${x.apps != null ? `${x.apps}試合` : ''}${x.goals != null ? ` ${x.goals}点` : ''}</span></div>`;
  const careerHtml = career.length ? `
      <h3>クラブ</h3><div class="card plist">${clubs.map(row).join('') || '<div class="p">記録なし</div>'}</div>
      ${nat.length ? `<h3>代表</h3><div class="card plist">${nat.map(row).join('')}</div>` : ''}
      ${youth.length ? `<details><summary>ユース・下部組織・年代別代表（${youth.length}）</summary><div class="card plist">${youth.map(row).join('')}</div></details>` : ''}`
    : '<p class="empty">所属履歴のデータがありません</p>';
  const lgHtml = lgSeasons.length ? `<h2>${esc(compJa(lg.name))}のシーズン別成績</h2><div class="card"><table class="st"><thead><tr><th style="text-align:left">シーズン</th><th>先発</th><th>ゴール</th><th>アシスト</th></tr></thead><tbody>
      ${lgSeasons.map(s => `<tr><td class="tm">${esc((s.season.match(/\d{4}(-\d{2})?/) || [''])[0])}</td><td>${s.starts}</td><td>${s.totalGoals}</td><td>${s.goalAssists}</td></tr>`).join('')}
      <tr class="total"><td class="tm">通算</td><td>${lgSum('starts')}</td><td>${lgSum('totalGoals')}</td><td>${lgSum('goalAssists')}</td></tr></tbody></table></div>` : '';

  app.innerHTML = `<button class="back" onclick="document.getElementById('backbtn').click()">‹ 戻る</button>${head}
    <h2>今季の成績（全大会）</h2>${nowHtml}
    <h2>通算成績</h2>${totals}
    <h2>今までの所属</h2>${careerHtml}
    ${lgHtml}
    <p class="note" style="margin-top:16px">今季の成績・シーズン別成績：ESPN（先発数のみで途中出場は含みません）／所属履歴・クラブ別の出場数とゴール数：Wikidata（Wikipedia の記録。主にリーグ戦の数で、更新が遅れることがあります）。毎日0時に更新。</p>
    ${footer()}`;
}

function viewCredits() {
  setNav(null);
  const rows = Object.entries(CREDIT).filter(([n]) => !photoIsAF(n))
    .sort((a, b) => pn(a[0]).localeCompare(pn(b[0]), 'ja'))
    .map(([n, c]) => `<div class="p"><img class="av" src="${esc(PHOTOS[n])}" alt="" loading="lazy"><span class="grow">${esc(pn(n))}<br><span class="x">${esc(c.a)}／<a href="${esc(c.u || c.f)}" target="_blank" rel="noopener">${esc(c.l)}</a>・<a href="${esc(c.f)}" target="_blank" rel="noopener">元の写真</a></span></span></div>`);
  app.innerHTML = `<button class="back" onclick="document.getElementById('backbtn').click()">‹ 戻る</button>
    <h2>写真のクレジット</h2>
    <p class="explain" style="padding:0">選手の顔写真の一部は Wikimedia Commons の写真を、各ライセンス（クリエイティブ・コモンズなど）に従って縮小して表示しています。写真をタップしても撮影者とライセンスを確認できます。その他の写真は API-Football の提供です。</p>
    <div class="card plist">${rows.join('')}</div>${footer()}`;
}

// ===== ルーティング =====
// 画面上部の戻るボタン：アプリ内で移動してきたなら1つ前へ、そうでなければ（直接開いたときなど）ホームへ
// 各ページの履歴に「アプリ内で何ページ目か（d）」を記録しておく
let lastDepth = 0;
document.getElementById('backbtn').addEventListener('click', () => {
  if (history.state && history.state.d > 0) history.back();
  else location.hash = '#/';
});

async function route() {
  const p = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  document.getElementById('backbtn').hidden = p.length === 0;
  window.scrollTo(0, 0);
  try {
    if (p[0] === 'c') await viewComp(p[1], p[2], p[3]);
    else if (p[0] === 'm') await viewMatch(p[1], p[2], p[3], p[4]);
    else if (p[0] === 't') await viewTeam(p[1], p[2]);
    else if (p[0] === 'credits') viewCredits();
    else if (p[0] === 'p') await viewPlayer(p[1]);
    else if (p[0] === 'pn') await viewPlayerByName(p.slice(1).join('/'));
    else await viewHome();
  } catch (e) {
    console.error(e);
    app.innerHTML = '<p class="empty">表示できませんでした。再読み込みしてください。</p>';
  }
}

(async function init() {
  let names;
  let photos;
  [INDEX, TEAMS, names, photos] = await Promise.all([load('index.json'), load('teams.json'), load('names_ja.json'), load('photos.json')]);
  if (names) NAMES = names;
  if (photos) {
    PHOTOS = { ...(photos.wiki || {}), ...(photos.players || {}) }; // API-Football の写真を優先
    CREDIT = photos.credit || {};
  }
  if (!INDEX) { app.innerHTML = '<p class="empty">データがまだありません</p>'; return; }
  TEAMS = TEAMS || {};
  if (INDEX.updated) {
    document.getElementById('updated').textContent = '更新 ' + new Date(INDEX.updated).toLocaleString('ja-JP', { ...JST, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  history.replaceState({ d: 0 }, '');
  window.addEventListener('hashchange', () => {
    if (!history.state || history.state.d == null) history.replaceState({ d: lastDepth + 1 }, '');
    lastDepth = history.state.d;
    route();
  });
  route();
})();
