#!/usr/bin/env python3
"""Build a single subscription feed from data/*.json. Python 3.9+, no dependencies."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent

def escape(value):
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')

def fold(line):
    # RFC 5545: at most 75 octets per physical line, including continuation space.
    result, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            result.append(current)
            current = ' '
        current += char
    result.append(current)
    return '\r\n'.join(result)

def main():
    events = []
    for path in sorted((ROOT / 'data').glob('*.json')):
        dataset = json.loads(path.read_text(encoding='utf-8'))
        for item in dataset['events']:
            event = dict(item)
            event['start'] = date.fromisoformat(event['start'])
            event['end'] = date.fromisoformat(event['end'])
            if event['end'] < event['start'] or event['kind'] not in ('holiday', 'short'):
                raise ValueError(f'Invalid event in {path}: {item}')
            event['sources'] = {dataset['source']}
            event['notes'] = {event.get('description', '')} - {''}
            events.append(event)
    # Adjacent intervals of the same holiday merge, including across year files.
    merged = []
    for event in sorted(events, key=lambda x: (x['kind'], x['key'], x['start'])):
        if (merged and event['kind'] == 'holiday'
                and (merged[-1]['kind'], merged[-1]['key']) == (event['kind'], event['key'])
                and event['start'] <= merged[-1]['end'] + timedelta(days=1)):
            merged[-1]['end'] = max(merged[-1]['end'], event['end'])
            merged[-1]['sources'].update(event['sources'])
            merged[-1]['notes'].update(event['notes'])
        else:
            merged.append(event)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0',
             'PRODID:-//RU Production Calendar//RU', 'CALSCALE:GREGORIAN',
             'X-WR-CALNAME:Производственный календарь России',
             'X-WR-CALDESC:Нерабочие праздничные дни и сокращённые рабочие дни. Пятидневка.',
             'REFRESH-INTERVAL;VALUE=DURATION:P1D', 'X-PUBLISHED-TTL:P1D']
    for event in sorted(merged, key=lambda x: x['start']):
        start = event['start'].strftime('%Y%m%d')
        end = (event['end'] + timedelta(days=1)).strftime('%Y%m%d')
        description = []
        if event['kind'] == 'holiday':
            description.append('Нерабочие праздничные дни по статье 112 Трудового кодекса Российской Федерации.')
        description.extend(sorted(event['notes']))
        description.extend('Источник: ' + s for s in sorted(event['sources']))
        lines.extend(['BEGIN:VEVENT',
                      f"UID:ru-production-{event['key']}-{start}@calendar.invalid",
                      f'DTSTAMP:{stamp}', f'LAST-MODIFIED:{stamp}',
                      f'DTSTART;VALUE=DATE:{start}', f'DTEND;VALUE=DATE:{end}',
                      'SUMMARY:' + escape(event['title']),
                      'DESCRIPTION:' + escape('\n'.join(description)),
                      'URL:' + sorted(event['sources'])[0],
                      'TRANSP:TRANSPARENT', 'STATUS:CONFIRMED', 'END:VEVENT'])
    lines.append('END:VCALENDAR')
    (ROOT / 'calendar.ics').write_bytes(('\r\n'.join(map(fold, lines)) + '\r\n').encode('utf-8'))
    print(f'calendar.ics: {len(merged)} events')

if __name__ == '__main__':
    main()
