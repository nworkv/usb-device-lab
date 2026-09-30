import re

PATTERN=re.compile(r'(BUG:|WARNING:|KASAN:|KMSAN:|UBSAN:|Oops:|Kernel panic|general protection fault|INFO: task .* blocked)')

def classify(result):
    errors=list(result.get('errors',[]));seen=set()
    for line in result.get('kernel_log','').splitlines():
        m=PATTERN.search(line)
        if m:
            summary=re.sub(r'0x[0-9a-fA-F]+|\+[0-9a-fA-F]+/[0-9a-fA-F]+','<addr>',line[m.start():])
            if summary not in seen: seen.add(summary);errors.append({'kind':'kernel','summary':summary})
    for line in result.get('executor_log','').splitlines():
        if '"kind":' in line and ('_error"' in line or '"protocol_error"' in line):
            errors.append({'kind':'executor_io','summary':line[:1024]})
    if result.get('log_dropped'): errors.append({'kind':'log_loss','summary':'kernel log records lost or truncated'})
    if result.get('saturated'): errors.append({'kind':'coverage','summary':'KCOV buffer saturated; coverage rejected'})
    if result.get('executor_returncode') not in (0,None,-15,-9):
        errors.append({'kind':'executor','summary':'executor exited with code %s'%result.get('executor_returncode')})
    if not result.get('pcs') and not result.get('saturated') and not any(e['kind'] in ('infrastructure','cleanup') for e in errors):
        result['coverage_valid']=False
        errors.append({'kind':'coverage_unavailable','summary':'no remote USB coverage observed; check bus and kernel instrumentation'})
    result['errors']=errors
    return result
