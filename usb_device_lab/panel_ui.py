"""Local, Russian-language control panel assets; no embedded credentials."""

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>USB Device Lab — управление</title><link rel="stylesheet" href="/control.css"></head>
<body><main class="shell"><header><span class="eyebrow">USB DEVICE LAB / ЛОКАЛЬНЫЙ СТЕНД</span>
<h1>Исследуйте USB.<br><span>Контролируйте кампанию.</span></h1>
<p class="subtitle">Запуск, состояние и безопасная остановка в одном окне.</p></header>
<section class="metrics" aria-label="Состояние кампании">
<article class="glass"><span class="caption">Состояние</span><strong id="state-label">Нет подключения</strong></article>
<article class="glass"><span class="caption">Завершено попыток</span><strong id="run-count">—</strong></article>
<article class="glass"><span class="caption">Идентификатор сессии</span><strong id="session-label" class="mono">—</strong></article></section>
<div class="columns"><section class="glass panel" aria-labelledby="settings-title"><h2 id="settings-title">Новая кампания</h2>
<label for="token">Токен доступа</label><input id="token" type="password" autocomplete="off" spellcheck="false" aria-describedby="token-help">
<p id="token-help" class="hint">Скопируйте токен из терминала. Он не сохраняется в браузере.</p>
<div class="fields"><div><label for="iterations">Количество попыток</label><input id="iterations" type="number" value="100" min="1" max="1000000" step="1"></div>
<div><label for="seconds">Окно попытки, секунд</label><input id="seconds" type="number" value="5" min="0.01" max="300" step="any"></div>
<div><label for="seed">Seed генератора</label><input id="seed" type="number" value="0" min="0" max="9007199254740991" step="1"></div></div>
<div class="actions"><button id="start" class="primary">Запустить</button><button id="stop" class="danger">Остановить</button>
<button id="resume">Возобновить</button><button id="status">Обновить состояние</button></div>
<label class="toggle"><input id="auto-status" type="checkbox" checked> Обновлять состояние каждые 2 секунды</label>
<p class="hint">Остановка завершает текущую попытку. Возобновление продолжает совместимую кампанию с checkpoint; при несовместимости продолжение будет запрещено.</p></section>
<section class="glass panel" aria-labelledby="response-title"><h2 id="response-title">Ответ supervisor</h2>
<p id="notice" class="notice" role="status" aria-live="polite">Введите токен и обновите состояние.</p>
<details><summary>Технические данные</summary><pre id="result">Данных пока нет.</pre></details>
<div class="note"><h3>Находка — не просто ошибка</h3><p>Состояние failed не подтверждает ошибку ядра. Для вывода о находке нужны артефакты и проверка воспроизводимости.</p></div>
<p class="hint">Панель управляет только настроенным стендом. Токен нужен для всех запросов API.</p></section></div>
<footer>USB Device Lab · Только локальный доступ · Без внешних ресурсов</footer>
</main><script src="/control.js"></script></body></html>
""".encode("utf-8")

STYLE = """ :root{color-scheme:dark;--text:#edf4ff;--muted:#b8c8dc;--border:rgba(180,204,255,.18);--accent:#a7e4ff}
*{box-sizing:border-box}body{margin:0;color:var(--text);font:16px/1.6 system-ui,-apple-system,sans-serif;background:#101725;background-image:radial-gradient(ellipse at 12% 0%,#244261 0%,transparent 55%),radial-gradient(ellipse at 90% 35%,#342a54 0%,transparent 50%);min-height:100vh}
.shell{max-width:1160px;margin:auto;padding:48px 24px}header{margin-bottom:32px}.eyebrow{color:var(--accent);font-size:12px;letter-spacing:.15em}h1{font-size:clamp(32px,5vw,58px);line-height:1.12;letter-spacing:-.04em;margin:18px 0}h1 span{color:var(--accent)}.subtitle,.hint,.caption,footer{color:var(--muted)}h2{font-size:22px;margin:0 0 22px}h3{font-size:16px;margin:0 0 8px}.glass{background:rgba(27,40,61,.8);border:1px solid var(--border);border-radius:24px;box-shadow:0 16px 48px rgba(0,0,0,.18);backdrop-filter:blur(18px)}.metrics{display:grid;grid-template-columns:1fr 1fr 1.5fr;gap:16px;margin-bottom:20px}.metrics article{padding:20px}.caption{display:block;font-size:13px}.metrics strong{display:block;font-size:21px;margin-top:10px;overflow-wrap:anywhere}.metrics .mono{font-size:13px}.columns{display:grid;grid-template-columns:1.4fr 1fr;gap:20px}.panel{padding:28px}label{display:block;font-size:14px;margin-bottom:8px}input:not([type=checkbox]){width:100%;padding:12px 14px;border:1px solid var(--border);border-radius:12px;background:#101d30;color:var(--text);font:inherit}input:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:3px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:24px 0}.fields>div:last-child{grid-column:1/-1}.hint{font-size:12px}.actions{display:flex;flex-wrap:wrap;gap:10px}button{border:1px solid var(--border);border-radius:12px;padding:12px 16px;background:#293b55;color:var(--text);font:inherit;font-size:14px;cursor:pointer}button.primary{background:#a7e4ff;color:#0a2636;font-weight:650}button.danger{background:#543244}button:disabled{opacity:.5;cursor:wait}.toggle{display:flex;align-items:center;gap:8px;margin:22px 0 12px}.notice{padding:14px;border-radius:12px;background:#14283d;overflow-wrap:anywhere}.notice[data-error=true]{background:#4b2635;color:#ffe3e9}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;font:12px/1.7 ui-monospace,monospace;color:var(--muted)}summary{cursor:pointer;color:var(--accent)}.note{margin:24px 0;padding-top:20px;border-top:1px solid var(--border)}.note p{font-size:14px;color:var(--muted)}footer{font-size:12px;margin-top:28px}
@media(max-width:760px){.shell{padding:28px 16px}.metrics,.columns{grid-template-columns:1fr}.panel{padding:22px}.fields{grid-template-columns:1fr}.fields>div:last-child{grid-column:auto}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
""".encode("utf-8")

SCRIPT = """ 'use strict';
const el = id => document.getElementById(id);
const stateNames = {idle:'Ожидание',starting:'Проверка и запуск',running:'Кампания выполняется',stopping:'Завершение попытки',stopped:'Остановлена',completed:'Завершена',failed:'Ошибка выполнения',interrupted:'Прервана при перезапуске'};
let controlBusy = false;
function notice(message, error=false) { el('notice').textContent=message; el('notice').dataset.error=String(error); }
async function request(action) {
  if (controlBusy) return;
  const token=el('token').value.trim();
  if (!token) { notice('Введите токен из локального терминала.',true); return; }
  const status=action==='status';
  const options={method:status?'GET':'POST',headers:{Authorization:'Bearer '+token}};
  if (!status) {
    let data={};
    if (action==='start') {
      data={iterations:Number(el('iterations').value),seconds:Number(el('seconds').value),seed:Number(el('seed').value)};
      if (!Number.isSafeInteger(data.iterations)||data.iterations<1||data.iterations>1000000||!Number.isFinite(data.seconds)||data.seconds<=0||data.seconds>300||!Number.isSafeInteger(data.seed)||data.seed<0) {
        notice('Проверьте параметры: 1–1000000 попыток, окно до 300 секунд, неотрицательный целый seed в точном диапазоне JavaScript.',true); return;
      }
    }
    options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);
  }
  controlBusy=true;
  const buttons=['start','stop','resume','status'];buttons.forEach(id=>el(id).disabled=true);
  try {
    const response=await fetch('/api/campaign/'+action,options);
    const data=await response.json();el('result').textContent=JSON.stringify(data,null,2);
    if (!response.ok) {
      const errors={400:'Некорректные параметры запроса.',401:'Токен не принят. Проверьте терминал.',403:'Доступ запрещён: проверьте адрес панели.',409:'Команда сейчас недоступна. Проверьте состояние кампании.',503:'Сервер завершает работу.'};
      throw new Error(errors[response.status]||'Сервер сообщил об ошибке. Подробности — в технических данных.');
    }
    el('state-label').textContent=stateNames[data.state]||data.state||'Нет данных';
    el('run-count').textContent=Number.isInteger(data.runs)?String(data.runs):'—';
    el('session-label').textContent=data.session||'—';
    const failed=Boolean(data.error||data.persistence_error);
    notice(failed?'Обнаружена ошибка выполнения. Проверьте технические данные и артефакты.':status?'Состояние обновлено.':'Команда принята. Итог выполнения появится после обновления состояния.',failed);
  } catch(error) { notice(error instanceof TypeError?'Не удалось связаться с панелью. Проверьте сервер и локальный адрес.':String(error.message||error),true); }
  finally { controlBusy=false;buttons.forEach(id=>el(id).disabled=false); }
}
for(const action of ['start','stop','resume','status']) el(action).addEventListener('click',()=>request(action));
setInterval(()=>{if(el('auto-status').checked&&el('token').value&&!controlBusy)request('status');},2000);
""".encode("utf-8")
