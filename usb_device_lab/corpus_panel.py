"""Russian read-only corpus cards and authenticated browser routes."""
import re
from urllib.parse import parse_qs, urlsplit

CORPUS_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>USB Device Lab — корпус</title><link rel="stylesheet" href="/control.css"></head><body><main class="shell">
<nav class="actions"><a href="/">Управление</a><a href="/events">События</a><a href="/results">Результаты</a><a href="/logs">Логи</a><a href="/corpus">Корпус</a></nav>
<header><h1>Корпус устройств</h1><p class="subtitle">Конфигурации, которыми пользуется фаззер. Просмотр не запускает устройство.</p></header>
<section class="glass panel"><label for="corpus-token">Токен доступа</label><input id="corpus-token" type="password" autocomplete="off">
<label for="corpus-source">Источник</label><select id="corpus-source"><option value="seeds">Исходные seeds, выбранные в lab.toml</option><option value="learned">Индекс корпуса кампании, включая seeds</option></select>
<div class="fields"><div><label for="corpus-family">Семейство — точное имя или пусто</label><input id="corpus-family" maxlength="127" placeholder="hid"></div><div><label for="corpus-profile">Профиль — точное имя или пусто</label><input id="corpus-profile" maxlength="127" placeholder="keyboard"></div></div>
<div class="actions"><button id="corpus-load">Обновить</button><button id="corpus-next">Следующая порция</button></div>
<p id="corpus-notice" role="status" aria-live="polite">Введите токен для просмотра.</p><div id="corpus-cards" class="metrics"></div>
<p class="hint">Неизвестные family/profile показываются как unknown, без догадок по имени файла. Количество endpoints включает все runtime-альтернативы, а не только активные.</p>
<h2>Конфигурация выбранного элемента</h2><pre id="corpus-detail">Выберите карточку.</pre></section>
</main><script src="/corpus.js"></script></body></html>
""".encode('utf-8')
CORPUS_SCRIPT = r"""'use strict';
const ce=id=>document.getElementById(id);let corpusOffset=0,corpusNext=0,corpusBusy=false,corpusGeneration=0;
for(const id of ['corpus-token','corpus-source','corpus-family','corpus-profile'])ce(id).addEventListener('input',()=>{corpusGeneration++;corpusOffset=0;corpusNext=0;ce('corpus-cards').replaceChildren();ce('corpus-detail').textContent='Выберите карточку.';});
async function corpusGet(url){const token=ce('corpus-token').value.trim();if(!token)throw new Error('Введите токен.');const response=await fetch(url,{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw new Error('Не удалось прочитать корпус: HTTP '+response.status);return response.json();}
async function corpusDetail(source,id){const generation=corpusGeneration;try{const data=await corpusGet('/api/corpus/'+source+'/'+id);if(generation===corpusGeneration)ce('corpus-detail').textContent=JSON.stringify(data,null,2);}catch(error){if(generation===corpusGeneration)ce('corpus-notice').textContent=String(error.message||error);}}
async function loadCorpus(){if(corpusBusy)return;corpusBusy=true;const generation=corpusGeneration;try{
 const source=ce('corpus-source').value,q=new URLSearchParams({source,offset:corpusOffset,family:ce('corpus-family').value.trim(),profile:ce('corpus-profile').value.trim()});const data=await corpusGet('/api/corpus?'+q);if(generation!==corpusGeneration)return;
 ce('corpus-cards').replaceChildren();for(const item of data.items){const card=document.createElement('article');card.className='glass panel';const title=document.createElement('h3');title.textContent=item.family+' / '+item.profile;const name=document.createElement('p');name.textContent=item.name;const description=document.createElement('p');description.textContent=item.valid?item.description:item.error;card.append(title,name,description);
 if(item.valid){const parameters=document.createElement('p');parameters.textContent='Дескрипторов: '+item.descriptor_count+'; конфигураций: '+item.configuration_count+'; интерфейсов: '+item.interface_count+'; endpoints по альтернативам: '+item.endpoint_count;const button=document.createElement('button');button.textContent='Показать JSON и параметры';button.addEventListener('click',()=>corpusDetail(item.source,item.id));card.append(parameters,button);}ce('corpus-cards').append(card);}
 corpusNext=data.next_offset;ce('corpus-next').disabled=!data.more;ce('corpus-notice').textContent='Карточек: '+data.items.length+'; просмотрено записей: '+data.scanned+(data.more?' · доступна следующая порция':' · конец списка');
 }catch(error){if(generation===corpusGeneration)ce('corpus-notice').textContent=String(error.message||error);}finally{corpusBusy=false;}}
ce('corpus-load').addEventListener('click',()=>{corpusOffset=0;loadCorpus();});ce('corpus-next').addEventListener('click',()=>{if(!corpusBusy){corpusOffset=corpusNext;loadCorpus();}});
""".encode('utf-8')


class CorpusRoutes:
    def do_GET(self):
        if self.path in ('/corpus', '/corpus.js'):
            if self.guard(authenticated=False):
                self.reply(200, CORPUS_PAGE if self.path == '/corpus' else CORPUS_SCRIPT,
                           'text/html; charset=utf-8' if self.path == '/corpus' else 'text/javascript; charset=utf-8')
            return
        parsed = urlsplit(self.path)
        if parsed.path != '/api/corpus' and not parsed.path.startswith('/api/corpus/'):
            return super().do_GET()
        if not self.guard():
            return
        browser = getattr(self.server, 'corpus_browser', None)
        if browser is None:
            self.reply(503, {'error': 'Corpus browser is not configured'})
            return
        try:
            if parsed.path == '/api/corpus':
                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
                if set(query) - {'source', 'offset', 'family', 'profile'} or any(len(values) != 1 for values in query.values()):
                    raise ValueError('Invalid corpus query')
                offset = query.get('offset', ['0'])[0]
                if not offset.isascii() or not offset.isdigit():
                    raise ValueError('Invalid offset')
                data = browser.list(query.get('source', ['seeds'])[0], int(offset),
                                    query.get('family', [''])[0], query.get('profile', [''])[0])
            else:
                match = re.fullmatch(r'/api/corpus/(seeds|learned)/([0-9a-f]{64})', parsed.path)
                if match is None or parsed.query:
                    raise ValueError('Invalid corpus detail request')
                data = browser.detail(*match.groups())
        except FileNotFoundError:
            self.reply(404, {'error': 'Corpus entry unavailable'})
            return
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            self.reply(400, {'error': 'Invalid corpus request or configuration'})
            return
        except Exception:
            self.reply(503, {'error': 'Corpus storage unavailable'})
            return
        self.reply(200, data)
