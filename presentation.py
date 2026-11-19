"""Apresentação de datas sem depender da API do Discord."""
from html import unescape
import re

from calendar_core import Date


def inline(value):
    value = ' '.join(value.split()).replace('@', '@\u200b')
    return re.sub(r'([\\`*_~|<>\[\]])', r'\\\1', value)


def description(value):
    value = re.sub(r'(?i)<br\s*/?>|</p\s*>', '\n', value)
    return '\n'.join(inline(line) for line in unescape(re.sub(r'<[^>]*>', '', value)).splitlines())


def event_row(event):
    private = ' · oculto' if event['settings'].get('hide') or event['settings'].get('hide_full') else ''
    return f'**{inline(event["name"])}** · ID `{event["id"]}`{private}'


def event_lines(events):
    return ['– ' + event_row(e) for e in events]


def sky_moons(cal, date):
    return [f'{m.emoji} **{inline(m.name)}** · {m.phase} · ciclo de {m.cycle.normalize():f} dias'
            for m in cal.moons(date)]


def day_lines(cal, date):
    out = [f'**{inline(cal.weekday(date))}** · dia {cal.epoch(date) % cal.year_length + 1} de {cal.year_length}']
    special = cal.special_day(date)
    if special:
        out.append('◆ ' + inline(special))
    season = cal.season(date)
    if season:
        out.append(f'🍂 {inline(season[1])} · dia {season[2]} da estação')
    if date == cal.current:
        out.append(f'🕰️ {cal.dynamic["hour"]:02d}:{cal.dynamic["minute"]:02d} em Arkanis')
    out.append('')
    out.extend(sky_moons(cal, date))
    out.append('')
    out.append('**Eventos do dia**')
    out.extend(event_lines(cal.events_on(date)) or ['Nenhum evento publicado.'])
    return out


def compact_day(cal, date, show_month=False):
    current = ' ◀ hoje' if date == cal.current else ''
    names = [inline(e['name']) for e in cal.events_on(date)]
    events = (' · ' + ', '.join(names[:3]) + (f' e mais {len(names) - 3}' if len(names) > 3 else '')) if names else ''
    special = cal.special_day(date)
    named = ' ◆ ' + inline(special) if special else ''
    display = inline(cal.date_name(date)) if show_month else f'{date.day:02d}'
    return f'**{display}** · {inline(cal.weekday(date))}{current}{named}{events}'


def week_lines(cal, date):
    return [compact_day(cal, d, show_month=True) for d in cal.week_dates(date)]


def month_lines(cal, date):
    return [compact_day(cal, Date(date.year, date.month, day)) for day in range(1, cal.lengths[date.month] + 1)]


def year_lines(cal):
    out = [f'**{cal.year_length} dias** · {len(cal.months)} meses · semana de {len(cal.week)} dias', '']
    out.extend(f'`{i + 1:02d}` **{inline(m["name"])}** · {cal.lengths[i]} dias' for i, m in enumerate(cal.months))
    out.extend(['', '**Dias da semana em ordem**'])
    out.extend(f'`{i + 1:02d}` {inline(w)}' for i, w in enumerate(cal.week))
    return out


def chunks(lines, limit=3400):
    resulting = []
    page = ''
    for line in lines:
        for index in range(0, max(1, len(line)), limit):
            fragment = line[index:index + limit]
            if len(page) + len(fragment) + int(bool(page)) > limit:
                resulting.append(page)
                page = ''
            page += ('\n' if page else '') + fragment
    if page or not resulting:
        resulting.append(page or 'Nada para mostrar.')
    return resulting


def daily_snapshot(cal, current, post_date, post_events, missed):
    if post_date:
        title = '📜 ' + cal.date_name(current)
        lines = ['**' + inline(cal.weekday(current)) + '**']
        season = cal.season(current)
        special = cal.special_day(current)
        if season:
            lines.append(f'{inline(season[1])} · dia {season[2]} da estação')
        if special:
            lines.append('◆ ' + inline(special))
        lines += sky_moons(cal, current)
        if missed > 1:
            lines.append(f'Avanço de {missed} dias para recuperar os dias em que o bot esteve offline.')
    else:
        title, lines = '📅 Eventos de ' + cal.date_name(current), []
    ids = [e['id'] for e in cal.events_on(current)] if post_events else []
    if not post_date and not ids:
        return None
    return {'title': title, 'lines': lines, 'events': ids, 'post_date': post_date}


def daily_notice_lines(calendar, notice):
    # Recheck the latest visibility. A queued event may have been hidden before delivery.
    public = {e['id']: e for e in calendar.events if not (e['settings'].get('hide') or e['settings'].get('hide_full'))}
    events = [public[i] for i in notice['events'] if i in public]
    if not events and not notice['post_date']:
        return None
    lines = list(notice['lines'])
    if events:
        lines.extend(['', '**Eventos do dia**', *event_lines(events[:10])])
        if len(events) > 10:
            lines.append('Veja todos em /eventos hoje.')
    return lines
