"""Transport operator UI with bounded, cached per-attempt device cards."""
PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>USB Lab: UART / SSH</title><body>
<h1>USB Lab: UART / SSH</h1><p>Канал наблюдения не доказывает здоровье хоста. Кандидаты не являются подтверждёнными багами.</p>
<input id="token" type="password" autocomplete="off" placeholder="Токен"><button id="login">Войти</button><button id="logout">Выйти</button>
<p id="error" role="status"></p><select id="mode"><option value="uart">UART</option><option value="ssh">SSH</option></select><button id="apply">Выбрать режим</button>
<button id="start">Новая кампания</button><button id="stop">Остановить попытки</button><pre id="state"></pre>
<h2>Кампании</h2><select id="campaign"></select><button id="resume">Продолжить выбранную</button><button id="refresh">Обновить</button>
<h2>Устройства в попытках</h2><p>VID/PID и класс взяты из сохранённых дескрипторов; семейство и профиль — из метаданных. Показаны последние попытки, доступные API.</p><div id="attempts" aria-live="polite"></div>
<pre id="details"></pre><div id="files"></div><script src="/dual.js"></script></body></html>""".encode('utf-8')
SCRIPT = """'use strict';
const el=id=>document.getElementById(id);let token='',epoch=0,controller=new AbortController(),busy=false;
const summaries=new Map();
async function api(path,body){const gen=epoch,opts={signal:controller.signal,headers:{Authorization:'Bearer '+token}};if(body!==undefined){opts.method='POST';opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}const r=await fetch(path,opts),v=await r.json();if(gen!==epoch)throw new Error('Session changed');if(!r.ok)throw new Error(v.error||'Request failed');return v;}
function button(campaign,area,name){const b=document.createElement('button');b.textContent=area+'/'+name;b.onclick=async()=>{const gen=epoch;try{const r=await fetch('/api/file?'+new URLSearchParams({campaign,area,name}),{signal:controller.signal,headers:{Authorization:'Bearer '+token}});if(!r.ok)throw new Error((await r.json()).error);const data=await r.blob();if(gen!==epoch)return;const u=URL.createObjectURL(data),a=document.createElement('a');a.href=u;a.download=campaign+'-'+name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);}catch(e){if(gen===epoch&&e.name!=='AbortError')el('error').textContent=e.message;}};el('files').append(b);}
function deviceSummary(config){
 const descriptors=Array.isArray(config.descriptors)?config.descriptors:[];
 const descriptor=descriptors.find(d=>d&&d.type===1&&(d.index===undefined||d.index===0)&&(d.wIndex===undefined||d.wIndex===0));
 if(!descriptor||typeof descriptor.hex!=='string'||descriptor.hex.length<36||descriptor.hex.length>524288||descriptor.hex.length%2||!/^[0-9a-f]+$/i.test(descriptor.hex))throw new Error('Некорректный дескриптор устройства');
 const b=descriptor.hex.match(/../g).map(x=>parseInt(x,16));
 const hex=n=>n.toString(16).toUpperCase().padStart(4,'0');
 const m=config.metadata&&typeof config.metadata==='object'?config.metadata:{};
 const family=typeof m.family==='string'?m.family.slice(0,127):'unknown';
 const profile=typeof m.profile==='string'?m.profile.slice(0,127):'unknown';
 const icons=Object.assign(Object.create(null),{hid:'⌨',audio:'♫',cdc:'⇄',storage:'▣',midi:'♫',uvc:'◉',composite:'▦',boundary:'◇'});
 const icon=icons[family]||'⎈';
 const speed=({1:'Low Speed',2:'Full Speed',3:'High Speed',5:'SuperSpeed',6:'SuperSpeedPlus'})[config.speed]||'не указана';
 return {icon,family,profile,vid:hex(b[8]|b[9]<<8),pid:hex(b[10]|b[11]<<8),deviceClass:b[4].toString(16).toUpperCase().padStart(2,'0'),speed};
}
async function renderAttempts(campaign,attempts){
 const gen=epoch;el('attempts').replaceChildren();let next=0;
 const rows=attempts.map(a=>{const node=document.createElement('p');node.textContent=a.id+' — загрузка устройства';el('attempts').append(node);return {a,node};});
 async function worker(){while(next<rows.length){const {a,node}=rows[next++];const key=campaign+'/'+a.id;try{
  let summary=summaries.get(key);if(!summary){summary=deviceSummary(await api('/api/file?'+new URLSearchParams({campaign,area:a.id,name:'config.json'})));if(gen!==epoch)return;if(summaries.size>=200)summaries.delete(summaries.keys().next().value);summaries.set(key,summary);}
  if(gen!==epoch)return;const r=a.result;const status=!r?'выполняется / результат отсутствует':r.infrastructure_error?'ошибка инфраструктуры':r.interrupted?'прервана':r.window_completed?'окно завершено':'окно не завершено';
  node.textContent=`${summary.icon} ${a.id} | ${summary.family}/${summary.profile} | VID:PID ${summary.vid}:${summary.pid} | class 0x${summary.deviceClass} | ${summary.speed} (из конфигурации) | ${status}`;
 }catch(e){if(gen!==epoch)return;node.textContent=a.id+' — сведения об устройстве недоступны';}}}
 await Promise.all(Array.from({length:Math.min(4,rows.length)},worker));
}
async function refresh(){if(!token||busy)return;busy=true;const gen=epoch;try{const s=await api('/api/state');el('state').textContent=JSON.stringify(s,null,2);el('error').textContent=s.error||'';el('apply').disabled=s.busy;el('start').disabled=s.busy;el('resume').disabled=s.busy;el('stop').disabled=!s.busy||s.stage==='analyzing';const rows=await api('/api/history'),old=el('campaign').value,keep=rows.some(r=>r.id===old)?old:(s.active||rows[0]?.id||'');el('campaign').replaceChildren();for(const row of rows){const o=document.createElement('option');o.value=row.id;o.textContent=row.mode+' '+row.id;el('campaign').append(o);}el('campaign').value=keep;el('files').replaceChildren();el('attempts').replaceChildren();el('details').textContent='';if(keep){const d=await api('/api/details?'+new URLSearchParams({campaign:keep}));el('details').textContent=JSON.stringify(d,null,2);for(const name of ['campaign.json','checkpoint.json','selection.json','transport-analysis.json'])button(keep,'campaign',name);for(const a of d.attempts)for(const name of ['config.json','started.json','window.json','result.json','executor.log'])button(keep,a.id,name);for(const x of d.exports)for(const name of x.files)button(keep,x.area,name);for(const name of ['uart.raw','chunks.jsonl','events.jsonl'])button(keep,'capture',name);if(d.metadata.mode==='ssh')button(keep,'capture','ssh-stderr.log');await renderAttempts(keep,d.attempts);}}catch(e){if(gen===epoch&&e.name!=='AbortError')el('error').textContent=e.message;}finally{if(gen===epoch)busy=false;}}
async function action(path,value){try{await api(path,value);await refresh();}catch(e){el('error').textContent=e.message;}}
el('login').onclick=()=>{epoch++;controller.abort();controller=new AbortController();busy=false;summaries.clear();token=el('token').value;el('token').value='';refresh();};
el('logout').onclick=()=>{epoch++;controller.abort();controller=new AbortController();token='';busy=false;summaries.clear();for(const id of ['state','error','details'])el(id).textContent='';el('campaign').replaceChildren();el('files').replaceChildren();el('attempts').replaceChildren();};
el('apply').onclick=()=>action('/api/mode',{mode:el('mode').value});el('stop').onclick=()=>action('/api/stop',{});
el('start').onclick=()=>{if(confirm('Запустить USB-активность на выделенном стенде?'))action('/api/start',{});};
el('resume').onclick=()=>{if(confirm('Продолжить совместимую кампанию?'))action('/api/resume',{campaign:el('campaign').value});};
el('refresh').onclick=refresh;el('campaign').onchange=refresh;setInterval(refresh,3000);
""".encode('utf-8')
