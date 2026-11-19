"""Persistência transacional e relógio diário do servidor."""
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import re
import sqlite3

from calendar_core import Calendar, CalendarError
from heavens import default_sky
from presentation import daily_snapshot


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


class Store:
    def __init__(self, database, seed):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn, conn:
            conn.execute(
                'CREATE TABLE IF NOT EXISTS state ('
                'id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL)'
            )
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT body FROM state WHERE id = 1').fetchone()
            if row is None:
                doc = json.loads(Path(seed).read_bytes())
                cal = Calendar(doc)
                cal.set_date(cal.current)
                state = {'version': 2, 'calendar': doc, 'daily': None, 'pending': None,
                         'sky': default_sky()}
                conn.execute('INSERT INTO state VALUES (1, ?)', (json.dumps(state),))
            else:
                state = json.loads(row[0])
                if state['version'] == 1:
                    migrate_v1(state)
                    conn.execute('UPDATE state SET body = ? WHERE id = 1', (json.dumps(state),))
                elif state['version'] != 2:
                    raise CalendarError('A versão deste banco precisa de outra versão do bot.')

    def connect(self):
        return sqlite3.connect(self.database, timeout=5)

    def read(self):
        with closing(self.connect()) as conn:
            return json.loads(conn.execute('SELECT body FROM state WHERE id = 1').fetchone()[0])

    def change(self, callback):
        with closing(self.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            raw = conn.execute('SELECT body FROM state WHERE id = 1').fetchone()[0]
            state = json.loads(raw)
            result = callback(state)
            encoded = json.dumps(state)
            if encoded != raw:
                conn.execute('UPDATE state SET body = ? WHERE id = 1', (encoded,))
            return result

    def calendar_change(self, callback):
        return self.change(lambda state: callback(Calendar(state['calendar'])))


def migrate_v1(state):
    """Aplica só o novo nome padrão e substitui apenas o sol provisório intacto."""
    from calendar_core import key

    cal = Calendar(state['calendar'])
    if not any(key(m['name']) == 'skaryel' for m in cal.months):
        for m in cal.months:
            if key(m['name']) == 'syrveth':
                m['name'] = 'Skaryel'
                break

    placeholders = {'name': 'Sol principal', 'period': 1008, 'shift': 0, 'variation': 0,
                    'sunrise': 6 * cal.clock['minutes'], 'sunset': 18 * cal.clock['minutes']}
    sky = default_sky()
    taken = {key(s['name']) for s in sky['suns']}
    for old in state.pop('suns', []):
        if old == placeholders:
            continue
        name = old['name']
        if key(name) in taken:
            suffix = ' (legado)'
            name = name[:60 - len(suffix)] + suffix
            number = 2
            while key(name) in taken:
                suffix = f' (legado {number})'
                name = old['name'][:60 - len(suffix)] + suffix
                number += 1
        taken.add(key(name))
        sky['suns'].append({**old, 'name': name, 'mode': 'manual', 'reference': False})
    state.update(version=2, sky=sky)


def due_date(now, zone, hhmm):
    here = now.astimezone(ZoneInfo(zone))
    return here.date() - timedelta(days=(here.strftime('%H:%M') < hhmm))


def configure_daily(state, now, channel, hhmm, zone, post_date, post_events):
    if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', hhmm):
        raise CalendarError('O horário real precisa estar em HH:MM, entre 00:00 e 23:59.')
    try:
        cursor = due_date(now, zone, hhmm)
    except (ValueError, ZoneInfoNotFoundError):
        raise CalendarError('Use um fuso IANA válido, como America/Fortaleza.') from None
    state['daily'] = {'channel': channel, 'hhmm': hhmm, 'zone': zone, 'last': cursor.isoformat(),
                      'post_date': post_date, 'post_events': post_events}
    state['pending'] = None


def daily_tick(state, now):
    daily = state['daily']
    if daily is None:
        return None
    due = due_date(now, daily['zone'], daily['hhmm'])
    missed = (due - date.fromisoformat(daily['last'])).days
    if missed > 0:
        cal = Calendar(state['calendar'])
        current = cal.move(missed)
        daily['last'] = due.isoformat()
        notice = daily_snapshot(cal, current, daily['post_date'], daily['post_events'], missed)
        state['pending'] = {'key': due.isoformat(), 'channel': daily['channel'], **notice} if notice else None
    return state['pending']


def finish_post(state, pending):
    if state['pending'] == pending:
        state['pending'] = None

