#!/usr/bin/env python3
"""Import an approved five-day calendar from ConsultantPlus (Python 3.9+)."""
import argparse
import calendar
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MOSCOW = timezone(timedelta(hours=3))
MONTHS = 'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()
MONTH_NAMES = 'январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь'.split()
KEYS = dict(zip([
    'Новогодние каникулы', 'Рождество Христово', 'День защитника Отечества',
    'Международный женский день', 'Праздник Весны и Труда', 'День Победы',
    'День России', 'День народного единства',
], ['new-year', 'christmas', 'defender', 'womens-day', 'spring-labour', 'victory', 'russia', 'unity']))
WEEKDAYS = 'понедельника вторника среды четверга пятницы субботы воскресенья'.split()
TARGET_WEEKDAYS = 'понедельник вторник среду четверг пятницу субботу воскресенье'.split()


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ' '.join(''.join(c if isinstance(c, str) else c.text() for c in self.children).split())

    def find(self, tag):
        for c in self.children:
            if isinstance(c, Node):
                if c.tag == tag:
                    yield c
                yield from c.find(tag)


class Page(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def section(self, heading):
        # Source sections are siblings of their h3 headings.
        for parent in self.walk(self.root):
            for i, child in enumerate(parent.children):
                if isinstance(child, Node) and child.tag == 'h3' and child.text().startswith(heading):
                    result = Node()
                    for sibling in parent.children[i + 1:]:
                        if isinstance(sibling, Node) and sibling.tag in ('h2', 'h3'):
                            break
                        result.children.append(sibling)
                    return result
        raise ValueError(f'Не найден раздел: {heading}')

    def walk(self, node):
        yield node
        for child in node.children:
            if isinstance(child, Node):
                yield from self.walk(child)


def day(year, number, month):
    return date(year, MONTHS.index(month) + 1, int(number))


def event(d, key, title, kind, description=None):
    result = dict(start=d.isoformat(), end=d.isoformat(), key=key, title=title, kind=kind)
    if description:
        result['description'] = description
    return result


def parse_calendar(html, year):
    page = Page(html)
    require([h.text() for h in page.root.find('h1')] == [f'Производственный календарь на {year} год'], 'Неверный год или заголовок страницы')
    holidays = {}
    section = page.section('Нерабочие праздничные дни')
    quotes = list(section.find('blockquote'))
    require(len(quotes) == 1, 'Изменилась структура списка праздников')
    titles = set()
    for p in quotes[0].find('p'):
        match = re.fullmatch(r'([\d, и]+) (\w+)\s*[—–-]\s*(.+?)[;.]?', p.text())
        require(match is not None, f'Не распознан праздник: {p.text()}')
        numbers, month, title = match.groups()
        title = title.rstrip(';.')
        require(title in KEYS and title not in titles, f'Неизвестный или повторный праздник: {title}')
        titles.add(title)
        for number in re.findall(r'\d+', numbers):
            d = day(year, number, month)
            require(d not in holidays, f'Повтор праздника: {d}')
            holidays[d] = event(d, KEYS[title], title, 'holiday')
    require(titles == set(KEYS), 'Неполный список федеральных праздников')

    section = page.section('Перенос выходных дней')
    text = section.text()
    require(not re.search(r'проект|предполага|планиру', text, re.I), 'Переносы пока не утверждены')
    decree = re.search(rf'В {year} году в соответствии с Постановлением Правительства РФ от (\d{{2}}\.\d{{2}}\.\d{{4}}) N ([\d-]+).*?перенесены следующие выходные дни', text)
    require(decree is not None, 'Не найдено утверждённое постановление о переносах')
    decree_date = datetime.strptime(decree[1], '%d.%m.%Y').date()
    require(year - 1 <= decree_date.year <= year and decree_date <= datetime.now(MOSCOW).date(), 'Неожиданная дата постановления')
    transfers, origins = {}, set()
    for li in section.find('li'):
        m = re.fullmatch(r'с (\w+) (\d+) (\w+) на (\w+) (\d+) (\w+)[;.]?', li.text())
        require(m is not None, f'Не распознан перенос: {li.text()}')
        wd1, n1, m1, wd2, n2, m2 = m.groups()
        origin, target = day(year, n1, m1), day(year, n2, m2)
        require(wd1 == WEEKDAYS[origin.weekday()] and wd2 == TARGET_WEEKDAYS[target.weekday()], 'Дата переноса не совпадает с днём недели')
        require(origin.weekday() >= 5 and origin != target and target.weekday() < 5, 'Неожиданный перенос: требуется ручная проверка')
        require(origin not in origins and target not in transfers and target not in holidays, 'Повтор или пересечение переноса')
        origins.add(origin)
        transfers[target] = origin
    require(transfers, 'Пустой список переносов')
    require('следующий после праздничного рабочий день' in text and 'с 1 по 8 января' in text, 'Не найдено правило переноса при совпадении праздника с выходным')
    # January is governed only by the explicit decree, never by this rule.
    for d in sorted(holidays):
        if d.month == 1 or d.weekday() < 5 or d in origins:
            continue
        target = d + timedelta(days=1)
        while target.weekday() >= 5 or target in holidays or target in transfers:
            target += timedelta(days=1)
        require(target.year == year, 'Межгодовой перенос требует ручной проверки')
        transfers[target] = d

    marks, short = {}, set()
    tables = [t for t in page.root.find('table') if 'cal' in t.attrs.get('class', '').split()]
    require(len(tables) == 12, 'Ожидалось 12 таблиц календаря пятидневки')
    for month, table in enumerate(tables, 1):
        headings = [n.text().lower() for n in table.find('th') if 'month' in n.attrs.get('class', '').split()]
        require(headings == [MONTH_NAMES[month - 1]], 'Изменился порядок месяцев')
        for td in table.find('td'):
            label = td.text()
            if not label:
                continue
            require(re.fullmatch(r'\d+\s*\*?', label) is not None, 'Неизвестная отметка календаря')
            d = date(year, month, int(re.match(r'\d+', label)[0]))
            classes = set(td.attrs.get('class', '').split())
            require(d not in marks, f'Повтор дня: {d}')
            marks[d] = bool(classes & {'weekend', 'holiday'})
            if 'preholiday' in classes:
                require('*' in label and not marks[d], f'Некорректный сокращённый день: {d}')
                short.add(d)
            else:
                require('*' not in label, 'Неизвестный формат сокращённого дня')
    days = {date(year, m, d) for m in range(1, 13) for d in range(1, calendar.monthrange(year, m)[1] + 1)}
    require(set(marks) == days, 'Неполный календарь')
    working_weekends = origins - set(holidays)
    expected_off = {d for d in days if (d.weekday() >= 5 and d not in working_weekends) or d in holidays or d in transfers}
    require({d for d, off in marks.items() if off} == expected_off, 'Даты выходных в таблицах не совпадают с праздниками и переносами')
    comment = page.section('Определение нормы рабочего времени').text()
    m = re.search(rf'В {year} году работники будут работать на один час меньше ([^.()]+)', comment)
    require(m is not None, 'Не найден список сокращённых дней в комментарии')
    pairs = re.findall(r'(\d+) (' + '|'.join(MONTHS) + ')', m[1])
    require(short == {day(year, n, month) for n, month in pairs} and short, 'Сокращённые дни в комментарии и таблицах не совпадают')
    require(not short & expected_off, 'Сокращённый день совпал с выходным')
    full_text = page.root.text()
    totals = re.search(rf'В {year} году в целом при пятидневной рабочей неделе с двумя выходными днями будет (\d+) рабочих дней, в том числе (\d+) сокращенн.*?и (\d+) выходных и нерабочих праздничных дней', full_text)
    require(totals is not None, 'Не найдены годовые итоги')
    require(tuple(map(int, totals.groups())) == (len(days - expected_off), len(short), len(expected_off)), 'Не совпадают годовые итоги')

    events = list(holidays.values())
    for target, origin in transfers.items():
        events.append(event(target, 'transfer', 'Перенесённый выходной', 'transfer', f'Выходной перенесён с {origin:%d.%m.%Y}.'))
    for d in short:
        # A working Saturday inherits the hours of the weekday moved onto it.
        effective = next((target for target, origin in transfers.items() if origin == d), d)
        upcoming = [h for h in sorted(holidays) if 0 < (h - effective).days <= 7]
        require(upcoming, f'Не определён праздник для сокращённого дня {d}')
        holiday = upcoming[0]
        title = holidays[holiday]['title']
        description = f'Рабочий день сокращён на один час. Предстоящий праздник: {title}, {holiday:%d.%m.%Y}.'
        events.append(event(d, 'short', 'Сокращённый рабочий день', 'short', description))
    # Store adjacent dates of the same holiday as intervals; keep January 7 separate.
    merged = []
    for item in sorted(events, key=lambda e: e['start']):
        if (merged and item['kind'] == 'holiday' and merged[-1]['key'] == item['key']
                and date.fromisoformat(merged[-1]['end']) + timedelta(days=1) == date.fromisoformat(item['start'])):
            merged[-1]['end'] = item['end']
        else:
            merged.append(item)
    print(f'{year}: постановление от {decree[1]} № {decree[2]}; {totals[1]} рабочих, {totals[3]} выходных, {len(short)} сокращённых дней')
    return dict(year=year, source=f'https://www.consultant.ru/law/ref/calendar/proizvodstvennye/{year}/', events=merged)


def update(dataset):
    path = ROOT / 'data' / f"{dataset['year']}.json"
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) == dataset:
        print('Данные не изменились; ICS и дата обновления сохранены.')
        return
    years = sorted({int(p.stem) for p in (ROOT / 'data').glob('*.json')} | {dataset['year']})
    span = str(years[0]) if len(years) == 1 else f'{years[0]}–{years[-1]}'
    today = datetime.now(MOSCOW).date()
    stamp = f'{today.day} {MONTHS[today.month - 1]} {today.year} года'
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    readme, count = re.subn(r'\| \*\*\d{4}(?:–\d{4})?\*\* \| \*\*[^\n]+\*\* \|', f'| **{span}** | **{stamp}** |', readme)
    require(count == 1, 'Изменился формат README')
    index = (ROOT / 'index.html').read_text(encoding='utf-8')
    index, count = re.subn(r'за \d{4}(?:–\d{4})? годы', f'за {span} годы', index)
    require(count == 1, 'Изменился формат index.html')
    # Parse and validate everything before touching any repository file.
    outputs = {path: json.dumps(dataset, ensure_ascii=False, indent=2) + '\n', ROOT / 'README.md': readme, ROOT / 'index.html': index}
    previous = {p: p.read_bytes() if p.exists() else None for p in [*outputs, ROOT / 'calendar.ics']}
    try:
        for p, content in outputs.items():
            p.write_text(content, encoding='utf-8')
        subprocess.run([sys.executable, str(ROOT / 'generate.py')], check=True)
    except BaseException:
        for p, content in previous.items():
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(content)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, help='Год; по умолчанию следующий по Москве')
    parser.add_argument('--html', type=Path, help='Проверить сохранённый HTML вместо HTTP-запроса')
    parser.add_argument('--check', action='store_true', help='Только загрузить и проверить, без записи')
    args = parser.parse_args()
    year = args.year if args.year is not None else datetime.now(MOSCOW).year + 1
    require(2000 <= year <= 2100, 'Год должен быть в диапазоне 2000–2100')
    if args.html:
        html = args.html.read_text(encoding='utf-8')
    else:
        url = f'https://www.consultant.ru/law/ref/calendar/proizvodstvennye/{year}/'
        request = Request(url, headers={'User-Agent': 'production-calendar-ru/1.0 (+https://github.com/AlexandrDev/production-calendar-ru)'})
        with urlopen(request, timeout=60) as response:
            require(response.status == 200 and response.geturl().rstrip('/') == url.rstrip('/'), 'Неожиданный ответ или перенаправление источника')
            html = response.read().decode(response.headers.get_content_charset() or 'utf-8')
    dataset = parse_calendar(html, year)
    if not args.check:
        update(dataset)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Ошибка обновления календаря: {error}', file=sys.stderr)
        sys.exit(1)
