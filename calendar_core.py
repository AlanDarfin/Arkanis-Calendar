"""Regras civis de Arkanis e conversão para o JSON do Fantasy Calendar."""
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from itertools import accumulate
from html import escape
import re
import unicodedata


class CalendarError(ValueError):
    """Uma regra ou entrada inválida que pode ser explicada ao usuário."""


def key(name):
    return ''.join(c for c in unicodedata.normalize('NFKD', name.casefold().strip()) if not unicodedata.combining(c))


def label(name, limit=80):
    name = ' '.join(name.split())
    if not 1 <= len(name) <= limit:
        raise CalendarError(f'Use um nome de 1 a {limit} caracteres.')
    return name


@dataclass(frozen=True, order=True)
class Date:
    year: int
    month: int
    day: int

    def fields(self):
        return [self.year, self.month, self.day]


@dataclass(frozen=True)
class Moon:
    name: str
    emoji: str
    phase: str
    index: int
    granularity: int
    cycle: Decimal


PHASES = (
    ('🌑', 'Nova'), ('🌒', 'Crescente'), ('🌓', 'Quarto crescente'),
    ('🌔', 'Gibosa crescente'), ('🌕', 'Cheia'), ('🌖', 'Gibosa minguante'),
    ('🌗', 'Quarto minguante'), ('🌘', 'Minguante'),
)


class Calendar:
    def __init__(self, document):
        self.document = document
        self.static = document['static_data']
        self.year = self.static['year_data']
        self.dynamic = document['dynamic_data']
        self.months = self.year['timespans']
        self.week = self.year['global_week']
        self.extras = self.year['leap_days']
        self.seasons = self.static['seasons']
        self.events = document['events']
        self.clock = self.static['clock']
        self._reindex()

    def _reindex(self):
        if not self.months or not self.week or not self.year['overflow']:
            raise CalendarError('Arkanis precisa de meses e de uma semana contínua.')
        if self.static['eras'] or self.static['settings'].get('year_zero_exists'):
            raise CalendarError('Esta versão de Arkanis não usa eras nem ano zero.')
        self.extra_by_month = [[] for _ in self.months]
        self.lengths = []
        for m in self.months:
            if m['type'] != 'month' or int(m['interval']) != 1 or m.get('week') or int(m['length']) < 1:
                raise CalendarError('Arkanis usa meses numerados anuais e uma semana global.')
            self.lengths.append(int(m['length']))
        for extra in self.extras:
            m = extra['timespan']
            if (not 0 <= m < len(self.months) or str(extra['interval']) != '1'
                    or any(extra.get(flag) for flag in ('intercalary', 'adds_week_day', 'not_numbered'))):
                raise CalendarError('Arkanis usa dias especiais numerados, anuais e dentro da semana.')
            self.extra_by_month[m].append(extra)
            self.lengths[m] += 1
        self.starts = list(accumulate(self.lengths, initial=0))
        self.year_length = self.starts[-1]

    @property
    def current(self):
        return Date(self.dynamic['year'], self.dynamic['timespan'], self.dynamic['day'])

    def validate(self, date):
        if date.year < 1:
            raise CalendarError('O calendário começa no ano 1.')
        if not 0 <= date.month < len(self.months):
            raise CalendarError('Esse mês não existe.')
        maximum = self.lengths[date.month]
        if not 1 <= date.day <= maximum:
            raise CalendarError(f'{self.months[date.month]["name"]} tem dias de 1 a {maximum}.')
        return date

    def epoch(self, date):
        self.validate(date)
        return (date.year - 1) * self.year_length + self.starts[date.month] + date.day - 1

    def from_epoch(self, epoch):
        if epoch < 0:
            raise CalendarError('Não dá para voltar além do primeiro dia do ano 1.')
        year, ordinal = divmod(epoch, self.year_length)
        month = bisect_right(self.starts, ordinal) - 1
        return Date(year + 1, month, ordinal - self.starts[month] + 1)

    def move(self, days):
        return self.set_date(self.from_epoch(self.epoch(self.current) + days))

    def set_date(self, date):
        epoch = self.epoch(date)
        self.dynamic.update(year=date.year, timespan=date.month, day=date.day, epoch=epoch)
        return date

    def month_index(self, name):
        name = name.strip()
        if re.fullmatch(r'\d{1,3}', name):
            index = int(name) - 1
            if 0 <= index < len(self.months):
                return index
        for i, m in enumerate(self.months):
            if key(m['name']) == key(name):
                return i
        raise CalendarError('Mês não encontrado. Use o nome ou número mostrado em /calendario ano.')

    def resolve(self, day=None, month=None, year=None):
        old = self.current
        return self.validate(Date(
            old.year if year is None else year, old.month if month is None else self.month_index(month),
            old.day if day is None else day,
        ))

    def date_name(self, date):
        return f'{date.day} de {self.months[date.month]["name"]}, ano {date.year}'

    def weekday_index(self, date):
        return (self.epoch(date) + int(self.year['first_day']) - 1) % len(self.week)

    def weekday(self, date):
        return self.week[self.weekday_index(date)]

    def week_dates(self, date):
        start = self.epoch(date) - self.weekday_index(date)
        return [self.from_epoch(i) for i in range(max(0, start), start + len(self.week))]

    def special_day(self, date):
        offset = date.day - self.months[date.month]['length'] - 1
        return self.extra_by_month[date.month][offset]['name'] if offset >= 0 else None

    def season(self, date):
        data = self.seasons['data']
        if not data:
            return None
        lengths = [Decimal(str(s['transition_length'])) + Decimal(str(s['duration'])) for s in data]
        position = (Decimal(self.epoch(date)) - Decimal(str(self.seasons['global_settings']['season_offset']))) % sum(lengths)
        if position < 0:
            position += sum(lengths)
        for i, (item, length) in enumerate(zip(data, lengths)):
            if position < length:
                return i, item['name'], int(position) + 1
            position -= length
        raise AssertionError('Dia de estação não encontrado')

    def moons(self, date):
        for item in self.static['moons']:
            if item.get('hidden'):
                continue
            period = Decimal(str(item['cycle']))
            gran = int(item['granularity'])
            # Shift desloca a origem da fase. Decimal evita erros perto das fronteiras.
            ratio = (Decimal(self.epoch(date)) - Decimal(str(item['shift']))) % period
            ratio = (ratio + period) % period
            rounding = {'floor': ROUND_FLOOR, 'ceil': ROUND_CEILING, 'round': ROUND_HALF_UP}.get(item.get('cycle_rounding'), ROUND_HALF_UP)
            index = int((ratio / period * gran).to_integral_value(rounding=rounding)) % gran
            if index == 0:
                phase = 0
            elif index == gran // 4:
                phase = 2
            elif index == gran // 2:
                phase = 4
            elif index == (gran * 3) // 4:
                phase = 6
            else:
                phase = 1 if index < gran / 4 else 3 if index < gran / 2 else 5 if index < gran * 3 / 4 else 7
            yield Moon(item['name'], *PHASES[phase], index, gran, period)

    def edit_moon(self, name, new_name=None, cycle=None, shift=None, hidden=None):
        moons = self.static['moons']
        moon = next((item for item in moons if key(name) == key(item['name'])), None)
        if moon is None:
            raise CalendarError('Lua não encontrada.')
        if new_name is not None:
            new_name = label(new_name, 60)
            if any(other is not moon and key(new_name) == key(other['name']) for other in moons):
                raise CalendarError('Já existe uma lua com esse nome.')
            moon['name'] = new_name
        for field, value, low, high in (('cycle', cycle, 1, 100000), ('shift', shift, -100000, 100000)):
            if value is None:
                continue
            number = Decimal(str(value))
            if not number.is_finite() or not low <= number <= high:
                what = 'O ciclo lunar' if field == 'cycle' else 'O deslocamento lunar'
                raise CalendarError(f'{what} precisa ficar entre {low} e {high} dias.')
            moon[field] = format(number.normalize(), 'f')
        if hidden is not None:
            moon['hidden'] = hidden
        return moon

    def clock_time(self, value):
        match = re.fullmatch(r'([0-9]{1,2}):([0-9]{2})', value.strip())
        hours, minutes = self.clock['hours'], self.clock['minutes']
        if not match or int(match[1]) >= hours or int(match[2]) >= minutes:
            raise CalendarError(f'Use HH:MM entre 00:00 e {hours - 1:02d}:{minutes - 1:02d} no relógio de Arkanis.')
        return int(match[1]) * minutes + int(match[2])

    def format_time(self, time):
        hour, minute = divmod(time, self.clock['minutes'])
        return f'{hour:02d}:{minute:02d}'

    def set_clock(self, value):
        self.dynamic['hour'], self.dynamic['minute'] = divmod(self.clock_time(value), self.clock['minutes'])

    def events_on(self, date, private=False):
        for event in self.events:
            settings = event['settings']
            if (private or not (settings.get('hide') or settings.get('hide_full'))) and self.event_matches(event, date):
                yield event

    def event_matches(self, event, date):
        rules = event['data']['conditions']
        parts = rules[::2]
        if not parts or any(op != ['&&'] for op in rules[1::2]):
            return False
        for variable, operator, values in parts:
            if (variable, operator) == ('Date', '0'):
                ok = date.fields() == values
            elif (variable, operator) == ('Season', '0'):
                season = self.season(date)
                ok = season is not None and season[0] == values[0]
            elif (variable, operator) == ('Season', '8'):
                season = self.season(date)
                ok = season is not None and season[2] == values[0]
            else:
                return False
            if not ok:
                return False
        return True

    def add_event(self, name, text, date, hidden):
        self.validate(date)
        name = label(name, 100)
        if len(text) > 1500:
            raise CalendarError('A descrição pode ter até 1.500 caracteres.')
        event = {
            'id': max((e['id'] for e in self.events), default=0) + 1, 'name': name,
            'data': {'has_duration': False, 'duration': 1, 'show_first_last': False,
                     'limited_repeat': False, 'limited_repeat_num': 1,
                     'conditions': [['Date', '0', date.fields()]], 'connected_events': [],
                     'date': date.fields(), 'search_distance': 0, 'overrides': {'moons': []}},
            'description': escape(text).replace('\n', '<br>'), 'event_category_id': -1,
            'settings': {'color': 'Dark-Solid', 'text': 'text', 'hide': hidden, 'print': False, 'hide_full': False},
            'sort_by': len(self.events),
        }
        now = datetime.now(UTC).isoformat(timespec='seconds')
        event.update(created_at=now, updated_at=now)
        self.events.append(event)
        return event

    def get_event(self, event_id):
        for e in self.events:
            if e['id'] == event_id:
                return e
        raise CalendarError('Não encontrei esse ID de evento.')

    def edit_event(self, event_id, title=None, text=None, new_date=None, hidden=None):
        event = self.get_event(event_id)
        if title is not None:
            event['name'] = label(title, 100)
        if text is not None:
            if len(text) > 1500:
                raise CalendarError('A descrição pode ter até 1.500 caracteres.')
            event['description'] = escape(text).replace('\n', '<br>')
        if hidden is not None:
            event['settings']['hide'] = hidden
            event['settings']['hide_full'] = False
        if new_date is not None:
            if not event['data']['date']:
                raise CalendarError('Esse evento segue uma regra de estação e não tem data fixa.')
            self.validate(new_date)
            event['data']['date'] = new_date.fields()
            event['data']['conditions'] = [['Date', '0', new_date.fields()]]
        event['updated_at'] = datetime.now(UTC).isoformat(timespec='seconds')
        return event

    def _anchors(self):
        try:
            return [self.starts.index(s['start']) for s in self.seasons['data']]
        except ValueError as exc:
            raise CalendarError('As estações precisam começar no primeiro dia de um mês para editar a estrutura.') from exc

    def _retime_seasons(self, anchors):
        for i, season in enumerate(self.seasons['data']):
            start = self.starts[anchors[i]]
            end = self.starts[anchors[i + 1]] if i + 1 < len(anchors) else self.year_length
            season.update(start=start, end=end, length=end - start, transition_length=end - start, duration=0)

    def _retime_dates(self, old_date, old_base, remap):
        def convert(fields, subject):
            date = Date(*fields)
            month = remap(date.month)
            day = date.day
            if day > old_base[date.month]:
                day += self.months[month]['length'] - old_base[date.month]
            elif day > self.months[month]['length']:
                raise CalendarError(f'O dia {day} de {self.months[month]["name"]} está em uso ({subject}).')
            return self.validate(Date(date.year, month, day))
        for e in self.events:
            data = e['data']
            if data['date']:
                data['date'] = convert(data['date'], e['name']).fields()
            for rule in data['conditions']:
                if rule[:2] == ['Date', '0']:
                    rule[2] = convert(rule[2], e['name']).fields()
        self.set_date(convert(old_date.fields(), 'data atual'))

    def add_month(self, title, days, before=None):
        title = label(title)
        if not 1 <= days <= 120:
            raise CalendarError('Um mês pode ter de 1 a 120 dias comuns.')
        if title.isdecimal() or any(key(title) == key(m['name']) for m in self.months):
            raise CalendarError('Use um nome de mês único, que não seja só um número.')
        index = len(self.months) if before is None else self.month_index(before)
        remap = lambda m: m + (m >= index)
        anchors = [0 if i == 0 else remap(m) for i, m in enumerate(self._anchors())]
        old_date, old_base = self.current, [m['length'] for m in self.months]
        for day in self.extras:
            day['timespan'] = remap(day['timespan'])
        self.months.insert(index, {'name': title, 'type': 'month', 'length': days, 'interval': 1, 'offset': 0})
        self._reindex()
        self._retime_dates(old_date, old_base, remap)
        self._retime_seasons(anchors)

    def edit_month(self, month, name=None, days=None):
        index = self.month_index(month)
        anchors = self._anchors()
        old_date, old_base = self.current, [m['length'] for m in self.months]
        if name is not None:
            name = label(name)
            if name.isdecimal() or any(i != index and key(name) == key(m['name']) for i, m in enumerate(self.months)):
                raise CalendarError('Use um nome de mês único, que não seja só um número.')
            self.months[index]['name'] = name
        if days is not None:
            if not 1 <= days <= 120:
                raise CalendarError('Um mês pode ter de 1 a 120 dias comuns.')
            self.months[index]['length'] = days
            self._reindex()
            self._retime_dates(old_date, old_base, lambda m: m)
            self._retime_seasons(anchors)

    def add_special_day(self, month, title):
        title = label(title)
        index = self.month_index(month)
        anchors = self._anchors()
        if any(key(title) == key(e['name']) for e in self.extra_by_month[index]):
            raise CalendarError('Já existe um dia especial com esse nome no mês.')
        self.extras.append({'name': title, 'intercalary': False, 'timespan': index, 'adds_week_day': False,
                            'day': 0, 'week_day': '', 'interval': '1', 'offset': 0, 'not_numbered': False})
        self._reindex()
        self._retime_seasons(anchors)
        self.set_date(self.current)
        return self.lengths[index]

    def edit_special_day(self, month, day, title):
        date = self.resolve(day, month)
        if self.special_day(date) is None:
            raise CalendarError('Esse número não corresponde a um dia especial. Para mudar a duração, use /arkanis-admin mes-editar.')
        title = label(title)
        entries = self.extra_by_month[date.month]
        item = entries[day - self.months[date.month]['length'] - 1]
        if any(e is not item and key(e['name']) == key(title) for e in entries):
            raise CalendarError('Já existe um dia especial com esse nome no mês.')
        item['name'] = title

    def write_weekday(self, title, number=None):
        title = label(title, 40)
        index = len(self.week) if number is None else number - 1
        if number is not None and not 0 <= index < len(self.week):
            raise CalendarError(f'A semana tem {len(self.week)} dias. Use o número correspondente.')
        if any(i != index and key(title) == key(name) for i, name in enumerate(self.week)):
            raise CalendarError('Esse dia da semana já existe.')
        if number is None:
            if len(self.week) >= 24:
                raise CalendarError('Esta versão comporta até 24 dias na semana.')
            self.week.append(title)
        else:
            self.week[index] = title
