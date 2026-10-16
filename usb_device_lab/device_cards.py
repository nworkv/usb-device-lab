"""Bounded device-identity enrichment for the active research console."""
SCRIPT = """'use strict';
function usbIdentity(config){
 if(!config||typeof config!=='object')throw new Error('Invalid configuration');
 const list=Array.isArray(config.descriptors)?config.descriptors:[];
 const descriptor=list.find(d=>d&&d.type===1&&(d.index===undefined||d.index===0)&&(d.wIndex===undefined||d.wIndex===0));
 if(!descriptor||typeof descriptor.hex!=='string'||descriptor.hex.length<36||descriptor.hex.length>131070||descriptor.hex.length%2||!/^[0-9a-f]+$/i.test(descriptor.hex))throw new Error('Invalid device descriptor');
 const b=descriptor.hex.match(/../g).map(x=>parseInt(x,16)),hex=(n,w=4)=>n.toString(16).toUpperCase().padStart(w,'0');
 const meta=config.metadata&&typeof config.metadata==='object'?config.metadata:{};
 const family=typeof meta.family==='string'?meta.family.slice(0,127):'unknown',profile=typeof meta.profile==='string'?meta.profile.slice(0,127):'unknown';
 const icons=Object.assign(Object.create(null),{hid:'⌨',audio:'♫',cdc:'⇄',storage:'▣',midi:'♫',uvc:'◉',composite:'▦',boundary:'◇'});
 const speeds=new Map([[1,'Low Speed'],[2,'Full Speed'],[3,'High Speed'],[5,'SuperSpeed'],[6,'SuperSpeedPlus']]);
 return {icon:icons[family]||'⎈',family,profile,vid:hex(b[8]|b[9]<<8),pid:hex(b[10]|b[11]<<8),deviceClass:hex(b[4],2),bcdUSB:hex(b[2]|b[3]<<8),speed:speeds.get(config.speed)||'не указана'};
}
const deviceCardCache=new Map();let deviceCardEpoch=-1;
async function addDeviceCards(id){
 const gen=epoch;if(deviceCardEpoch!==gen){deviceCardCache.clear();deviceCardEpoch=gen;}
 const options=[...el('attempt').options].slice(0,100),cards=[...el('attempt-cards').children];let cursor=0;
 const rows=options.map((o,i)=>{const info=node('p','Загрузка USB-идентификаторов…','muted'),card=cards[i];if(card)card.prepend(info);return {attempt:o.value,info,card};});
 async function worker(){while(cursor<rows.length){
  if(gen!==epoch||!token||el('campaign').value!==id)return;
  const {attempt,info,card}=rows[cursor++];if(!card)continue;const key=id+'/'+attempt;
  try{
   let value=deviceCardCache.get(key);
   if(!value){value=usbIdentity(await api(file(id,attempt,'config.json')));if(gen!==epoch||!token||el('campaign').value!==id)return;if(deviceCardCache.size>=200)deviceCardCache.delete(deviceCardCache.keys().next().value);deviceCardCache.set(key,value);}
   if(gen!==epoch||el('campaign').value!==id)return;
   info.textContent=`${value.icon} ${value.family}/${value.profile} · VID:PID ${value.vid}:${value.pid} · class 0x${value.deviceClass} · bcdUSB 0x${value.bcdUSB} · ${value.speed} (из конфигурации, не измерение линии)`;
  }catch(error){if(gen!==epoch||el('campaign').value!==id)return;info.textContent='USB-идентификаторы недоступны: конфигурация отсутствует или некорректна';}
 }}
 await Promise.all(Array.from({length:Math.min(4,rows.length)},worker));
}
const originalDeviceDetails=details;
details=async function(id){const gen=epoch;await originalDeviceDetails(id);if(gen!==epoch||!token||el('campaign').value!==id)return;await addDeviceCards(id);};
""".encode('utf-8')
