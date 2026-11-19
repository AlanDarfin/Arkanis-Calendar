from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, skipUnless
from importlib.util import find_spec
from collections import Counter
from contextlib import closing
import json

from calendar_core import Calendar, CalendarError, Date
from heavens import default_sky, orbital_period, remove_sun, set_latitude, solar_positions, write_sun
from presentation import chunks, daily_notice_lines, description, month_lines, week_lines
from storage import Store, configure_daily, daily_tick, due_date, finish_post, json_bytes

BASE = Path(__file__).resolve().parents[1] / 'data/arkanis.json'


def source():
    return json.loads(BASE.read_bytes())


def instant(day, hours=3, minutes=0):
    return datetime(2026, 11, day, hours, minutes, tzinfo=UTC)


class DateTests(TestCase):
    def setUp(self):
        self.cal = Calendar(source())

    def test_initial(self):
        self.assertEqual(self.cal.current, Date(1, 0, 1))
        self.assertEqual(self.cal.epoch(self.cal.current), 0)
        self.assertEqual((self.cal.dynamic['hour'], self.cal.dynamic['minute']), (0, 0))
        self.assertEqual(self.cal.year_length, 1008)
        self.assertEqual(len(self.cal.months), 26)
        self.assertEqual(len(self.cal.week), 12)
        self.assertEqual(self.cal.week[-1], 'Aplurge')
        self.assertEqual(self.cal.months[18]['name'], 'Skaryel')
        self.assertEqual(self.cal.lengths[18], 42)

    def test_epoch_roundtrip_across_entire_years(self):
        for epoch in range(3024):
            with self.subTest(epoch=epoch):
                self.assertEqual(self.cal.epoch(self.cal.from_epoch(epoch)), epoch)

    def test_new_year(self):
        self.cal.set_date(Date(1, 25, 45))
        self.assertEqual(self.cal.move(1), Date(2, 0, 1))
        self.assertEqual(self.cal.move(-1), Date(1, 25, 45))
        self.assertEqual(self.cal.weekday(Date(1, 0, 1)), self.cal.weekday(Date(2, 0, 1)))
        self.cal.set_date(Date(1, 0, 1))
        with self.assertRaises(CalendarError):
            self.cal.move(-1)

    def test_week_includes_adjacent_month(self):
        dates = self.cal.week_dates(Date(1, 0, 40))
        self.assertEqual(len(dates), 12)
        self.assertEqual(dates[0], Date(1, 0, 37))
        self.assertEqual(dates[-1], Date(1, 1, 8))
        self.assertEqual(len(week_lines(self.cal, Date(1, 0, 40))), 12)

    def test_special_days(self):
        self.assertIsNone(self.cal.special_day(Date(1, 0, 36)))
        self.assertEqual(self.cal.special_day(Date(1, 0, 37)), 'Krusada Druor')
        self.assertEqual(self.cal.special_day(Date(2, 0, 40)), 'Khorq Dryart')
        self.assertEqual(len(month_lines(self.cal, Date(1, 0, 1))), 40)
        with self.assertRaises(CalendarError):
            self.cal.validate(Date(1, 0, 41))

    def test_seasons_and_recurrence(self):
        names = ['Velanthir', 'Solanarê', 'Drosmy', 'Kealyth']
        for year in (1, 2):
            for part, ordinal in enumerate((0, 252, 504, 756)):
                epoch = (year - 1) * self.cal.year_length + ordinal
                start = self.cal.from_epoch(epoch)
                self.assertEqual(self.cal.season(start), (part, names[part], 1))
                self.assertIn(names[part], [e['name'] for e in self.cal.events_on(start)])
                self.assertNotIn(names[part], [e['name'] for e in self.cal.events_on(self.cal.from_epoch(epoch + 1))])

    def test_moons_recur_at_144_days(self):
        d = self.cal.from_epoch(50)
        self.assertEqual(list(self.cal.moons(d)), list(self.cal.moons(self.cal.from_epoch(194))))
        full = list(self.cal.moons(self.cal.from_epoch(36)))
        self.assertEqual(full[0].phase, 'Cheia')
        self.assertEqual(full[0].cycle, 48)

    def test_clock(self):
        self.assertEqual(self.cal.clock_time('31:63'), 2047)
        self.cal.set_clock('17:61')
        self.assertEqual((self.cal.dynamic['hour'], self.cal.dynamic['minute']), (17, 61))
        for bad in ('32:00', '06:64', '6:9', 'x'):
            with self.subTest(bad=bad), self.assertRaises(CalendarError):
                self.cal.clock_time(bad)

    def test_events_with_hidden_edit_and_linkage(self):
        d = Date(1, 0, 4)
        event = self.cal.add_event('Limiar', 'A < B\nDuas linhas', d, True)
        self.assertEqual(description(event['description']), 'A \\< B\nDuas linhas')
        self.assertNotIn(event, list(self.cal.events_on(d)))
        self.assertIn(event, list(self.cal.events_on(d, private=True)))
        target = Date(1, 1, 4)
        self.cal.edit_event(event['id'], new_date=target, hidden=False)
        self.assertNotIn(event, list(self.cal.events_on(d)))
        self.assertIn(event, list(self.cal.events_on(target)))
        data = event['data']
        self.assertEqual(data['conditions'][0][2], data['date'])
        self.assertEqual(data['date'], [1, 1, 4])

    def test_month_insert_rebases_indexes(self):
        self.cal.add_month('Abertura', 10, 'Arkanis')
        self.assertEqual(self.cal.year_length, 1018)
        self.assertEqual(self.cal.current, Date(1, 1, 1))
        self.assertEqual(self.cal.dynamic['epoch'], 10)
        self.assertEqual(self.cal.extras[0]['timespan'], 1)
        date = Date(1, 1, 1)
        self.assertIn('Arkas, A Criação', [e['name'] for e in self.cal.events_on(date)])
        self.assertEqual([e['start'] for e in self.cal.seasons['data']], [0, 262, 514, 766])

    def test_month_length_rebases_named_days_and_current_date(self):
        orig = Date(1, 0, 38)
        self.cal.set_date(orig)
        self.cal.add_event('Nome antigo', '', orig, False)
        self.cal.edit_month('Arkanis', days=40)
        self.assertEqual(self.cal.current, Date(1, 0, 42))
        self.assertEqual(self.cal.special_day(self.cal.current), 'Vryant Kor')
        self.assertEqual(self.cal.events[-1]['data']['date'], [1, 0, 42])
        self.assertEqual(self.cal.seasons['data'][1]['start'], 256)

    def test_special_day_edit_and_week_edit(self):
        self.assertEqual(self.cal.add_special_day('Arkanis', 'Nuvem'), 41)
        self.cal.edit_special_day('Arkanis', 41, 'Aurora')
        self.assertEqual(self.cal.special_day(Date(1, 0, 41)), 'Aurora')
        self.assertEqual(self.cal.year_length, 1009)
        self.cal.write_weekday('Marginalia')
        self.cal.write_weekday('Glossário', 13)
        self.assertEqual(self.cal.week[-1], 'Glossário')

    def test_json_schema_remains_fantasy_calendar(self):
        event = self.cal.add_event('Editado', '', self.cal.current, False)
        export = json.loads(json_bytes(self.cal.document))
        self.assertEqual(export.keys(), source().keys())
        self.assertEqual(export['static_data'].keys(), source()['static_data'].keys())
        self.assertNotIn('suns', export['static_data'])
        self.assertEqual(export['events'][-1], event)
        self.assertEqual(Calendar(export).current, self.cal.current)

    def test_description_pages_keep_order_and_limits(self):
        self.assertEqual(chunks(['abcde', 'f'], 5), ['abcde', 'f'])
        self.assertEqual(chunks(['abcdefgh'], 5), ['abcde', 'fgh'])


class StorageTests(TestCase):
    def setUp(self):
        workspace = self.enterContext(TemporaryDirectory())
        self.db = Path(workspace) / 'test.sqlite3'
        self.store = Store(self.db, BASE)

    def test_existing_calendar_is_not_reset_by_new_seed(self):
        def set_saved(cal):
            cal.set_date(Date(2, 18, 7))
            cal.set_clock('07:32')
        self.store.calendar_change(set_saved)
        cal = Calendar(Store(self.db, BASE).read()['calendar'])
        self.assertEqual(cal.current, Date(2, 18, 7))
        self.assertEqual((cal.dynamic['hour'], cal.dynamic['minute']), (7, 32))

    def test_failed_edit_rolls_back(self):
        self.store.calendar_change(lambda cal: cal.add_event('Fim do mês', '', Date(1, 10, 54), False))
        before = self.store.read()
        with self.assertRaises(CalendarError):
            self.store.calendar_change(lambda cal: cal.edit_month('Khaos', 'Renomeado', 53))
        self.assertEqual(before, self.store.read())

    def test_config_validation_and_real_time_zone(self):
        self.assertEqual(due_date(instant(19, 2, 59), 'America/Fortaleza', '00:00').day, 18)
        self.assertEqual(due_date(instant(19), 'America/Fortaleza', '00:00').day, 19)
        for time, zone in [('31:64', 'America/Fortaleza'), ('07:00', 'etc/invalido')]:
            with self.subTest(time=time, zone=zone), self.assertRaises(CalendarError):
                self.store.change(lambda s: configure_daily(s, instant(19), 3, time, zone, True, True))

    def test_daily_notice_persists_until_sent(self):
        self.store.change(lambda s: configure_daily(s, instant(19), 55, '00:00', 'America/Fortaleza', True, True))
        self.store.calendar_change(lambda cal: cal.add_event('Evento público', '', Date(1, 0, 2), False))
        self.store.calendar_change(lambda cal: cal.add_event('Segredo', '', Date(1, 0, 2), True))
        self.assertIsNone(self.store.change(lambda s: daily_tick(s, instant(19))))
        pending = self.store.change(lambda s: daily_tick(s, instant(20)))
        self.assertEqual(pending['channel'], 55)
        cal = Calendar(self.store.read()['calendar'])
        lines = daily_notice_lines(cal, pending)
        self.assertIn('Evento público', '\n'.join(lines))
        self.assertNotIn('Segredo', '\n'.join(lines))
        cal.edit_event(cal.events[-2]['id'], hidden=True)
        self.assertNotIn('Evento público', '\n'.join(daily_notice_lines(cal, pending)))
        restart = Store(self.db, BASE)
        self.assertEqual(restart.change(lambda s: daily_tick(s, instant(20))), pending)
        self.assertEqual(Calendar(restart.read()['calendar']).current, Date(1, 0, 2))
        restart.change(lambda s: finish_post(s, pending))
        self.assertIsNone(restart.change(lambda s: daily_tick(s, instant(20))))

    def test_daily_catchup_posts_only_latest_day(self):
        self.store.change(lambda s: configure_daily(s, instant(19), 55, '00:00', 'America/Fortaleza', True, True))
        pending = self.store.change(lambda s: daily_tick(s, instant(23)))
        self.assertIn('Avanço de 4 dias', '\n'.join(pending['lines']))
        self.assertEqual(Calendar(self.store.read()['calendar']).current, Date(1, 0, 5))
        self.assertEqual(pending['key'], '2026-11-23')

    def test_event_only_is_silent_without_events(self):
        self.store.calendar_change(lambda cal: cal.set_date(Date(1, 0, 10)))
        self.store.change(lambda s: configure_daily(s, instant(19), 55, '00:00', 'America/Fortaleza', False, True))
        self.assertIsNone(self.store.change(lambda s: daily_tick(s, instant(20))))
        self.assertEqual(Calendar(self.store.read()['calendar']).current, Date(1, 0, 11))


class MigrationTests(TestCase):
    def test_existing_database_migrates_without_losing_date_or_edits(self):
        folder = self.enterContext(TemporaryDirectory())
        database = Path(folder) / 'old.sqlite3'
        store = Store(database, BASE)
        cal = Calendar(source())
        cal.months[18]['name'] = 'Syrveth'
        cal.set_date(Date(3, 18, 9))
        cal.add_event('Salvo', 'Não deve sumir', cal.current, False)
        original = {'version': 1, 'calendar': cal.document, 'daily': None, 'pending': None,
                    'suns': [
                       {'name': 'Sol principal', 'period': 1008, 'shift': 0, 'variation': 0,
                        'sunrise': 384, 'sunset': 1152},
                       {'name': 'Sol antigo', 'period': 143, 'shift': 3, 'variation': 0,
                        'sunrise': 384, 'sunset': 1088},
                    ]}
        configure_daily(original, instant(19), 9, '07:00', 'America/Fortaleza', True, False)
        with closing(store.connect()) as connection, connection:
            connection.execute('UPDATE state SET body = ? WHERE id=1', (json.dumps(original),))
        upgraded = Store(database, BASE).read()
        self.assertEqual(upgraded['version'], 2)
        patched = Calendar(upgraded['calendar'])
        self.assertEqual(patched.current, Date(3, 18, 9))
        self.assertEqual(patched.months[18]['name'], 'Skaryel')
        self.assertEqual(patched.events[-1]['name'], 'Salvo')
        self.assertEqual(upgraded['daily'], original['daily'])
        self.assertEqual([s['name'] for s in upgraded['sky']['suns']], ['Innart', 'Dinart', 'Reart', 'Sol antigo'])
        self.assertEqual(upgraded['sky']['suns'][-1]['mode'], 'manual')
        self.assertEqual(Store(database, BASE).read(), upgraded)


class SunTests(TestCase):
    def setUp(self):
        self.cal = Calendar(source())
        self.state = {'sky': default_sky()}

    def test_kepler_period_and_size_order(self):
        sky = self.state['sky']
        innart, dinart, reart = sky['suns']
        for sun in sky['suns']:
            with self.subTest(sun=sun['name']):
                self.assertAlmostEqual((orbital_period(sky, sun) / 1008)**2, sun['orbit']**3)
        self.assertLess(innart['size'], dinart['size'])
        self.assertLess(dinart['size'], reart['size'])

    def test_first_year_has_no_dark_day_and_varied_combinations(self):
        profiles, overlaps = Counter(), Counter()
        for e in range(self.cal.year_length):
            d = self.cal.from_epoch(e)
            noon = self.cal.clock_time('16:00')
            result = list(solar_positions(self.cal, self.state['sky'], d, noon))
            visible = tuple(sun.name for sun in result if sun.visible_today)
            self.assertIn('Reart', visible)
            self.assertTrue(result[-1].visible_now)
            profiles[visible] += 1
            overlaps[sum(sun.visible_now for sun in result)] += 1
        self.assertEqual(profiles[('Reart',)], 49)
        self.assertEqual(set(overlaps), {1, 2, 3})
        self.assertGreater(profiles[('Innart', 'Reart')], 0)
        self.assertGreater(profiles[('Dinart', 'Reart')], 0)

    def test_reference_above_horizon_at_noon_after_valid_edits(self):
        write_sun(self.state, 'Reart', name='Reart', tilt=35)
        set_latitude(self.state, 54)
        for e in range(0, self.cal.year_length * 2, 13):
            d = self.cal.from_epoch(e)
            noon = self.cal.clock_time('16:00')
            result = list(solar_positions(self.cal, self.state['sky'], d, noon))
            self.assertTrue(result[-1].visible_now)
            self.assertGreater(result[-1].altitude, 0)
        with self.assertRaises(CalendarError):
            set_latitude(self.state, 55)
        with self.assertRaises(CalendarError):
            write_sun(self.state, 'Reart', tilt=36)
        with self.assertRaises(CalendarError):
            remove_sun(self.state, 'Reart')

    def test_suns_with_and_without_sunrise_times(self):
        sky = self.state['sky']
        targets = set()
        for e in range(self.cal.year_length):
            d = self.cal.from_epoch(e)
            for sun in solar_positions(self.cal, sky, d, 1024):
                targets.add(sun.horizon)
                if sun.horizon == 'normal':
                    self.assertIsNotNone(sun.sunrise)
                    self.assertIsNotNone(sun.sunset)
                else:
                    self.assertIsNone(sun.sunrise)
                    self.assertIsNone(sun.sunset)
        self.assertEqual(targets, {'normal', 'never', 'always'})

    def test_add_edit_and_remove_sun(self):
        write_sun(self.state, name='Âmbar', orbit=.5, tilt=-20, phase=-20, size=.4)
        sun = write_sun(self.state, 'ambar', name='Âmbar II', tilt=25)
        self.assertEqual(sun['phase'], 340)
        self.assertEqual(sun['tilt'], 25)
        self.assertEqual(sun['orbit'], .5)
        with self.assertRaises(CalendarError):
            write_sun(self.state, 'Âmbar II', orbit=float('inf'))
        remove_sun(self.state, 'âmbar ii')
        self.assertEqual(len(self.state['sky']['suns']), 3)

    def test_moon_edits_stay_in_fantasy_calendar_schema(self):
        self.cal.edit_moon('Menrika', 'Menrika Nova', 25.5, -6.25, True)
        moon = self.cal.static['moons'][2]
        self.assertEqual(moon['cycle'], '25.5')
        self.assertEqual(moon['shift'], '-6.25')
        self.assertEqual(len(list(self.cal.moons(self.cal.current))), 2)
        self.assertNotIn('sky', self.cal.document)


@skipUnless(find_spec('discord'), 'discord.py não instalado neste ambiente')
class DiscordAdapterTests(IsolatedAsyncioTestCase):
    async def test_guild_scoped_schema_and_admin_gate(self):
        import discord
        from bot import ArkanisClient, is_admin
        folder = self.enterContext(TemporaryDirectory())
        bot = ArkanisClient(123, Store(Path(folder) / 'test.sqlite3', BASE))
        groups = bot.tree.get_commands(guild=discord.Object(id=123))
        self.assertEqual({c.name for c in groups}, {'calendario', 'eventos', 'ceu', 'arkanis-admin'})
        self.assertEqual(bot.tree.get_commands(), [])
        admin = next(c for c in groups if c.name == 'arkanis-admin')
        self.assertTrue(admin.default_permissions.administrator)
        self.assertLessEqual(len(admin.commands), 25)
        self.assertTrue(is_admin(SimpleNamespace(guild=object(), permissions=discord.Permissions(administrator=True))))
        self.assertFalse(is_admin(SimpleNamespace(guild=object(), permissions=discord.Permissions.none())))
        await bot.close()

    async def test_settings_view_sections_and_solar_modal(self):
        import discord
        from admin_ui import SettingsView, admin_allowed

        folder = self.enterContext(TemporaryDirectory())
        store = Store(Path(folder) / 'test.sqlite3', BASE)
        bot = SimpleNamespace(store=store, guild_id=123)
        view = SettingsView(bot, 456)
        self.assertEqual(len(view.children), 6)
        self.assertIn('Innart', view.embed().description)
        self.assertIn('Reart', view.embed().description)
        for section in ('cal', 'diario', 'eventos', 'ceu'):
            view.section = section
            view.rebuild()
            self.assertLessEqual(sum(len(row['components']) for row in view.to_components()), 25)

        class Response:
            async def send_modal(self, modal):
                self.modal = modal

        interaction = SimpleNamespace(user=SimpleNamespace(id=456), guild_id=123,
                                      permissions=discord.Permissions(administrator=True),
                                      response=Response())
        self.assertTrue(admin_allowed(interaction, 456, 123))
        self.assertFalse(admin_allowed(interaction, 999, 123))
        self.assertFalse(admin_allowed(interaction, 456, 999))
        await view.new_sun(interaction)
        self.assertEqual(len(interaction.response.modal.children), 5)
        view.target = 'lua/Verika'
        await view.edit_body(interaction)
        self.assertEqual(len(interaction.response.modal.children), 4)
        view.stop()
