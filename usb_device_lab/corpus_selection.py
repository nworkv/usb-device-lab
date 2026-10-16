"""Read-only manifest catalogue and in-memory next-campaign family selection."""
from dataclasses import replace
import re
import tomllib

NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')


def catalogue(config):
    with config.manifest.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Манифест превышает 64 KiB')
    data = tomllib.loads(raw.decode('utf-8'))
    if set(data) != {'corpus', 'family'} or type(data['corpus']) is not dict:
        raise ValueError('Некорректный манифест')
    header = data['corpus']
    if set(header) - {'format', 'seed_dir'} or type(header.get('format')) is not int or header['format'] != 1:
        raise ValueError('Неподдерживаемый формат манифеста')
    families = data['family']
    if type(families) is not list or not 1 <= len(families) <= 128:
        raise ValueError('В манифесте должно быть 1..128 семейств')
    result, seen, total = [], set(), 0
    for row in families:
        if type(row) is not dict or set(row) != {'name', 'profiles'}:
            raise ValueError('Некорректное семейство')
        name, profiles = row['name'], row['profiles']
        if type(name) is not str or NAME.fullmatch(name) is None or name in seen:
            raise ValueError('Некорректное или повторное имя семейства')
        if type(profiles) is not list or not 1 <= len(profiles) <= 128:
            raise ValueError('В семействе должно быть 1..128 профилей')
        if any(type(p) is not str or NAME.fullmatch(p) is None for p in profiles) or len(set(profiles)) != len(profiles):
            raise ValueError('Некорректные или повторные профили')
        seen.add(name)
        total += len(profiles)
        if total > 4096:
            raise ValueError('Каталог превышает 4096 профилей')
        result.append(dict(name=name, count=len(profiles)))
    return dict(families=sorted(result, key=lambda row: row['name']), profiles=total, selected_seed_limit=128)


def apply_family(config, family):
    if family is None:
        return config
    if type(family) is not str or family and NAME.fullmatch(family) is None:
        raise ValueError('Некорректный выбор семейства')
    choices = catalogue(config)
    counts = {row['name']: row['count'] for row in choices['families']}
    if family and family not in counts:
        raise ValueError('Семейство отсутствует в манифесте')
    count = counts[family] if family else choices['profiles']
    if count > choices['selected_seed_limit']:
        raise ValueError('Выбрано больше 128 seeds; выберите отдельное семейство')
    return replace(config, families=(family,) if family else (), profiles=())


SCRIPT = """'use strict';
const corpusPanel=node('article','','glass'),corpusSelect=document.createElement('select'),corpusApply=node('button','Выбрать корпус'),corpusReload=node('button','Обновить каталог'),corpusNote=node('p','','muted');
corpusSelect.setAttribute('aria-label','Семейство корпуса');corpusPanel.append(node('h2','Корпус следующей кампании'),corpusSelect,corpusApply,corpusReload,corpusNote,node('p','Каталог берётся из настроенного corpus.manifest. Выбор не меняет TOML и не генерирует новые файлы. Для обхода всех seeds задайте не меньше попыток, чем seeds в выбранной группе.','muted'));el('run').append(corpusPanel);
let corpusEpoch=-1,corpusLoading=false,corpusReady=false,corpusRetry=0,corpusDirty=false,corpusCatalogue=null;
corpusSelect.onchange=()=>{corpusDirty=true;};corpusReload.onclick=()=>{corpusReady=false;corpusRetry=0;};
corpusApply.onclick=async()=>{if(!token||!corpusReady)return;const family=corpusSelect.value==='@configured'?null:corpusSelect.value;try{state=await api('/api/corpus',{family});corpusDirty=false;await refresh();}catch(error){el('error').textContent=error.message;}};
setInterval(async()=>{
 if(corpusEpoch!==epoch){corpusEpoch=epoch;corpusReady=false;corpusLoading=false;corpusRetry=0;corpusDirty=false;corpusCatalogue=null;corpusSelect.replaceChildren();corpusNote.textContent='';}
 const blocked=!token||!state||Boolean(state.busy);corpusSelect.disabled=corpusApply.disabled=blocked||!corpusReady;corpusReload.disabled=blocked;
 if(!token||!state)return;
 if(!corpusReady&&!corpusLoading&&Date.now()>=corpusRetry){const gen=epoch;corpusLoading=true;try{
  const data=await api('/api/corpus');if(gen!==epoch)return;corpusCatalogue=data;corpusSelect.replaceChildren();
  function option(value,text,disabled=false){const o=node('option',text);o.value=value;o.disabled=disabled;corpusSelect.append(o);}
  option('@configured','Исходный выбор из TOML');option('','Все профили · '+data.profiles,data.profiles>data.selected_seed_limit);for(const row of data.families)option(row.name,row.name+' · '+row.count+' seeds');corpusReady=true;
 }catch(error){if(gen===epoch){corpusNote.textContent=error.message;corpusRetry=Date.now()+10000;}}finally{if(gen===epoch)corpusLoading=false;}}
 if(corpusReady){if(!corpusDirty)corpusSelect.value=state.corpus_family===null||state.corpus_family===undefined?'@configured':state.corpus_family;const row=corpusCatalogue.families.find(row=>row.name===corpusSelect.value);corpusNote.textContent='В каталоге: '+corpusCatalogue.profiles+' профилей'+(row?' · в группе: '+row.count+' seeds':'')+' · лимит кампании: '+corpusCatalogue.selected_seed_limit+'.';}
},500);
""".encode('utf-8')
