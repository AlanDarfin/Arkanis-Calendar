"""Painel privado de configuração, com seletores e formulários do Discord."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
import logging

import discord

from calendar_core import Calendar, CalendarError, Date, key
from heavens import find_sun, orbital_period, remove_sun, set_latitude, write_sun
from presentation import inline
from storage import configure_daily

LOG = logging.getLogger('arkanis.admin')
COLOR = discord.Color.from_rgb(138, 99, 180)
SECTIONS = {'ceu': 'Céu', 'cal': 'Calendário', 'diario': 'Rotina diária', 'eventos': 'Eventos'}


def admin_allowed(i, owner, guild):
    return i.user.id == owner and i.guild_id == guild and i.permissions.administrator


async def reply(i, text):
    if i.response.is_done():
        await i.followup.send(text, ephemeral=True)
    else:
        await i.response.send_message(text, ephemeral=True)


def integer(text, title, minimum=1, maximum=999999, default=None):
    if not text and default is not None:
        return default
    try:
        value = int(text)
    except ValueError:
        raise CalendarError(f'{title} precisa ser um número inteiro.') from None
    if not minimum <= value <= maximum:
        raise CalendarError(f'{title} precisa ficar entre {minimum} e {maximum}.')
    return value


def number(text, title):
    try:
        value = float(text.replace(',', '.'))
    except ValueError:
        raise CalendarError(f'{title} precisa ser um número.') from None
    if not isfinite(value):
        raise CalendarError(f'{title} precisa ser um número finito.')
    return value


def yesno(text, name):
    value = key(text)
    if value in ('s', 'sim'):
        return True
    if value in ('n', 'nao'):
        return False
    raise CalendarError(f'Em {name}, escreva sim ou não.')


def event_date(c, text):
    try:
        year, date = text.split('/', 1)
        month, day = date.rsplit('/', 1)
        year, month, day = year.strip(), month.strip(), day.strip()
    except ValueError:
        raise CalendarError('Use ano/mês/dia. Exemplo · 1/Skaryel/12.') from None
    return c.resolve(integer(day, 'Dia'), month, integer(year, 'Ano'))


@dataclass(frozen=True)
class Field:
    label: str
    maxlength: int = 80
    default: str = ''
    optional: bool = False
    hint: str | None = None


class SettingsModal(discord.ui.Modal):
    def __init__(self, view, title, fields, callback):
        super().__init__(title=title, timeout=300)
        self.settings = view
        self.apply = callback
        self.inputs = []
        for spec in fields:
            box = discord.ui.TextInput(
                label=spec.label, max_length=spec.maxlength, default=spec.default or None,
                required=not spec.optional, placeholder=spec.hint,
                style=discord.TextStyle.paragraph if spec.maxlength > 120 else discord.TextStyle.short,
            )
            self.inputs.append(box)
            self.add_item(box)

    async def on_submit(self, i: discord.Interaction):
        if not await self.settings.guard(i):
            return
        result = await self.apply(i, *(x.value.strip() for x in self.inputs))
        if i.message:
            await self.settings.refresh(i, result)
        else:
            await reply(i, result)

    async def on_error(self, i: discord.Interaction, error: Exception):
        if isinstance(error, CalendarError):
            await reply(i, str(error))
        else:
            LOG.error('Falha no formulário de configuração.', exc_info=(type(error), error, error.__traceback__))
            await reply(i, 'Não consegui salvar. Abra o painel e tente novamente.')


class SettingsView(discord.ui.View):
    def __init__(self, bot, owner):
        super().__init__(timeout=900)
        self.bot = bot
        self.store = bot.store
        self.owner = owner
        self.section = 'ceu'
        self.target = None
        self.channel = None
        self.rebuild()

    async def guard(self, i):
        if admin_allowed(i, self.owner, self.bot.guild_id):
            return True
        await reply(i, 'O painel é privado e só pode ser usado pelo ADM que o abriu.')
        return False

    async def interaction_check(self, i):
        return await self.guard(i)

    async def on_error(self, i, error, item):
        if isinstance(error, CalendarError):
            await reply(i, str(error))
        else:
            LOG.error('Falha no painel de configuração.', exc_info=(type(error), error, error.__traceback__))
            await reply(i, 'Não consegui concluir essa ação. Abra o painel e tente novamente.')

    def select(self, row, placeholder, options, callback):
        item = discord.ui.Select(placeholder=placeholder, options=options, row=row)
        async def run(i):
            await callback(i, item.values[0])
        item.callback = run
        self.add_item(item)

    def button(self, row, text, callback, style=discord.ButtonStyle.secondary, disabled=False):
        item = discord.ui.Button(label=text, style=style, row=row, disabled=disabled)
        item.callback = callback
        self.add_item(item)

    def rebuild(self):
        self.clear_items()
        self.select(0, 'Escolha uma seção', [
            discord.SelectOption(label=label, value=k, default=k == self.section) for k, label in SECTIONS.items()
        ], self.switch)
        self.state = state = self.store.read()
        if self.section == 'ceu':
            choices = []
            for s in state['sky']['suns']:
                value = 'sol/' + s['name']
                choices.append(discord.SelectOption(
                    label='☀ ' + s['name'], value=value, default=value == self.target,
                    description='Sol de referência' if s.get('reference') else 'Sol',
                ))
            for m in state['calendar']['static_data']['moons']:
                value = 'lua/' + m['name']
                choices.append(discord.SelectOption(label='☾ ' + m['name'], value=value, default=value == self.target))
            if self.target not in {option.value for option in choices}:
                self.target = None
            self.select(1, 'Escolha um sol ou lua', choices, self.pick_body)
            self.button(2, 'Editar selecionado', self.edit_body, disabled=self.target is None)
            self.button(2, 'Adicionar sol', self.new_sun, discord.ButtonStyle.primary)
            self.button(2, 'Latitude', self.latitude)
            removable = self.target is not None and self.target.startswith('sol/') and not find_sun(
                state['sky'], self.target.split('/', 1)[1]
            )[1].get('reference')
            self.button(2, 'Remover sol', self.delete_sun, discord.ButtonStyle.danger, disabled=not removable)
        elif self.section == 'cal':
            self.select(1, 'Escolha o que alterar', [
                discord.SelectOption(label=title, value=action) for action, title in (
                    ('new-month', 'Adicionar mês'), ('edit-month', 'Editar mês'),
                    ('new-day', 'Adicionar dia especial'), ('edit-day', 'Editar dia especial'),
                    ('new-weekday', 'Adicionar dia da semana'), ('edit-weekday', 'Editar dia da semana'),
                    ('set-date', 'Definir data e hora do mundo'), ('move-date', 'Avançar ou voltar dias'),
                )
            ], self.calendar_form)
        elif self.section == 'diario':
            item = discord.ui.ChannelSelect(placeholder='Escolha o canal dos avisos', row=1,
                                            channel_types=[discord.ChannelType.text, discord.ChannelType.news])
            async def choose(i):
                self.channel = item.values[0].id
                await self.refresh(i)
            item.callback = choose
            self.add_item(item)
            self.button(2, 'Configurar e ativar', self.daily_form, discord.ButtonStyle.primary,
                        disabled=not (self.channel or state['daily']))
            self.button(2, 'Pausar', self.pause_form, disabled=not state['daily'])
        elif self.section == 'eventos':
            self.select(1, 'Escolha uma ação', [
                discord.SelectOption(label=title, value=action) for action, title in (
                    ('new', 'Criar evento'), ('edit', 'Editar evento'), ('remove', 'Remover evento')
                )
            ], self.event_form)
            self.button(2, 'Mostrar IDs', self.event_ids)

    def embed(self, note=''):
        state = self.state
        cal = Calendar(state['calendar'])
        title = f'Arkanis · Configurações · {SECTIONS[self.section]}'
        if self.section == 'ceu':
            sky = state['sky']
            lines = ['**Latitude** · ' + f'{sky["latitude"]:g}°', '**Sóis**']
            for s in sky['suns']:
                if s['mode'] == 'orbit':
                    days = f'{orbital_period(sky, s):.2f}'.replace('.', ',')
                    detail = f'ciclo de {days} dias'
                else:
                    detail = 'horários manuais (legado)'
                lines.append(f'☀️ **{inline(s["name"])}** · {detail}' + (' · referência' if s.get('reference') else ''))
            lines.append('**Luas**')
            lines.extend(f'☾ **{inline(m["name"])}** · {m["cycle"]} dias' +
                         (' · oculta' if m.get('hidden') else '') for m in cal.static['moons'])
            if self.target:
                body, name = self.target.split('/', 1)
                lines.extend(['', 'Selecionado · **' + inline(name) + '**'])
                if body == 'sol':
                    sun = find_sun(sky, name)[1]
                    if sun['mode'] == 'orbit':
                        lines.append(f'Semieixo {sun["orbit"]:g} · inclinação {sun["tilt"]:g}° '
                                     f'· fase {sun["phase"]:g}° · tamanho {sun["size"]:g}')
                else:
                    moon = next(m for m in cal.static['moons'] if m['name'] == name)
                    lines.append(f'Deslocamento · {moon["shift"]} dias')
        elif self.section == 'cal':
            lines = [f'**{inline(cal.date_name(cal.current))}**',
                     f'{len(cal.months)} meses · {cal.year_length} dias por ano · {len(cal.week)} dias por semana',
                     '', 'Dias especiais entram no fim do mês. Use **Editar mês** para mudar a quantidade de dias comuns.']
        elif self.section == 'diario':
            d = state['daily']
            lines = [f'**Ativo** · {d["hhmm"]} · {inline(d["zone"])}' if d else '**Pausado**',
                     'Um dia de Arkanis por dia real.']
            channel = self.channel or (d and d['channel'])
            if channel:
                lines.append(f'Canal selecionado · <#{channel}>')
            if d:
                lines.append('Data diária · ' + ('sim' if d['post_date'] else 'não'))
                lines.append('Avisar eventos · ' + ('sim' if d['post_events'] else 'não'))
        else:
            hidden = sum(bool(e['settings'].get('hide') or e['settings'].get('hide_full')) for e in cal.events)
            lines = [f'**{len(cal.events)} eventos** · {hidden} ocultos',
                     'Use **Mostrar IDs** para localizar um evento antes de editar ou remover.']
        if note:
            lines.extend(['', '✓ ' + inline(note)])
        return discord.Embed(title=title, description='\n'.join(lines), color=COLOR)

    async def refresh(self, i, note=''):
        self.rebuild()
        await i.response.edit_message(embed=self.embed(note), view=self)

    async def switch(self, i, section):
        self.section = section
        await self.refresh(i)

    async def pick_body(self, i, value):
        self.target = value
        await self.refresh(i)

    async def form(self, i, title, fields, callback):
        await i.response.send_modal(SettingsModal(self, title, fields, callback))

    async def sun_form(self, i, original=None):
        old_name = original['name'] if original else None
        defaults = original or {'name': '', 'orbit': 0.8, 'tilt': 35.0, 'phase': 0.0, 'size': 0.65}
        async def save(i, name, *values):
            params = {}
            if values:
                params = dict(zip(('orbit', 'tilt', 'phase', 'size'), (
                    number(x, field) for x, field in zip(values, ('Órbita', 'Inclinação', 'Fase', 'Tamanho'))
                )))
            item = self.store.change(lambda s: write_sun(s, old_name, name=name, **params))
            self.target = 'sol/' + item['name']
            return f'{item["name"]} salvo.'
        fields = [Field('Nome', 60, defaults['name'])]
        if defaults.get('mode') != 'manual':
            fields.extend(Field(title, 20, f'{defaults[field]:g}') for title, field in (
                ('Semieixo relativo', 'orbit'), ('Inclinação em graus', 'tilt'),
                ('Fase inicial em graus', 'phase'), ('Tamanho relativo', 'size'),
            ))
        await self.form(i, 'Editar sol' if original else 'Adicionar sol', fields, save)

    async def new_sun(self, i):
        await self.sun_form(i)

    async def edit_body(self, i):
        if not self.target:
            raise CalendarError('Escolha um sol ou uma lua primeiro.')
        kind, name = self.target.split('/', 1)
        if kind == 'sol':
            sun = find_sun(self.store.read()['sky'], name)[1]
            await self.sun_form(i, sun)
            return
        cal = Calendar(self.store.read()['calendar'])
        moon = next((m for m in cal.static['moons'] if m['name'] == name), None)
        if moon is None:
            raise CalendarError('Essa lua mudou. Selecione a seção Céu novamente.')
        async def save(i, new, cycle, shift, hide):
            result = self.store.calendar_change(lambda c: c.edit_moon(
                name, new, number(cycle, 'Ciclo'), number(shift, 'Deslocamento'), yesno(hide, 'Ocultar'),
            ))
            self.target = 'lua/' + result['name']
            return f'{result["name"]} salva.'
        await self.form(i, 'Editar lua', [
            Field('Nome', 60, name), Field('Ciclo em dias', 20, moon['cycle']),
            Field('Deslocamento em dias', 20, moon['shift']),
            Field('Ocultar (sim ou não)', 3, 'sim' if moon.get('hidden') else 'não'),
        ], save)

    async def latitude(self, i):
        old = self.store.read()['sky']['latitude']
        async def save(i, text):
            self.store.change(lambda s: set_latitude(s, number(text, 'Latitude')))
            return 'Latitude salva.'
        await self.form(i, 'Latitude da observação', [Field('Latitude em graus', 20, f'{old:g}')], save)

    async def delete_sun(self, i):
        name = self.target.split('/', 1)[1]
        async def apply(i, confirm):
            if not yesno(confirm, 'Confirmar'):
                raise CalendarError('Remoção cancelada.')
            self.store.change(lambda s: remove_sun(s, name))
            self.target = None
            return f'{name} removido.'
        await self.form(i, 'Remover sol', [Field('Confirmar (sim ou não)', 3, hint=name)], apply)

    async def calendar_form(self, i, action):
        month = Field('Mês (nome ou número)')
        day = Field('Número do dia', 6)
        name = Field('Nome')
        if action == 'new-month':
            title = 'Adicionar mês'
            fields = [name, Field('Dias comuns', 3), Field('Inserir antes de qual mês', optional=True)]
            operation = lambda c, n, d, bef: c.add_month(n, integer(d, 'Dias', maximum=120), bef or None)
        elif action == 'edit-month':
            title = 'Editar mês'
            fields = [month, Field('Novo nome', optional=True), Field('Novo total de dias comuns', 3, optional=True)]
            def operation(c, m, n, d):
                if not n and not d:
                    raise CalendarError('Informe o nome novo ou a quantidade de dias.')
                c.edit_month(m, n or None, integer(d, 'Dias', maximum=120) if d else None)
        elif action == 'new-day':
            title, fields = 'Adicionar dia especial', [month, name]
            operation = lambda c, m, n: c.add_special_day(m, n)
        elif action == 'edit-day':
            title, fields = 'Editar dia especial', [month, day, name]
            operation = lambda c, m, d, n: c.edit_special_day(m, integer(d, 'Dia'), n)
        elif action == 'new-weekday':
            title, fields = 'Adicionar dia da semana', [name]
            operation = lambda c, n: c.write_weekday(n)
        elif action == 'edit-weekday':
            title, fields = 'Editar dia da semana', [Field('Posição na semana', 2), name]
            operation = lambda c, d, n: c.write_weekday(n, integer(d, 'Posição', maximum=24))
        elif action == 'set-date':
            c = Calendar(self.store.read()['calendar'])
            current = c.current
            title = 'Definir data e hora'
            fields = [Field('Ano', 6, str(current.year)), Field('Mês', 80, c.months[current.month]['name']),
                      Field('Dia', 6, str(current.day)), Field('Hora fictícia (HH:MM)', 5, optional=True)]
            def operation(c, y, m, d, t):
                c.set_date(c.resolve(integer(d, 'Dia'), m, integer(y, 'Ano')))
                if t:
                    c.set_clock(t)
        else:
            title, fields = 'Avançar ou voltar dias', [Field('Dias (negativo para voltar)', 6)]
            operation = lambda c, d: c.move(integer(d, 'Dias', minimum=-10000, maximum=10000))
        async def save(i, *values):
            self.store.calendar_change(lambda c: operation(c, *values))
            return 'Calendário atualizado.'
        await self.form(i, title, fields, save)

    async def daily_form(self, i):
        saved = self.store.read()['daily'] or {}
        channel_id = self.channel or saved.get('channel')
        if not channel_id:
            raise CalendarError('Escolha um canal primeiro.')
        async def save(i, hhmm, zone, post, event):
            post, event = yesno(post, 'Publicar data'), yesno(event, 'Avisar eventos')
            if post or event:
                channel = i.guild.get_channel(channel_id) if i.guild else None
                if not isinstance(channel, discord.TextChannel):
                    raise CalendarError('Escolha novamente um canal de texto do servidor.')
                if i.guild.me is None:
                    raise CalendarError('Não consegui verificar as permissões do bot neste canal. Tente de novo.')
                p = channel.permissions_for(i.guild.me)
                if not (p.view_channel and p.send_messages and p.embed_links):
                    raise CalendarError('O bot precisa ver o canal, enviar mensagens e inserir links.')
            self.store.change(lambda s: configure_daily(s, datetime.now(UTC), channel_id, hhmm, zone, post, event))
            return 'Avanço diário ativado.'
        await self.form(i, 'Configurar rotina diária', [
            Field('Horário real (HH:MM)', 5, saved.get('hhmm', '00:00')),
            Field('Fuso IANA', 64, saved.get('zone', 'America/Fortaleza')),
            Field('Publicar data (sim ou não)', 3, 'sim' if saved.get('post_date', True) else 'não'),
            Field('Avisar eventos (sim ou não)', 3, 'sim' if saved.get('post_events', True) else 'não'),
        ], save)

    async def pause_form(self, i):
        self.store.change(lambda s: s.update(daily=None, pending=None))
        await self.refresh(i, 'Avanço diário pausado.')

    async def event_ids(self, i):
        cal = Calendar(self.store.read()['calendar'])
        results = [f'`{e["id"]}` · {inline(e["name"])}' for e in cal.events]
        message = '\n'.join(results[:12]) or 'Nenhum evento.'
        if len(results) > 12:
            message += '\nUse /arkanis-admin eventos-listar para ver a lista completa.'
        await reply(i, message[:1950])

    async def event_form(self, i, action):
        event_id = Field('ID', 17)
        title_field = Field('Título', 100)
        date_field = Field('Data (ano/mês/dia)', 100, hint='1/Skaryel/12')
        text_field = Field('Descrição', 1500, optional=True)
        if action == 'new':
            title = 'Criar evento'
            fields = [title_field, date_field, text_field, Field('Ocultar (sim ou não)', 3, 'não')]
            operation = lambda c, n, d, t, h: c.add_event(n, t, event_date(c, d), yesno(h, 'Ocultar'))
        elif action == 'edit':
            title = 'Editar evento'
            fields = [event_id, Field('Novo título', 100, optional=True),
                      Field('Nova data (ano/mês/dia)', 100, optional=True),
                      Field('Descrição (－ para limpar)', 1500, optional=True),
                      Field('Ocultar (sim ou não)', 3, optional=True)]
            operation = lambda c, e, n, d, t, h: c.edit_event(
                integer(e, 'ID', maximum=2**53-1), n or None, '' if t in ('-', '－') else t or None,
                event_date(c, d) if d else None, yesno(h, 'Ocultar') if h else None,
            )
        else:
            title = 'Remover evento'
            fields = [event_id, Field('Confirmar (sim ou não)', 3)]
            def operation(c, event, confirm):
                if not yesno(confirm, 'Confirmar'):
                    raise CalendarError('Remoção cancelada.')
                c.events.remove(c.get_event(integer(event, 'ID', maximum=2**53-1)))
                for pos, entry in enumerate(c.events):
                    entry['sort_by'] = pos
        async def save(i, *values):
            self.store.calendar_change(lambda c: operation(c, *values))
            return 'Eventos atualizados.'
        await self.form(i, title, fields, save)


async def settings_panel(interaction, bot):
    view = SettingsView(bot, interaction.user.id)
    if await view.guard(interaction):
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
