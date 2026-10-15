"""Bounded previews and allowlisted file descriptors; original logs stay untouched."""
import hashlib
import json
import os
import re
import stat
from .dual_web import IDENT, ATTEMPT, PIPELINE, artifact

CAMPAIGN = {'campaign.json','checkpoint.json','selection.json','transport-analysis.json','replay-source.json','replay-result.json'}
CAPTURE = {'uart.raw','chunks.jsonl','events.jsonl','ssh-stderr.log'}
ATTEMPT_FILES = {'config.json','started.json','window.json','result.json','executor.log'}
EXPORT = {'pipeline-summary.json','summary.json','complete.json','analysis-error.json','selection.json','exports.jsonl','reports.jsonl','candidates.sqlite3','analysis.json'}


def open_file(config, campaign, area, name):
    if type(campaign) is not str or IDENT.fullmatch(campaign) is None:
        raise ValueError('Некорректный запуск')
    if area == 'campaign':
        root, parts, allowed = config.results_dir, [campaign], CAMPAIGN
    elif area == 'capture':
        meta = json.loads(artifact(config,campaign,'campaign','campaign.json',65536))
        if meta.get('mode') not in ('uart','ssh') or meta.get('capture_id') != campaign:
            raise ValueError('Некорректная идентификация захвата')
        root, parts, allowed = config.logs_dir, [meta['mode'],campaign], CAPTURE
    elif type(area) is str and ATTEMPT.fullmatch(area):
        root, parts, allowed = config.results_dir, [campaign,area], ATTEMPT_FILES
    elif type(area) is str and PIPELINE.fullmatch(area):
        root, parts, allowed = config.results_dir, [campaign,area], EXPORT
        if type(name) is str and re.fullmatch(r'report-[0-9]{6}\.json',name):
            allowed = allowed | {name}
    else:
        raise ValueError('Некорректная область файла')
    if type(name) is not str or name not in allowed:
        raise ValueError('Файл не разрешён для выдачи')
    fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        leaf=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        stream=os.fdopen(leaf,'rb')
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            stream.close();raise ValueError('Требуется обычный файл')
        return stream
    finally:
        os.close(fd)


def read_json(config,campaign,area,name,limit=1048576):
    with open_file(config,campaign,area,name) as f:
        data=f.read(limit+1)
    if len(data)>limit:raise OverflowError('JSON превышает ограничение просмотра')
    value=json.loads(data)
    if type(value) is not dict:raise ValueError('Ожидался JSON-объект')
    return value


def severity(text):
    if re.search(r'kernel panic|\bOops\b|\bBUG:|KASAN:|KMSAN:|KFENCE:|(?:soft|hard) lockup',text,re.I):
        return 'critical','Критический текстовый маркер, требует проверки'
    priority=re.match(r'^\s*<(\d{1,3})>',text)
    if priority:
        value=int(priority[1])
        if value<=191:
            level=value%8
            if level<=2:return 'critical','Приоритет '+str(level)+'; не доказательство падения'
            if level==3:return 'error','Приоритет 3'
            if level==4:return 'warning','Приоритет 4'
    if re.search(r'permission denied|traceback|\berror\b|\bfailed\b|UBSAN:',text,re.I):
        return 'error','Текстовый маркер ошибки'
    if re.search(r'\bwarning\b|\bWARN\b|disconnected',text,re.I):
        return 'warning','Текстовый маркер предупреждения'
    return 'info','Без распознанного маркера'


def preview(config,campaign,area,name,*,offset=None,limit=65536,lines=300,level='all',query=''):
    if type(limit) is not int or not 1<=limit<=262144 or type(lines) is not int or not 1<=lines<=1000:
        raise ValueError('Некорректный размер окна')
    if level not in ('all','critical','error','warning','info') or type(query) is not str or len(query)>128:
        raise ValueError('Некорректный фильтр')
    if name not in CAPTURE|{'executor.log','reports.jsonl'}:raise ValueError('Это не журнал')
    with open_file(config,campaign,area,name) as f:
        size=os.fstat(f.fileno()).st_size
        start=max(0,size-limit) if offset is None else offset
        if type(start) is not int or not 0<=start<=size:raise ValueError('Смещение вне файла')
        partial_head=False
        if start:
            f.seek(start-1);partial_head=f.read(1)!=b'\n'
        f.seek(start);raw=f.read(min(limit,size-start))
    rows=[];position=start;counts={key:0 for key in ('critical','error','warning','info')}
    for chunk in raw.splitlines(keepends=True):
        text=chunk.decode('utf-8',errors='replace').rstrip('\r\n')
        kind,reason=severity(text);counts[kind]+=1
        if (level=='all' or level==kind) and query.casefold() in text.casefold():
            rows.append(dict(start=position,end=position+len(chunk),level=kind,reason=reason,
                             text=text[:8192],display_truncated=len(text)>8192))
        position+=len(chunk)
    shown=rows[-lines:] if offset is None else rows[:lines]
    return dict(size=size,start=start,end=start+len(raw),rows=shown,window_counts=counts,
        omitted_matching_rows=max(0,len(rows)-len(shown)),partial_head=partial_head,
        partial_tail=bool(raw and not raw.endswith(b'\n')),search_scope='loaded_window',
        completeness='unknown',causal=False,filters=dict(level=level,query=query,lines=lines,limit=limit),
        raw_window_sha256=hashlib.sha256(raw).hexdigest())


def export_paths(config,campaign):
    if type(campaign) is not str or IDENT.fullmatch(campaign) is None:raise ValueError('Некорректный запуск')
    yield from (('campaign',name) for name in sorted(CAMPAIGN))
    yield from (('capture',name) for name in sorted(CAPTURE))
    fd=os.open(config.results_dir,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        child=os.open(campaign,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        try:names=os.listdir(child)
        finally:os.close(child)
    finally:os.close(fd)
    for area in sorted(names):
        if ATTEMPT.fullmatch(area):
            yield from ((area,name) for name in sorted(ATTEMPT_FILES))
        elif PIPELINE.fullmatch(area):
            yield from ((area,name) for name in sorted(EXPORT))
