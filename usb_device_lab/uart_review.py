"""Offline HTML review of an existing evidence export; no active content or verdicts."""
import argparse
import html
import json
import os
import re
import uuid
from pathlib import Path
from .uart_evidence import decode, read_file

MAX_INPUT = 8 * 1024 * 1024
MAX_HTML = 16 * 1024 * 1024


def render(summary, analysis, marker):
    ident = summary.get('capture_id')
    if type(ident) is not str or re.fullmatch(r'[0-9a-f]{32}', ident) is None or analysis.get('capture_id') != ident or marker.get('capture_id') != ident:
        raise ValueError('Evidence capture identity mismatch')
    if marker.get('export_complete') is not True or type(marker.get('schema_version')) is not int or marker['schema_version'] != 1:
        raise ValueError('Completed evidence export required')
    if type(summary.get('schema_version')) is not int or summary['schema_version'] != 1:
        raise ValueError('Unsupported summary schema')
    if summary.get('causal') is not False or analysis.get('causal') is not False or summary.get('completeness') != 'unknown':
        raise ValueError('Unexpected evidence semantics; no confirmed verdicts are rendered')
    attempts, reports, diagnostics = summary.get('attempts'), analysis.get('reports'), summary.get('diagnostics')
    if type(attempts) is not list or len(attempts)>1024 or type(reports) is not list or len(reports)>1024 or type(diagnostics) is not list or len(diagnostics)>100000:
        raise ValueError('Invalid evidence lists/limits')
    chunks, size = [], 0
    def add(value):
        nonlocal size
        size += len(value.encode('utf-8'))
        if size > MAX_HTML:
            raise ValueError('Rendered HTML exceeds 16 MiB')
        chunks.append(value)
    def text(value):
        value = value if type(value) is str else json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(value)>1024*1024:
            raise ValueError('Display field exceeds 1 Mi characters; use original JSON')
        return html.escape(value, quote=True)
    add('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>UART: просмотр свидетельств</title>'
        '<style>body{font:16px system-ui;max-width:1100px;margin:auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eee;padding:16px}article{border:1px solid #aaa;margin:16px 0;padding:16px}th,td{text-align:left;padding:8px;border-bottom:1px solid #ccc}table{border-collapse:collapse}aside{background:#fff2c0;padding:16px}</style></head><body>')
    add('<h1>UART: просмотр свидетельств</h1><aside>Это кандидаты для проверки, не подтверждённые ошибки и не доказанная причина сбоя. '
        'Полнота UART-наблюдения неизвестна. Завершение экспорта не означает завершение кампании. Тишина не считается зависанием.</aside>')
    add('<p>Сеанс захвата: '+text(ident)+'</p><p>Проиндексировано байтов: '+text(summary.get('indexed_bytes'))+
        '; неиндексированный хвост: '+text(summary.get('unindexed_tail_bytes'))+'</p>')
    add('<h2>Попытки</h2><table><thead><tr><th>Попытка</th><th>Окно / результат доступны</th><th>Связанные кандидаты</th></tr></thead><tbody>')
    for attempt in attempts:
        if type(attempt) is not dict:
            raise ValueError('Invalid attempt')
        add('<tr><td>'+text(attempt.get('attempt_id'))+'</td><td>'+text([attempt.get('window_available'),attempt.get('result_available')])+
            '</td><td>'+text(attempt.get('candidate_reports',[]))+'</td></tr>')
    add('</tbody></table><h2>Кандидаты на сообщения об ошибке ядра</h2>')
    if not reports:
        add('<p>Распознанных сообщений нет; это не доказывает отсутствие ошибок.</p>')
    labels = {'candidate':'Кандидат на связь', 'ambiguous':'Неоднозначная связь', 'unassigned':'Без привязки'}
    for report in reports:
        if type(report) is not dict or report.get('causal') is not False:
            raise ValueError('Invalid/nonobservational report')
        add('<article><h3>'+text(report.get('report_id'))+'</h3><p>'+text(labels.get(report.get('association'),'Статус неизвестен'))+
            '</p><p>Диапазон байтов: '+text([report.get('offset_start'),report.get('offset_end')])+
            '; соединение: '+text(report.get('connection_epoch'))+'</p><p>Завершение разбора: '+text(report.get('termination'))+
            '; возможное обрезание: '+text(report.get('truncated'))+'</p><pre>'+text(report.get('associations',[]))+'</pre>'+
            '<details><summary>Предшествующий контекст</summary><pre>'+text(report.get('context',''))+'</pre></details>'+
            '<h4>Исходное сообщение и стек</h4><pre>'+text(report.get('text',''))+'</pre></article>')
    add('<h2>Диагностика канала и выполнения</h2>')
    names = {'uart_capture':'Канал UART', 'executor_exit':'Выход исполнителя', 'campaign_infrastructure':'Инфраструктура кампании',
        'attempt_incomplete':'Неполные артефакты попытки', 'metadata_tail':'Хвост метаданных', 'unindexed_raw_tail':'Неиндексированные байты'}
    if not diagnostics:
        add('<p>Диагностических записей нет; это не гарантия исправности стенда.</p>')
    for item in diagnostics:
        if type(item) is not dict:
            raise ValueError('Invalid diagnostic')
        add('<article><h3>'+text(names.get(item.get('kind'),'Другая диагностическая запись'))+'</h3><pre>'+text(item)+'</pre></article>')
    add('<footer>Категории могут пересекаться. Для проверки используйте исходные JSON и UART-байты. Автоматическое подтверждение или отбор корпуса не выполнялись.</footer></body></html>')
    return ''.join(chunks).encode('utf-8')


def export_html(directory):
    directory = Path(directory)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = '.review-' + uuid.uuid4().hex + '.tmp'
    created = False
    try:
        summary = decode(read_file(fd, 'summary.json', MAX_INPUT))
        analysis = decode(read_file(fd, 'analysis.json', MAX_INPUT))
        marker = decode(read_file(fd, 'complete.json', 65536))
        data = render(summary, analysis, marker)
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        created = True
        with os.fdopen(out, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, 'review.html', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=fd)
        created = False
        os.fsync(fd)
        return directory / 'review.html'
    finally:
        try:
            if created:
                os.unlink(temporary, dir_fd=fd)
        finally:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Render an existing, stopped UART evidence export as offline HTML')
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args(argv)
    try:
        output = export_html(args.evidence)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        parser.exit(2, 'HTML review failed: '+str(error)+'\n')
    print(str(output))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
