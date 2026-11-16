"""Synthetic source tests and generator round-trip checks, without network access."""
import calendar
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import generate
import update_calendar as importer

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    # Independent compact fixture for 2027, including the working Saturday.
    holidays = {date(2027, 1, d) for d in range(1, 9)} | {
        date(2027, m, d) for m, d in [(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)]}
    transfers = {date(2027, m, d) for m, d in [(2, 22), (5, 3), (5, 10), (6, 14), (11, 5), (12, 31)]}
    shorts = {date(2027, m, d) for m, d in [(2, 20), (4, 30), (6, 11), (11, 3)]}
    html = ['<h1>Производственный календарь на 2027 год</h1>']
    for month in range(1, 13):
        html.append(f'<table class="cal"><tr><th class="month">{importer.MONTH_NAMES[month - 1]}</th></tr><tr>')
        for number in range(1, calendar.monthrange(2027, month)[1] + 1):
            d = date(2027, month, number)
            off = (d.weekday() >= 5 and d != date(2027, 2, 20)) or d in holidays or d in transfers
            cls = 'weekend' if off else 'preholiday' if d in shorts else ''
            html.append(f'<td class="{cls}">{number}{"*" if d in shorts else ""}</td>')
        html.append('</tr></table>')
    html.append('<h3>Нерабочие праздничные дни в 2027 году</h3><blockquote>')
    for dates, title in zip(['1, 2, 3, 4, 5, 6 и 8 января', '7 января', '23 февраля', '8 марта', '1 мая', '9 мая', '12 июня', '4 ноября'], importer.KEYS):
        html.append(f'<p>{dates} — {title};</p>')
    html.append('</blockquote><h3>Перенос выходных дней</h3><p>следующий после праздничного рабочий день; с 1 по 8 января.</p>')
    html.append('<p>В 2027 году в соответствии с Постановлением Правительства РФ от 17.09.2026 N 1187 перенесены следующие выходные дни:</p><ul>')
    for text in ['с субботы 2 января на пятницу 5 ноября;', 'с воскресенья 3 января на пятницу 31 декабря;', 'с субботы 20 февраля на понедельник 22 февраля.']:
        html.append(f'<li>{text}</li>')
    html.append('</ul><h3>Определение нормы рабочего времени</h3>')
    html.append('<p>В 2027 году работники будут работать на один час меньше 20 февраля, 30 апреля, 11 июня, 3 ноября.</p>')
    html.append('<h3>Примеры</h3><p>В 2027 году в целом при пятидневной рабочей неделе с двумя выходными днями будет 247 рабочих дней, в том числе 4 сокращенных на один час рабочих дня, указанных выше, и 118 выходных и нерабочих праздничных дней.</p>')
    return ''.join(html)


class CalendarTests(unittest.TestCase):
    def parse(self, html):
        with redirect_stdout(io.StringIO()):
            return importer.parse_calendar(html, 2027)

    def test_source_round_trip(self):
        result = self.parse(fixture())
        self.assertEqual(len(result['events']), 19)
        events = {e['start']: e for e in result['events']}
        self.assertEqual(events['2027-01-01']['end'], '2027-01-06')
        self.assertEqual(events['2027-01-07']['key'], 'christmas')
        self.assertEqual(events['2027-01-08']['key'], 'new-year')
        self.assertEqual(events['2027-02-20']['kind'], 'short')
        self.assertIn('День защитника Отечества', events['2027-02-20']['description'])
        self.assertEqual(events['2027-02-22']['description'], 'Выходной перенесён с 20.02.2027.')
        self.assertEqual(events['2027-05-03']['description'], 'Выходной перенесён с 01.05.2027.')
        self.assertEqual(events['2027-12-31']['description'], 'Выходной перенесён с 03.01.2027.')
        self.assertNotIn('2027-01-11', events)
        self.assertNotIn('2027-12-30', events)

    def test_rejects_incomplete_or_changed_source(self):
        html = fixture()
        replacements = [
            ('на 2027 год</h1>', 'на 2026 год</h1>'),
            ('в соответствии с Постановлением', 'в соответствии с проектом Постановления'),
            ('17.09.2026', '17.09.2024'),
            ('с субботы 2 января', 'с воскресенья 2 января'),
            ('<td class="preholiday">20*</td>', '<td class="">20</td>'),
            ('247 рабочих', '248 рабочих'),
            ('День Победы;', 'Неизвестный праздник;'),
            ('<td class="weekend">22</td>', '<td class="">22</td>'),
            ('<table class="cal">', '<table class="other">'),
        ]
        for old, new in replacements:
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.parse(html.replace(old, new))

    def test_unchanged_data_does_not_regenerate(self):
        dataset = self.parse(fixture())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            (root / 'data/2027.json').write_text(json.dumps(dataset))
            with patch.object(importer, 'ROOT', root), patch.object(importer.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
                importer.update(dataset)
                run.assert_not_called()

    def test_successful_update_preserves_old_year(self):
        dataset = self.parse(fixture())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            original = (ROOT / 'data/2026.json').read_bytes()
            (root / 'data/2026.json').write_bytes(original)
            for name in ['README.md', 'index.html', 'calendar.ics']:
                (root / name).write_bytes((ROOT / name).read_bytes())
            with patch.object(importer, 'ROOT', root), patch.object(generate, 'ROOT', root), patch.object(importer.subprocess, 'run', side_effect=lambda *a, **k: generate.main()), redirect_stdout(io.StringIO()):
                importer.update(dataset)
            self.assertEqual((root / 'data/2026.json').read_bytes(), original)
            self.assertEqual(json.loads((root / 'data/2027.json').read_text()), dataset)
            self.assertIn('2026–2027', (root / 'index.html').read_text())
            today = importer.datetime.now(importer.MOSCOW).date()
            stamp = f'{today.day} {importer.MONTHS[today.month - 1]} {today.year} года'
            self.assertIn(stamp, (root / 'README.md').read_text())
            self.assertIn(b'DTSTART;VALUE=DATE:20270220', (root / 'calendar.ics').read_bytes())

    def test_failed_generation_restores_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            for name in ['README.md', 'index.html', 'calendar.ics']:
                (root / name).write_bytes((ROOT / name).read_bytes())
            before = {p: p.read_bytes() for p in root.iterdir() if p.is_file()}
            with patch.object(importer, 'ROOT', root), patch.object(importer.subprocess, 'run', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):
                    importer.update(self.parse(fixture()))
            self.assertFalse((root / 'data/2027.json').exists())
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_ics_format_and_stable_uids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            for src in (ROOT / 'data').glob('*.json'):
                (root / 'data' / src.name).write_bytes(src.read_bytes())
            with patch.object(generate, 'ROOT', root), redirect_stdout(io.StringIO()):
                generate.main()
            raw = (root / 'calendar.ics').read_bytes()
        self.assertTrue(raw.endswith(b'\r\n'))
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertNotIn(b'\r', raw.replace(b'\r\n', b''))
        for line in raw.split(b'\r\n'):
            self.assertLessEqual(len(line), 75)
            line.decode('utf-8')
        unfolded = raw.replace(b'\r\n ', b'').decode()
        self.assertNotIn('VALARM', unfolded)
        uids = []
        for block in unfolded.split('BEGIN:VEVENT\r\n')[1:]:
            fields = dict(line.split(':', 1) for line in block.split('END:VEVENT')[0].splitlines() if ':' in line)
            uids.append(fields['UID'])
            self.assertLess(fields['DTSTART;VALUE=DATE'], fields['DTEND;VALUE=DATE'])
            self.assertEqual(fields['TRANSP'], 'TRANSPARENT')
            self.assertTrue(fields['URL'].startswith('https://www.consultant.ru/'))
        self.assertEqual(len(uids), len(set(uids)))
        existing = (ROOT / 'calendar.ics').read_text()
        self.assertEqual(set(uids), {line[4:] for line in existing.splitlines() if line.startswith('UID:')})


if __name__ == '__main__':
    unittest.main()
