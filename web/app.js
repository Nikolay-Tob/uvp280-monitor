'use strict';
/* УВП-280 Монитор — интерфейс. Без внешних библиотек. */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(path, body) {
  const o = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch('/api/' + path, o);
  let j = {};
  try { j = await r.json(); } catch (e) { }
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
function toast(msg, err) {
  const t = $('#toast'), d = document.createElement('div');
  d.textContent = msg; if (err) d.className = 'err'; t.appendChild(d);
  setTimeout(() => d.remove(), err ? 7000 : 3500);
}
function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem('uvp.' + k)); localStorage.setItem('uvp.' + k, JSON.stringify(v)); } catch (e) { return null; } }

/* ---------------- единицы измерения (значения прибора — в СИ) ---------------- */
const UNITS = {
  pressure: { def: 'МПа', list: [['МПа', v => v * 1e-6, 4], ['кПа', v => v * 1e-3, 2], ['бар', v => v * 1e-5, 4], ['кгс/см²', v => v / 98066.5, 4], ['мм рт.ст.', v => v / 133.322, 1], ['Па', v => v, 0]], title: 'Давление' },
  temp: { def: '°C', list: [['°C', v => v, 2], ['K', v => v + 273.15, 2], ['°F', v => v * 9 / 5 + 32, 2]], title: 'Температура' },
  mflow: { def: 'кг/ч', list: [['кг/ч', v => v * 3600, 2], ['т/ч', v => v * 3.6, 4], ['кг/с', v => v, 4]], title: 'Массовый расход' },
  vflow: { def: 'м³/ч', list: [['м³/ч', v => v * 3600, 3], ['м³/с', v => v, 6], ['л/с', v => v * 1000, 3], ['тыс. м³/ч', v => v * 3.6, 4]], title: 'Объёмный расход' },
  mass: { def: 'т', list: [['т', v => v / 1000, 3], ['кг', v => v, 1]], title: 'Масса' },
  vol: { def: 'м³', list: [['м³', v => v, 2], ['тыс. м³', v => v / 1000, 4]], title: 'Объём' },
  power: { def: 'кВт', list: [['кВт', v => v / 1e3, 2], ['Вт', v => v, 0], ['МВт', v => v / 1e6, 4], ['Гкал/ч', v => v / 1.163e6, 4]], title: 'Тепловая мощность' },
  energy: { def: 'МДж', list: [['МДж', v => v / 1e6, 3], ['ГДж', v => v / 1e9, 5], ['Гкал', v => v / 4.1868e9, 5], ['кВт·ч', v => v / 3.6e6, 2], ['Дж', v => v, 0]], title: 'Тепловая энергия' },
  dens: { def: 'кг/м³', list: [['кг/м³', v => v, 4]], title: 'Плотность' },
  enthalpy: { def: 'кДж/кг', list: [['кДж/кг', v => v / 1e3, 3], ['Дж/кг', v => v, 0]], title: 'Энтальпия' },
};
function unitSel(q) { const s = store('units') || {}; const u = UNITS[q]; return u.list.find(x => x[0] === s[q]) || u.list.find(x => x[0] === u.def) || u.list[0]; }
function nf(v, d) { return v.toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: d }); }
function num(v, d = 3) { return v == null ? '—' : nf(v, d); }
function secs(v) { if (v == null) return '—'; v = Math.max(0, v | 0); const h = Math.floor(v / 3600), m = Math.floor(v % 3600 / 60), s = v % 60; return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0'); }
function q(qty, v, withUnit = true) {
  if (v == null) return '—';
  if (qty === 'secs') return secs(v);
  if (!qty || !UNITS[qty]) return nf(v, 3);
  const u = unitSel(qty);
  return nf(u[1](v), u[2]) + (withUnit ? ' ' + u[0] : '');
}
function qnum(qty, v) { return v == null ? '—' : (UNITS[qty] ? nf(unitSel(qty)[1](v), unitSel(qty)[2]) : nf(v, 3)); }

/* ---------------- состояние ---------------- */
const S = { devices: [], dev: null, route: { name: 'summary' }, screen: null, version: '' };
const TABS = [['main', 'Главная'], ['pipes', 'Трубопроводы'], ['sensors', 'Датчики'], ['oil', 'Учёт нефти'], ['archive', 'Архивы'], ['settings', 'Настройки']];

function parseRoute() {
  const p = (location.hash || '#/summary').slice(2).split('/').map(decodeURIComponent);
  if (p[0] === 'd' && p[1]) return { name: 'dev', id: p[1], tab: p[2] || 'main', tp: p[3] ? +p[3] : null };
  if (p[0] === 'devices') return { name: 'devices' };
  if (p[0] === 'units') return { name: 'units' };
  return { name: 'summary' };
}
function go(h) { location.hash = h; }

function ago(ts) { if (!ts) return 'нет данных'; const s = Math.round(Date.now() / 1000 - ts); return s < 3 ? 'сейчас' : s < 90 ? s + ' с назад' : Math.round(s / 60) + ' мин назад'; }
function devStatus(d) {
  if (d.paused) return ['warn', 'опрос на паузе'];
  if (!d.online) return ['bad', d.error ? 'нет связи' : 'подключение…'];
  if (d.crit >= 3) return ['bad', 'аварийные ситуации: ' + d.errors];
  if (d.errors > 0) return ['warn', 'ошибок на ТП: ' + d.errors];
  return ['ok', 'в работе' + (d.ms != null ? ' · ' + d.ms + ' мс' : '')];
}
function fmtTime(iso) { if (!iso) return '—'; const d = new Date(iso); return d.toLocaleTimeString('ru-RU'); }
function fmtDate(iso) { if (!iso) return ''; const d = new Date(iso); return d.toLocaleDateString('ru-RU', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' }); }
function diffText(s) { if (s == null) return '—'; return (s > 0 ? '+' : s < 0 ? '−' : '') + Math.abs(s) + ' с'; }
function diffCls(s) { if (s == null) return ''; const a = Math.abs(s); return a <= 5 ? 'ok' : a <= 60 ? 'warn' : 'bad'; }

/* ---------------- боковая панель ---------------- */
function renderNav() {
  const r = S.route;
  let h = `<a class="nav-i ${r.name === 'summary' ? 'on' : ''}" href="#/summary"><span class="t">▦ Сводка по всем приборам</span></a>
  <div class="nav-h"><span>ПРИБОРЫ</span><a href="#/devices">Изменить список</a></div>`;
  for (const d of S.devices) {
    const [c, t] = devStatus(d);
    h += `<a class="nav-i ${r.name === 'dev' && r.id === d.id ? 'on' : ''}" href="#/d/${encodeURIComponent(d.id)}/main">
      <span class="t"><span class="dot ${c}"></span>${esc(d.name)}</span>
      <span class="a mono">${esc(d.host)}:${d.port}${d.unit !== 1 ? ' · unit ' + d.unit : ''}</span>
      <span class="s" style="color:${c === 'ok' ? '#7FD6A3' : c === 'warn' ? '#F2B84B' : '#F59A90'}">${esc(t)}</span></a>`;
  }
  h += `<a class="nav-add" href="#/devices">+ Добавить прибор</a>
  <a class="nav-i ${r.name === 'units' ? 'on' : ''}" href="#/units"><span class="t">⚙ Единицы измерения</span></a>`;
  const nav = $('#nav');
  if (nav.dataset.sig !== h) { nav.innerHTML = h; nav.dataset.sig = h; }
}

/* ---------------- сводка ---------------- */
const Summary = {
  mount(root) { root.innerHTML = `<div class="hdr"><div><h1>Сводка</h1><div class="meta">Все приборы из списка. Нажмите на карточку, чтобы открыть прибор.</div></div></div><div class="grid s" id="sg"></div>`; },
  update() {
    const g = $('#sg'); if (!g) return;
    if (!S.devices.length) { g.innerHTML = `<div class="card">Список приборов пуст. <a href="#/devices" style="text-decoration:underline">Добавить прибор</a></div>`; return; }
    g.innerHTML = S.devices.map(d => {
      const [c, t] = devStatus(d);
      const cls = c === 'ok' ? '' : c;
      const pipes = d.pipes.slice(0, 8).map(p => {
        const flow = (p.type === 'Датчик количества') ? '' : (p.qv_std != null && Math.abs(p.qv_std) > 0 ? q('vflow', p.qv_std) + ' (ст.у.)' : (p.q_m ? q('mflow', p.q_m) : ''));
        return `<div class="small" style="display:flex;flex-direction:column"><span>${p.n_err ? '<span style="color:var(--warn)">▲</span> ' : ''}<b>${esc(p.name)}</b></span><span class="mono muted">${p.p_abs != null ? q('pressure', p.p_abs) : '—'} · ${p.t != null ? q('temp', p.t) : '—'}${flow ? ' · ' + flow : ''}</span></div>`;
      }).join('');
      return `<a class="card cardlink ${cls} ${d.online ? '' : 'off'}" href="#/d/${encodeURIComponent(d.id)}/main">
        <div class="row"><h3>${esc(d.name)}</h3><span class="pill ${c}">${esc(t)}</span></div>
        <div class="meta mono">${esc(d.host)}:${d.port} · unit ${d.unit}${d.serial ? ' · № ' + d.serial : ''}${d.oil ? ' · нефть' : ''}</div>
        ${d.online ? `<div class="two"><div><div class="l">Время прибора</div><div class="v mono">${fmtTime(d.clock)}</div></div>
        <div><div class="l">Прибор − ПК</div><div class="v mono ${diffCls(d.diff) === 'ok' ? '' : ''}" style="color:${d.diff == null ? '' : Math.abs(d.diff) > 60 ? 'var(--bad)' : Math.abs(d.diff) > 5 ? 'var(--warn)' : 'var(--ok)'}">${diffText(d.diff)}</div></div></div>
        <div style="display:flex;flex-direction:column;gap:4px">${pipes || '<span class="muted small">трубопроводы не описаны</span>'}</div>`
          : `<div class="box ${d.error ? 'bad' : ''}">${esc(d.error || 'Ожидание ответа…')}</div>`}
      </a>`;
    }).join('');
  }
};

/* ---------------- список приборов ---------------- */
const Devices = {
  mount(root) {
    root.innerHTML = `<div class="hdr"><div><h1>Список приборов</h1><div class="meta">Адреса хранятся на этом компьютере. Сетевые настройки самого прибора по Modbus не меняются.</div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button id="imp">Импорт…</button><button id="exp">Экспорт</button><input type="file" id="file" accept=".json" hidden></div></div>
    <div class="card"><h2 id="ftitle">Новый прибор</h2><div class="frm">
      <label class="f">Название<input id="f-name" style="width:220px" placeholder="Например: Котельная №2"></label>
      <label class="f">IP-адрес<input id="f-host" style="width:170px" placeholder="192.168.0.154"></label>
      <label class="f">Порт<input id="f-port" type="number" style="width:90px" value="502"></label>
      <label class="f">Unit ID<input id="f-unit" type="number" style="width:80px" value="1"></label>
      <button id="f-test">Проверить связь</button><button class="pri" id="f-save">Добавить</button><button id="f-cancel" hidden>Отмена</button></div>
      <div id="f-res" class="small"></div></div>
    <div class="sw"><table><thead><tr><th>Название</th><th>Адрес</th><th>Состояние</th><th></th></tr></thead><tbody id="dl"></tbody></table></div>`;
    this.edit = null;
    $('#f-save').onclick = () => this.save();
    $('#f-cancel').onclick = () => this.reset();
    $('#f-test').onclick = () => this.test();
    $('#exp').onclick = async () => { const j = await api('devices/export'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(j, null, 1)], { type: 'application/json' })); a.download = 'uvp280-devices.json'; a.click(); };
    $('#imp').onclick = () => $('#file').click();
    $('#file').onchange = async e => { const f = e.target.files[0]; if (!f) return; try { const j = JSON.parse(await f.text()); const r = await api('devices/import', { data: j }); toast('Добавлено приборов: ' + r.imported); await refresh(); } catch (x) { toast('Не удалось прочитать файл: ' + x.message, true); } e.target.value = ''; };
    this.update(true);
  },
  vals() { return { name: $('#f-name').value.trim(), host: $('#f-host').value.trim(), port: +$('#f-port').value || 502, unit: +$('#f-unit').value }; },
  reset() { this.edit = null; $('#f-name').value = ''; $('#f-host').value = ''; $('#f-port').value = 502; $('#f-unit').value = 1; $('#ftitle').textContent = 'Новый прибор'; $('#f-save').textContent = 'Добавить'; $('#f-cancel').hidden = true; $('#f-res').textContent = ''; },
  async save() {
    const v = this.vals();
    try { if (this.edit) await api('device/' + this.edit, v); else await api('devices', v); this.reset(); await refresh(); toast('Сохранено'); } catch (e) { toast(e.message, true); }
  },
  async test() {
    const r = $('#f-res'); r.textContent = 'Проверка…';
    try {
      const j = await api('test', this.vals());
      if (!j.ok) { r.innerHTML = `<span style="color:var(--bad)">Нет ответа: ${esc(j.error)}</span>`; return; }
      r.innerHTML = `<span style="color:var(--ok)">✓ Прибор отвечает.</span> Серийный № ${esc(j.info.serial ?? '?')}, ПО ${esc(j.info.fw ?? '?')}, порядок байт ${esc(j.info.order)}${j.info.order_ok ? '' : ' (контрольные константы не сошлись)'}, трубопроводов ${j.info.pipes.length}, время прибора ${esc((j.clock || '').replace('T', ' '))}`;
    } catch (e) { r.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  },
  update(force) {
    const tb = $('#dl'); if (!tb) return;
    const sig = JSON.stringify(S.devices.map(d => [d.id, d.name, d.host, d.port, d.unit, d.online, d.paused]));
    if (!force && tb.dataset.sig === sig) return; tb.dataset.sig = sig;
    tb.innerHTML = S.devices.map(d => { const [c, t] = devStatus(d); return `<tr><td><b>${esc(d.name)}</b></td><td class="mono">${esc(d.host)}:${d.port} · unit ${d.unit}</td><td><span class="pill ${c}">${esc(t)}</span></td>
      <td style="white-space:nowrap"><button data-o="${d.id}">Открыть</button> <button data-e="${d.id}">Изменить</button> <button class="dng" data-d="${d.id}">Удалить</button></td></tr>`; }).join('') || '<tr><td colspan="4" class="muted">Приборов нет — добавьте первый выше.</td></tr>';
    tb.querySelectorAll('[data-o]').forEach(b => b.onclick = () => go('#/d/' + b.dataset.o + '/main'));
    tb.querySelectorAll('[data-e]').forEach(b => b.onclick = () => { const d = S.devices.find(x => x.id === b.dataset.e); this.edit = d.id; $('#f-name').value = d.name; $('#f-host').value = d.host; $('#f-port').value = d.port; $('#f-unit').value = d.unit; $('#ftitle').textContent = 'Изменить прибор'; $('#f-save').textContent = 'Сохранить'; $('#f-cancel').hidden = false; window.scrollTo(0, 0); });
    tb.querySelectorAll('[data-d]').forEach(b => b.onclick = async () => { const d = S.devices.find(x => x.id === b.dataset.d); if (!confirm('Удалить «' + d.name + '» из списка?')) return; try { await api('device/' + d.id + '/delete', {}); await refresh(); } catch (e) { toast(e.message, true); } });
  }
};

/* ---------------- единицы ---------------- */
const Units = {
  mount(root) {
    const s = store('units') || {};
    root.innerHTML = `<div class="hdr"><div><h1>Единицы измерения</h1><div class="meta">Прибор отдаёт значения в СИ (Па, °C, кг/с, кг, м³, Вт, Дж). Здесь выбирается, в чём они показываются на экране. Давление по умолчанию — МПа.</div></div></div>
    <div class="card"><div class="frm">${Object.entries(UNITS).map(([k, u]) => `<label class="f">${u.title}<select data-q="${k}">${u.list.map(x => `<option ${(s[k] || u.def) === x[0] ? 'selected' : ''}>${x[0]}</option>`).join('')}</select></label>`).join('')}</div>
    <div class="small muted">Настройка хранится в этом окне и применяется сразу ко всем приборам. Величины, выдаваемые прибором в своих единицах (датчики, итоги СИКН, архивы), показываются как есть.</div></div>`;
    root.querySelectorAll('select').forEach(sel => sel.onchange = () => { const c = store('units') || {}; c[sel.dataset.q] = sel.value; store('units', c); toast('Сохранено'); });
  },
  update() { }
};

/* ---------------- прибор: каркас ---------------- */
function tabsFor(d) {
  return TABS.filter(t => t[0] !== 'oil' || (d && d.caps && d.caps.oil));
}
const Dev = {
  mount(root, r) {
    this.r = r; this.d = null;
    root.innerHTML = `<div id="dh"></div><nav class="tabs" id="dt"></nav><div id="db" style="display:flex;flex-direction:column;gap:16px"></div>`;
    this.sub = DevTabs[r.tab] || DevTabs.main;
    this.subMounted = false;
  },
  update() {
    const r = S.route; const d = S.dev;
    const base = S.devices.find(x => x.id === r.id);
    if (!base) { $('#dh').innerHTML = '<div class="card">Прибор не найден.</div>'; return; }
    const dd = d || base; const [c, t] = devStatus(dd);
    const info = dd.info || {};
    $('#dh').innerHTML = `<div class="hdr"><div><h1>${esc(base.name)}</h1>
      <div class="meta mono">${info.serial ? 'зав. № ' + info.serial + ' · ' : ''}${info.fw ? 'ПО ' + esc(info.fw) + (info.fw_date ? ' от ' + esc(info.fw_date) : '') + ' · ' : ''}${esc(base.host)}:${base.port} · Unit ${base.unit}</div></div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span class="pill ${c}">● ${esc(t)}</span>
      <button id="pz">${base.paused ? 'Продолжить опрос' : 'Пауза опроса'}</button></div></div>`;
    $('#pz').onclick = async () => { await api('device/' + base.id + '/pause', { paused: !base.paused }); await refresh(); };
    const tabs = tabsFor(dd);
    const tsig = tabs.map(x => x[0]).join() + r.tab;
    const nt = $('#dt'); if (nt.dataset.sig !== tsig) { nt.dataset.sig = tsig; nt.innerHTML = tabs.map(x => `<a class="tab ${x[0] === r.tab ? 'on' : ''}" href="#/d/${encodeURIComponent(r.id)}/${x[0]}">${x[1]}</a>`).join(''); }
    if (!this.subMounted) { this.sub.mount($('#db'), dd); this.subMounted = true; }
    this.sub.update($('#db'), dd);
  }
};

/* ---------------- вкладки прибора ---------------- */
const DevTabs = {};

DevTabs.main = {
  mount(root) { root.innerHTML = `<section class="grid" id="m1"></section><section id="m2" style="display:flex;flex-direction:column;gap:12px"></section>`; },
  update(root, d) {
    if (!d.online && !d.clock) { $('#m1').innerHTML = `<div class="card"><div class="box bad">${esc(d.error || 'Подключение к прибору…')}</div></div>`; $('#m2').innerHTML = ''; return; }
    const n = d.ntp, info = d.info || {};
    const dc = diffCls(d.diff);
    let ntp = '';
    if (d.caps && d.caps.ntp && n) {
      ntp = `<div style="border-top:1px solid var(--line2);padding-top:10px;display:flex;flex-direction:column;gap:6px"><div class="row"><b>NTP-клиент прибора</b><span class="mono small">${n.ip ? 'сервер ' + esc(n.ip) : 'сервер не задан'}</span></div>
      <ul style="margin:0;padding:0;list-style:none;font-size:13px;display:flex;flex-direction:column;gap:3px">
      <li style="color:${n.link ? 'var(--ok)' : 'var(--bad)'}">${n.link ? '✓' : '✗'} соединение с сервером ${n.link ? 'установлено' : 'не установлено'}</li>
      <li style="color:${n.fresh ? 'var(--ok)' : 'var(--warn)'}">${n.fresh ? '✓' : '–'} данные ${n.fresh ? 'получены менее 30 с назад' : 'устарели'}</li>
      <li style="color:${n.auto ? 'var(--ok)' : 'var(--mut)'}">${n.auto ? '✓' : '–'} автосинхронизация ${n.auto ? 'включена' : 'выключена'}</li></ul>
      <div class="small muted">Разница прибор − NTP: <span class="mono" style="color:var(--ink)">${diffText(n.diff)}</span></div></div>`;
    } else if (d.caps) {
      ntp = `<div class="box small">В этой версии ПО прибора NTP-клиента нет (регистры не отвечают).</div>`;
    }
    const level = d.level_text ? d.level_text : '—';
    const errs = (d.pipes || []).filter(p => p.n_err);
    const pi = d.pipe_info || {};
    $('#m1').innerHTML = `
    <article class="card"><div class="row"><h2>Время прибора</h2><span class="pill ${dc}">${d.diff == null ? '' : Math.abs(d.diff) <= 5 ? 'в допуске ±5 с' : 'расхождение ' + diffText(d.diff)}</span></div>
      <div><div class="big mono">${fmtTime(d.clock)}</div><div class="muted">${esc(fmtDate(d.clock))}</div></div>
      <div class="two"><div><div class="l">Время этого ПК</div><div class="v mono">${fmtTime(d.pc_time)}</div></div><div><div class="l">Прибор − ПК</div><div class="v mono">${diffText(d.diff)}</div></div></div>
      ${ntp}
      <div class="small muted">Установка времени и настройка NTP появятся в следующей версии (потребуется вход под пользователем).</div></article>
    <article class="card"><h2>Состояние</h2>
      ${errs.length ? `<div class="box ${errs.some(p => p.crit >= 3) ? 'bad' : 'warn'}"><b>Ошибки на трубопроводах: ${errs.reduce((a, p) => a + (p.n_err || 0), 0)}</b>${errs.map(p => `<div><a style="text-decoration:underline" href="#/d/${encodeURIComponent(d.id)}/pipes/${p.tp}">${esc(p.name)}</a> — ошибок: ${p.n_err}, критичность ${p.crit}</div>`).join('')}</div>` : `<div class="box ok">Ошибок на трубопроводах нет</div>`}
      <dl class="kv"><dt>Уровень доступа</dt><dd>${esc(level)}</dd>
      <dt>Описано трубопроводов</dt><dd class="mono">${(info.pipes || []).length}${info.pipes && info.pipes.length ? ' (' + info.pipes.map(x => 'ТП' + x).join(', ') + ')' : ''}</dd>
      <dt>Логических входов</dt><dd class="mono">${d.caps ? d.caps.inputs : '—'}</dd>
      <dt>Учёт нефти</dt><dd>${d.caps && d.caps.oil ? 'есть' + (d.caps.kmh ? ', КМХ' : '') + (d.caps.sampler ? ', пробоотборник' : '') : 'нет'}</dd></dl>
      <div class="box small">Часы, отчёты и параметры читаются без авторизации.</div></article>
    <article class="card"><h2>Прибор и обмен</h2>
      <dl class="kv"><dt>Серийный номер</dt><dd class="mono">${esc(info.serial ?? '—')}</dd>
      <dt>Версия ПО</dt><dd class="mono">${esc(info.fw ?? '—')}${info.fw_date ? ' · ' + esc(info.fw_date) : ''}</dd>
      <dt>Modbus TCP</dt><dd class="mono">порт ${d.port} · Unit ${d.unit}</dd>
      <dt>Порядок байт</dt><dd style="color:${info.order_ok ? 'var(--ok)' : 'var(--bad)'}">${info.order_ok ? '✓ ' : '✗ '}${esc(info.order || '')}</dd>
      <dt>Отклик</dt><dd class="mono">${d.ms != null ? d.ms + ' мс' : '—'}</dd>
      <dt>Переподключений</dt><dd class="mono">${d.reconnects ?? 0}</dd></dl>
      <div class="small muted">Порядок байт определяется автоматически по контрольным константам HR110/112/114. Сетевые настройки прибора (IP, маска) по Modbus не читаются.</div></article>`;
    $('#m2').innerHTML = `<div class="row"><h3>Трубопроводы</h3><a style="text-decoration:underline" href="#/d/${encodeURIComponent(d.id)}/pipes">Все трубопроводы</a></div>
      <div class="grid s">${(d.pipes || []).map(p => pipeCard(d, p)).join('') || '<div class="muted">Трубопроводы не описаны</div>'}</div>`;
  }
};

function pipeKind(t) { return t === 'УУН' || t === 'КМХ' ? 'oil' : t === 'Датчик количества' ? 'cnt' : 'flow'; }
function pipeCard(d, p) {
  const pf = (d.pipes_full || {})[p.tp] || p;
  const k = pipeKind(p.type);
  const cls = p.crit >= 3 ? 'bad' : p.n_err ? 'warn' : '';
  let body;
  if (k === 'oil') body = `<div class="small muted">${p.type === 'УУН' ? 'Узел учёта нефти — данные на вкладке «Учёт нефти»' : 'Контроль метрологических характеристик — вкладка «Учёт нефти»'}</div>`;
  else body = `<dl class="kv small"><dt>Давление</dt><dd class="mono">${q('pressure', pf.p_abs)}</dd><dt>Температура</dt><dd class="mono">${q('temp', pf.t)}</dd>
    ${k === 'flow' ? `<dt>Расход (ст.у.)</dt><dd class="mono">${q('vflow', pf.qv_std)}</dd>` : `<dt>Объём (р.у.)</dt><dd class="mono">${q('vol', pf.s_v_wc)}</dd>`}
    <dt>Объём (ст.у.)</dt><dd class="mono">${q('vol', pf.v_std)}</dd></dl>`;
  return `<a class="card cardlink ${cls}" href="#/d/${encodeURIComponent(d.id)}/pipes/${p.tp}"><div class="row"><b>${esc(p.name)}</b><span class="chip ${cls}">${p.n_err ? 'ошибок: ' + p.n_err : 'ТП' + p.tp}</span></div><div class="small muted">${esc(p.type || '')}</div>${body}</a>`;
}

/* ---- трубопроводы ---- */
const PIPE_GROUPS = [
  ['Параметры среды', ['p_g', 'p_abs', 'dp', 't']],
  ['Расход', ['q_m', 'qv_std', 'qv_wc', 's_qm', 's_qv_std', 's_qv_wc']],
  ['Накопленные итоги', ['m', 'v_std', 'v_wc', 's_m', 's_v_wc']],
  ['Плотность и энергия', ['rho_std', 'rho_wc', 'h', 'pow', 'en']],
  ['Состояние', ['t_ok', 't_nss', 'n_err', 'crit']],
  ['Прошлый час / сутки', ['m_prev_h', 'v_std_prev_h', 'v_wc_prev_h', 'm_end_h', 'v_std_end_h', 'v_wc_end_h', 'm_prev_d', 'v_std_prev_d', 'v_wc_prev_d', 'm_end_d', 'v_std_end_d', 'v_wc_end_d']],
];
const PIPE_META = {
  p_g: ['Избыточное давление', 'pressure'], p_abs: ['Абсолютное давление', 'pressure'], dp: ['Перепад давления', 'pressure'], t: ['Температура', 'temp'],
  q_m: ['Массовый расход', 'mflow'], qv_std: ['Объёмный расход при ст.у.', 'vflow'], qv_wc: ['Объёмный расход при р.у.', 'vflow'],
  s_qm: ['Датчик: массовый расход', 'mflow'], s_qv_std: ['Датчик: объёмный расход при ст.у.', 'vflow'], s_qv_wc: ['Датчик: объёмный расход при р.у.', 'vflow'],
  m: ['Масса', 'mass'], v_std: ['Объём при ст.у.', 'vol'], v_wc: ['Объём при р.у.', 'vol'], s_m: ['Датчик: масса', 'mass'], s_v_wc: ['Датчик: объём при р.у.', 'vol'],
  rho_std: ['Плотность при ст.у.', 'dens'], rho_wc: ['Плотность при р.у.', 'dens'], h: ['Энтальпия', 'enthalpy'], pow: ['Тепловая мощность', 'power'], en: ['Тепловая энергия', 'energy'],
  t_ok: ['Время штатной работы', 'secs'], t_nss: ['Время нештатных ситуаций', 'secs'], n_err: ['Количество ошибок', ''], crit: ['Уровень критичности', ''],
  m_prev_h: ['Масса за прошлый час', 'mass'], v_std_prev_h: ['Объём ст.у. за прошлый час', 'vol'], v_wc_prev_h: ['Объём р.у. за прошлый час', 'vol'],
  m_end_h: ['Масса на конец часа', 'mass'], v_std_end_h: ['Объём ст.у. на конец часа', 'vol'], v_wc_end_h: ['Объём р.у. на конец часа', 'vol'],
  m_prev_d: ['Масса за прошлые сутки', 'mass'], v_std_prev_d: ['Объём ст.у. за прошлые сутки', 'vol'], v_wc_prev_d: ['Объём р.у. за прошлые сутки', 'vol'],
  m_end_d: ['Масса на конец суток', 'mass'], v_std_end_d: ['Объём ст.у. на конец суток', 'vol'], v_wc_end_d: ['Объём р.у. на конец суток', 'vol'],
};

DevTabs.pipes = {
  mount(root, d) {
    root.innerHTML = `<div class="split"><div class="pl" id="pl"></div><div id="pd" style="display:flex;flex-direction:column;gap:14px"></div></div>`;
    this.params = null; this.paramSig = '';
  },
  update(root, d) {
    const tps = (d.pipes || []);
    let cur = S.route.tp && tps.find(p => p.tp === S.route.tp) ? S.route.tp : (tps[0] && tps[0].tp);
    $('#pl').innerHTML = tps.map(p => `<a class="plitem ${p.tp === cur ? 'on' : ''}" href="#/d/${encodeURIComponent(d.id)}/pipes/${p.tp}"><b>ТП${p.tp} · ${esc(p.name)}</b><span class="small muted">${esc(p.type || '')}${p.n_err ? ' · <span style="color:var(--warn)">ошибок: ' + p.n_err + '</span>' : ''}</span></a>`).join('') || '<div class="muted">Трубопроводы не описаны</div>';
    if (cur == null) { $('#pd').innerHTML = ''; return; }
    if (S.route.tp !== cur) { S.route.tp = cur; api('select', { id: d.id, tab: 'pipes', tp: cur }).catch(() => { }); }
    const pf = (d.pipes_full || {})[cur]; if (!pf) return;
    const p = tps.find(x => x.tp === cur);
    const k = pipeKind(p.type);
    let h = `<div class="row"><h3>ТП${cur} · ${esc(p.name)}</h3><span class="chip ${p.crit >= 3 ? 'bad' : p.n_err ? 'warn' : 'ok'}">${p.n_err ? 'ошибок: ' + p.n_err + ', критичность ' + p.crit : 'ошибок нет'}</span></div>`;
    if (k === 'oil') h += `<div class="box">Это ${p.type === 'УУН' ? 'узел учёта нефти' : 'КМХ'}: основные данные — на вкладке «Учёт нефти».</div>`;
    const grid = PIPE_GROUPS.map(([title, keys]) => {
      const rows = keys.filter(kx => pf[kx] != null).map(kx => { const m = PIPE_META[kx]; return `<tr><td>${m[0]}</td><td class="n">${m[1] === '' ? pf[kx] : q(m[1], pf[kx])}</td></tr>`; }).join('');
      return rows ? `<article class="card"><h2>${title}</h2><table><tbody>${rows}</tbody></table></article>` : '';
    }).join('');
    h += `<div class="grid">${grid}</div><div id="pp"></div>`;
    if ($('#pd').dataset.cur !== String(cur) || true) { $('#pd').innerHTML = h; $('#pd').dataset.cur = String(cur); }
    this.loadParams(d, cur);
  },
  async loadParams(d, tp) {
    try {
      const j = await api('device/' + d.id + '/params');
      const rows = j.pipes[String(tp)];
      const el = $('#pp'); if (!el) return;
      if (!rows) { el.innerHTML = '<div class="muted small">Настройки трубопровода читаются из прибора…</div>'; return; }
      el.innerHTML = `<article class="card"><h2>Настройки трубопровода в приборе</h2><div class="sw"><table><tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.value)}</td></tr>`).join('')}</tbody></table></div><div class="small muted">Только просмотр. Параметры читаются из карты параметров прибора (рег. 7600…7870).</div></article>`;
    } catch (e) { }
  }
};

/* ---- датчики ---- */
DevTabs.sensors = {
  mount(root) {
    root.innerHTML = `<div class="row"><div class="meta">Логические входы прибора. Названия и единицы берутся из настроек прибора.</div><label class="small"><input type="checkbox" id="sa"> показывать неописанные</label></div><div class="sw"><table><thead><tr><th class="n">№</th><th>Параметр</th><th>Источник</th><th class="n">Значение</th><th>Состояние</th></tr></thead><tbody id="sb"></tbody></table></div><div id="sn" class="small muted"></div>`;
    this.params = {}; this.all = false;
    $('#sa').onchange = e => { this.all = e.target.checked; this.render(S.dev); };
    this.timer = setInterval(() => this.fetch(), 3000); this.fetch();
  },
  async fetch() { if (S.route.tab !== 'sensors' || !S.dev) { clearInterval(this.timer); return; } try { const j = await api('device/' + S.dev.id + '/params'); this.params = j.inputs; this.pending = j.pending; this.render(S.dev); } catch (e) { } },
  update(root, d) { this.render(d); },
  render(d) {
    const tb = $('#sb'); if (!tb || !d) return;
    const inp = d.inputs || [];
    const rows = inp.filter(i => this.all || i.described);
    tb.innerHTML = rows.map(i => {
      const pr = this.params[String(i.n)] || [];
      const get = k => (pr.find(r => r.name.startsWith(k)) || {}).value || '';
      const nm = get('Тип выходного'), un = get('Единица'), src = get('Источник');
      const chips = i.flags.filter(f => f !== 'описан константой').map(f => `<span class="chip ${i.level}">${esc(f)}</span>`).join('') || (i.described ? '<span class="chip ok">норма</span>' : '<span class="chip">не описан</span>');
      return `<tr class="${i.described ? '' : 'undesc'}"><td class="n">${i.n}</td><td>${esc(nm || (i.described ? '…' : 'не описан'))}</td><td>${esc(src)}</td><td class="n">${i.value == null ? '—' : num(i.value, 4)} ${esc(un)}</td><td>${chips}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">Нет данных</td></tr>';
    $('#sn').textContent = this.pending ? 'Читаю названия входов из прибора… осталось: ' + this.pending : '';
  }
};

/* ---- учёт нефти ---- */
function oilTable(rows) {
  return `<table><tbody>${rows.filter(r => r.value != null).map(r => `<tr><td>${esc(r.label)}</td><td class="n">${num(r.value, 4)}</td></tr>`).join('') || '<tr><td class="muted">нет данных</td></tr>'}</tbody></table>`;
}
DevTabs.oil = {
  mount(root) {
    this.period = 'hour'; this.pd = null;
    root.innerHTML = `<div class="grid" id="og"></div>
    <div class="card"><div class="row"><h2>Итоги закончившихся периодов</h2><div class="seg" id="seg"><span data-k="hour" class="on">Час / 2 часа</span><span data-k="shift">Смена</span><span data-k="day">Сутки</span></div></div>
      <div class="small muted">Прибор формирует часовой или двухчасовой отчёт в зависимости от настройки — реальный период указан во времени начала и конца.</div><div id="op"></div></div>
    <details class="card"><summary style="cursor:pointer"><b>Управление СИКН, коэффициенты плотности и К-факторы</b> <span class="small muted">(только просмотр)</span></summary><div class="grid" id="ok" style="margin-top:12px"></div></details>`;
    $('#seg').onclick = e => { const k = e.target.dataset.k; if (!k) return; this.period = k; [...$('#seg').children].forEach(s => s.classList.toggle('on', s.dataset.k === k)); this.loadPeriod(); };
    this.loadPeriod(); this.timer = setInterval(() => { if (S.route.tab === 'oil') this.loadPeriod(); else clearInterval(this.timer); }, 15000);
    this.loadControl();
  },
  async loadPeriod() {
    if (!S.dev) return; const key = this.period;
    try { const j = await api('device/' + S.dev.id + '/oil/period/' + key); if (key !== this.period) return; this.renderPeriod(j.scopes); } catch (e) { const el = $('#op'); if (el) el.innerHTML = `<div class="box">${esc(e.message)}</div>`; }
  },
  renderPeriod(scopes) {
    const el = $('#op'); if (!el) return;
    if (!scopes.length) { el.innerHTML = '<div class="muted">Данных за период нет</div>'; return; }
    const first = scopes[0].rows; const g = a => (first.find(r => r.addr === a) || {}).value;
    const t = (b) => { const v = [2, 3, 4, 5, 6, 7].map(o => g(b + o)); return v[0] == null ? '' : `${String(v[0]).padStart(2, '0')}.${String(v[1]).padStart(2, '0')}.${v[2]} ${String(v[3]).padStart(2, '0')}:${String(v[4]).padStart(2, '0')}:${String(v[5]).padStart(2, '0')}`; };
    el.innerHTML = `<div class="box" style="margin-top:10px">Период: <b class="mono">${t(0)}</b> — <b class="mono">${t(6)}</b></div>
      <div class="grid" style="margin-top:12px">${scopes.map(sc => `<article class="card"><h2>${esc(sc.title)}</h2>${oilTable(sc.rows.filter(r => !(sc.id === 'sikn' && r.addr < 14)))}</article>`).join('')}</div>`;
  },
  async loadControl() {
    try { const j = await api('device/' + S.dev.id + '/oil/control'); this.ctl = j; this.renderControl(); } catch (e) { }
  },
  renderControl() {
    const j = this.ctl; if (!j || !$('#ok')) return;
    const mask = j.line_mask;
    let h = '';
    if (mask != null) h += `<article class="card"><h2>Управление СИКН</h2><dl class="kv"><dt>Маска отключённых линий</dt><dd class="mono">${mask}</dd>
      <dt>Отключены линии</dt><dd>${[1, 2, 3, 4].filter(n => mask >> (n - 1) & 1).join(', ') || 'нет'}</dd>
      <dt>Резервный канал влагомера</dt><dd>${mask & 64 ? 'используется' : 'нет'}</dd></dl><div class="small muted">Изменение доступно только в режиме «Наладчик» — появится в следующей версии.</div></article>`;
    if (j.dens && j.dens.length) h += `<article class="card"><h2>Коэффициенты плотности</h2>${oilTable(j.dens)}</article>`;
    for (const k of (j.kfactors || [])) h += `<article class="card"><h2>К-факторы · ${esc(k.title)}</h2>${oilTable(k.rows)}</article>`;
    $('#ok').innerHTML = h;
  },
  update(root, d) {
    const og = $('#og'); if (!og) return;
    const groups = d.oil_groups;
    if (!groups) { og.innerHTML = '<div class="card muted">Чтение данных нефтяного узла…</div>'; return; }
    let h = groups.map(g => `<article class="card"><h2>${esc(g.title)}</h2>${oilTable(g.rows)}</article>`).join('');
    const kmh = d.kmh;
    if (kmh) {
      const prog = (kmh.rows.find(r => r.addr === 18046) || {}).value;
      h += `<article class="card"><h2>КМХ</h2><dl class="kv"><dt>Эталонная линия</dt><dd class="mono">ТП${kmh.ref_pipe ?? '—'}</dd><dt>Контролируемая линия</dt><dd class="mono">ТП${kmh.ctl_pipe ?? '—'}</dd>
        <dt>Выполнено КМХ</dt><dd class="mono">${kmh.count ?? '—'}</dd><dt>Ход текущего КМХ</dt><dd class="mono">${prog == null ? '—' : num(prog, 1) + ' %'}</dd></dl>
        ${oilTable(kmh.rows.filter(r => r.addr !== 18046))}<div class="small muted">Запуск и прерывание КМХ — в следующей версии (потребуется «Наладчик»).</div></article>`;
    }
    for (const s of (d.sampler || [])) h += `<article class="card"><h2>Пробоотборник · ${s.name}</h2><dl class="kv"><dt>Режим</dt><dd>${esc(s.mode_text)}</dd><dt>Длительность импульса</dt><dd class="mono">${s.pulse_ms} мс</dd>
      <dt>Осталось проб</dt><dd class="mono">${s.count ?? '—'}</dd><dt>${s.mode === 200 ? 'Масса на импульс' : 'Период, с'}</dt><dd class="mono">${num(s.period, 3)}</dd></dl></article>`;
    og.innerHTML = h;
  }
};

/* ---- архивы ---- */
function dtLocal(dt) { const p = n => String(n).padStart(2, '0'); return dt.getFullYear() + '-' + p(dt.getMonth() + 1) + '-' + p(dt.getDate()) + 'T' + p(dt.getHours()) + ':' + p(dt.getMinutes()) + ':' + p(dt.getSeconds()); }
const OPTS = [[0, 'Единицы измерения, заданные в приборе'], [1, 'Шапка параметров'], [2, 'Теги параметров'], [3, 'Максимум знаков'], [4, 'Без меток абсолютного времени'], [5, 'Все доступные параметры']];
DevTabs.archive = {
  mount(root, d) {
    const now = d.clock ? new Date(d.clock) : new Date();
    const hr = new Date(now); hr.setMinutes(0, 0, 0);
    this.now = now;
    root.innerHTML = `<div class="card"><h2>Запрос отчёта из архива</h2>
      <div class="frm"><label class="f">Трубопровод<select id="a-tp">${(d.pipes || []).map(p => `<option value="${p.tp}">ТП${p.tp} · ${esc(p.name)}</option>`).join('')}</select></label>
      <label class="f">Начало<input type="datetime-local" step="1" id="a-s"></label><label class="f">Конец<input type="datetime-local" step="1" id="a-e"></label>
      <label class="small"><input type="checkbox" id="a-snap"> срез (без конца)</label></div>
      <div class="frm"><span class="small muted">Периоды:</span>
      <button data-p="h1">Прошлый час</button><button data-p="h2">Прошлые 2 часа</button><button data-p="d0">С начала суток</button><button data-p="d1">Прошлые сутки</button><button data-p="m1">Прошлый месяц</button></div>
      <details><summary class="small muted" style="cursor:pointer">Параметры формата</summary><div style="display:flex;flex-direction:column;gap:4px;padding:8px 0">${OPTS.map(o => `<label class="small"><input type="checkbox" data-b="${o[0]}" ${o[0] <= 2 ? 'checked' : ''}> ${o[1]}</label>`).join('')}</div></details>
      <div class="frm"><button class="pri" id="a-go">Запросить</button><span class="small muted">Не более 5 суток за раз; сбор может занять несколько секунд.</span></div></div>
      <div id="a-res"></div>`;
    const set = (a, b) => { $('#a-s').value = dtLocal(a); $('#a-e').value = dtLocal(b); $('#a-snap').checked = false; };
    root.querySelectorAll('[data-p]').forEach(b => b.onclick = () => {
      const h0 = new Date(now); h0.setMinutes(0, 0, 0); const d0 = new Date(now); d0.setHours(0, 0, 0, 0);
      const k = b.dataset.p;
      if (k === 'h1') set(new Date(h0 - 3600e3), h0);
      if (k === 'h2') set(new Date(h0 - 7200e3), h0);
      if (k === 'd0') set(d0, now);
      if (k === 'd1') set(new Date(d0 - 86400e3), d0);
      if (k === 'm1') { const m0 = new Date(now.getFullYear(), now.getMonth(), 1); const m1 = new Date(now.getFullYear(), now.getMonth() - 1, 1); set(m1, m0); }
    });
    set(new Date(hr - 7200e3), hr);
    $('#a-go').onclick = () => this.go(d);
  },
  async go(d) {
    const btn = $('#a-go'); btn.disabled = true; btn.textContent = 'Запрос выполняется…';
    let opts = 0; document.querySelectorAll('[data-b]').forEach(c => { if (c.checked) opts |= 1 << (+c.dataset.b); });
    const snap = $('#a-snap').checked;
    try {
      const j = await api('device/' + d.id + '/archive', { tp: +$('#a-tp').value, start: $('#a-s').value, end: snap ? null : $('#a-e').value, opts });
      this.res = j; this.render(j);
    } catch (e) { $('#a-res').innerHTML = `<div class="box bad">${esc(e.message)}</div>`; }
    btn.disabled = false; btn.textContent = 'Запросить';
  },
  render(j) {
    const el = $('#a-res');
    if (j.status !== 2) { el.innerHTML = `<div class="box ${j.status === 3 ? '' : 'warn'}">Прибор ответил: ${esc(j.status_text)} (код ${j.status})</div>`; return; }
    const items = j.items || []; const big = items.filter(i => i.raw.includes('\n') || i.raw.length > 60);
    const small = items.filter(i => !big.includes(i));
    el.innerHTML = `<div class="card"><div class="row"><h2>Результат</h2><span><button id="a-cp">Копировать</button> <button id="a-csv">Скачать CSV</button></span></div>
      <div class="sw"><table><thead><tr><th>Параметр</th><th>Тег</th><th class="n">Значение</th></tr></thead><tbody>${small.map(i => `<tr><td>${esc(i.title)}</td><td class="mono small muted">${esc(i.tag)}</td><td class="n">${esc(i.raw)}</td></tr>`).join('')}</tbody></table></div>
      ${big.map(i => `<div><div class="small muted">${esc(i.title)} (${esc(i.tag)})</div><pre class="arch">${esc(i.raw)}</pre></div>`).join('')}</div>`;
    $('#a-cp').onclick = async () => { try { await navigator.clipboard.writeText(j.text); toast('Скопировано'); } catch (e) { toast('Не удалось скопировать', true); } };
    $('#a-csv').onclick = () => { const rows = [['Параметр', 'Тег', 'Значение']].concat(items.map(i => [i.title, i.tag, i.raw])); const csv = '﻿' + rows.map(r => r.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(';')).join('\r\n'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'archive_tp' + j.tp + '.csv'; a.click(); };
  },
  update() { }
};

/* ---- настройки прибора ---- */
DevTabs.settings = {
  mount(root, d) { },
  update(root, d) {
    const info = d.info || {};
    root.innerHTML = `<div class="grid"><article class="card"><h2>Режим работы</h2><div class="box">Версия 0.1 работает только на чтение: запись параметров, времени и управление (КМХ, пробоотборник, линии СИКН) включаются отдельным шагом, с входом под пользователем и подтверждением каждой записи.</div>
      <dl class="kv"><dt>Уровень доступа сейчас</dt><dd>${esc(d.level_text || '—')}</dd></dl></article>
      <article class="card"><h2>Диагностика</h2><dl class="kv"><dt>Адресация</dt><dd class="mono">смещение ${info.offset ?? 0}</dd><dt>Порядок байт</dt><dd class="mono">${esc(info.order || '')}</dd>
      <dt>Сообщает прибор (HR100/101)</dt><dd class="mono">${esc(info.order_hint || '—')}</dd><dt>Маска трубопроводов</dt><dd class="mono">0x${(info.mask || 0).toString(16).toUpperCase().padStart(4, '0')}</dd>
      <dt>Формат года в часах</dt><dd class="mono">${d.year4 ? '4 цифры' : '2 цифры'}</dd><dt>Ошибок связи за сеанс</dt><dd class="mono">${d.errors_total ?? 0}</dd></dl>
      <button id="rd">Определить прибор заново</button></article></div>`;
    const b = $('#rd'); if (b) b.onclick = async () => { await api('device/' + d.id + '/refresh', {}); toast('Прибор будет определён заново'); };
  }
};

/* ---------------- ядро: маршрутизация и обновление ---------------- */
async function refresh() {
  try {
    const j = await api('devices'); S.devices = j.devices; S.version = j.version; $('#ver').textContent = 'версия ' + j.version;
  } catch (e) { return; }
  const r = S.route;
  if (r.name === 'dev') { try { S.dev = await api('device/' + encodeURIComponent(r.id)); } catch (e) { S.dev = null; } } else S.dev = null;
  renderNav();
  if (S.screen && S.screen.update) S.screen.update(S.dev);
}
async function route() {
  clearInterval(DevTabs.sensors.timer); clearInterval(DevTabs.oil.timer);
  S.route = parseRoute(); S.dev = null;
  const root = $('#main');
  root.innerHTML = '';
  const r = S.route;
  if (r.name === 'summary') { S.screen = Summary; Summary.mount(root); api('select', { id: null }).catch(() => { }); }
  else if (r.name === 'devices') { S.screen = Devices; Devices.mount(root); api('select', { id: null }).catch(() => { }); }
  else if (r.name === 'units') { S.screen = Units; Units.mount(root); }
  else { S.screen = Dev; Dev.mount(root, r); api('select', { id: r.id, tab: r.tab, tp: r.tp }).catch(() => { }); }
  await refresh();
}
window.addEventListener('hashchange', route);
setInterval(() => api('ping').catch(() => { }), 5000);
setInterval(refresh, 2000);
route();
