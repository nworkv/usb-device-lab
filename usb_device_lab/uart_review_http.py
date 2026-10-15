"""Authenticated, read-only generated HTML download for the guarded UART panel."""
import re
from urllib.parse import parse_qs, urlsplit
from .uart_web import Server as BaseServer, Handler, PAGE, artifact
from .uart_evidence import decode
from .uart_review import render, MAX_INPUT


def review(config, campaign, evidence):
    if re.fullmatch(r'[0-9a-f]{32}', campaign) is None or re.fullmatch(r'evidence-[0-9a-f]{32}', evidence) is None:
        raise ValueError('Invalid campaign/evidence identifier')
    values = []
    for name in ('summary.json', 'analysis.json', 'complete.json'):
        raw = artifact(config, campaign, evidence, name)
        if len(raw) > (65536 if name == 'complete.json' else MAX_INPUT):
            raise OverflowError('Review input exceeds its size limit')
        values.append(decode(raw))
    if values[0].get('capture_id') != campaign:
        raise ValueError('Evidence does not belong to the requested campaign')
    return render(*values)


SCRIPT = """'use strict';
(()=>{
const box=document.createElement('section'),label=document.createElement('p'),select=document.createElement('select'),button=document.createElement('button');
label.textContent='HTML-отчёт готового экспорта';button.textContent='Скачать HTML-отчёт';box.append(label,select,button);document.body.append(box);
function update(){const row=(window.rows||[]).find(x=>x.id===el('campaign').value),keep=select.value;select.replaceChildren();for(const value of row?.evidence||[]){const option=document.createElement('option');option.value=value;option.textContent=value;select.append(option);}select.value=(row?.evidence||[]).includes(keep)?keep:(row?.evidence||[])[0]||'';button.disabled=!select.value||!token;}
button.addEventListener('click',async()=>{if(!token||!select.value)return;const generation=epoch,campaign=el('campaign').value,evidence=select.value;button.disabled=true;try{const response=await fetch('/api/review?'+new URLSearchParams({campaign,evidence}),{headers:{Authorization:'Bearer '+token},signal:abort.signal});if(!response.ok)throw new Error((await response.json()).error||'Review unavailable');const blob=await response.blob();if(generation!==epoch)return;const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=campaign+'-'+evidence+'-review.html';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){if(generation===epoch&&error.name!=='AbortError')el('error').textContent=error.message;}finally{if(generation===epoch)update();}});
el('campaign').addEventListener('change',update);el('logout').addEventListener('click',()=>{select.replaceChildren();button.disabled=true;});setInterval(update,3000);update();
})();
""".encode('utf-8')


class ReviewHandler(Handler):
    def do_GET(self):
        if self.path in ('/', '/uart-review.js'):
            if not self.guard(auth=False):
                return
            if self.path == '/uart-review.js':
                return self.reply(200, SCRIPT, 'text/javascript; charset=utf-8')
            page = PAGE.replace(b'</html>', b'<script src="/uart-review.js"></script></html>')
            return self.reply(200, page, 'text/html; charset=utf-8')
        url = urlsplit(self.path)
        if url.path != '/api/review':
            return super().do_GET()
        if not self.guard():
            return
        try:
            query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            if set(query) != {'campaign', 'evidence'} or any(len(values) != 1 for values in query.values()):
                raise ValueError('Invalid query')
            campaign, evidence = query['campaign'][0], query['evidence'][0]
            data = review(self.server.supervisor.config, campaign, evidence)
            self.reply(200, data, 'application/octet-stream', campaign+'-'+evidence+'-review.html')
        except FileNotFoundError:
            self.reply(404, {'error':'Completed evidence export is not available'})
        except OverflowError as error:
            self.reply(413, {'error':str(error)})
        except (ValueError, TypeError, RecursionError):
            self.reply(400, {'error':'Invalid/incomplete evidence or unsupported review size'})
        except OSError:
            self.reply(503, {'error':'Evidence temporarily unavailable'})


class Server(BaseServer):
    def __init__(self, supervisor, token, port=8766):
        super().__init__(supervisor, token, port)
        self.RequestHandlerClass = ReviewHandler
