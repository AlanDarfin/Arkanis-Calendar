"""Bot de calendário de Arkanis para um único servidor do Discord."""
from datetime import UTC, datetime
from io import BytesIO
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from calendar_core import Calendar, CalendarError, Date, key
from admin_ui import settings_panel
from heavens import remove_sun, set_latitude, solar_positions, write_sun
from presentation import (chunks, daily_notice_lines, day_lines, description, event_lines, event_row,
                          inline, month_lines, sky_moons, week_lines, year_lines)
from storage import Store, configure_daily, daily_tick, finish_post, json_bytes


ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger('arkanis')
COLOR = discord.Color.from_rgb(138, 99, 180)
NOMENTIONS = discord.AllowedMentions.none()
Day = app_commands.Range[int, 1, 500]
Year = app_commands.Range[int, 1, 999999]
DaysInMonth = app_commands.Range[int, 1, 120]


def utc_now():
    return datetime.now(UTC)


def make_embed(title, body, page=1, total=1):
    e = discord.Embed(title=title[:256], description=body, color=COLOR)
    if total > 1:
        e.set_footer(text=f'Página {page}/{total}')
    return e


class Pages(discord.ui.View):
    def __init__(self, owner, title, pages):
        super().__init__(timeout=180)
        self.owner = owner
        self.title = title
        self.pages = pages
        self.index = 0
        self.buttons()

    def buttons(self):
        self.previous.disabled = self.index == 0
        self.next_page.disabled = self.index + 1 == len(self.pages)

    def embed(self):
        return make_embed(self.title, self.pages[self.index], self.index + 1, len(self.pages))

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message('Abra seu próprio calendário para navegar.', ephemeral=True)
        return False

    @discord.ui.button(label='← Anterior', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self.buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='Próxima →', style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


async def panel(i, title, lines, private=False):
    pages = chunks(lines)
    if len(pages) > 1:
        view = Pages(i.user.id, title, pages)
        await i.response.send_message(embed=view.embed(), view=view, ephemeral=private)
    else:
        await i.response.send_message(embed=make_embed(title, pages[0]), ephemeral=private)


async def confirm(i, message):
    await i.response.send_message(message, ephemeral=True, allowed_mentions=NOMENTIONS)


def is_admin(i):
    return i.guild is not None and i.permissions.administrator


class AdminGroup(app_commands.Group):
    async def interaction_check(self, interaction: discord.Interaction):
        if is_admin(interaction):
            return True
        await confirm(interaction, 'Esse comando é restrito a administradores do servidor.')
        return False


class SingleGuildTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.guild_id == self.client.guild_id:
            return True
        await confirm(interaction, 'O calendário só funciona no servidor de Arkanis.')
        return False


class ArkanisClient(discord.Client):
    def __init__(self, guild_id, store):
        super().__init__(intents=discord.Intents(guilds=True), allowed_mentions=NOMENTIONS)
        self.guild_id = guild_id
        self.store = store
        self.tree = SingleGuildTree(self)
        install_commands(self)

    async def setup_hook(self):
        guild = discord.Object(id=self.guild_id)
        synced = await self.tree.sync(guild=guild)
        LOG.info('Sincronizados %s grupos de comandos no servidor configurado.', len(synced))
        self.daily.start()

    async def on_ready(self):
        LOG.info('Bot conectado como %s.', self.user)

    async def close(self):
        self.daily.cancel()
        await super().close()

    @tasks.loop(seconds=30)
    async def daily(self):
        try:
            pending = self.store.change(lambda state: daily_tick(state, utc_now()))
            if pending is None:
                return
            channel = await self.fetch_channel(pending['channel'])
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.guild_id:
                LOG.warning('Canal diário inválido. Configure novamente com /arkanis-admin diario.')
                return
            latest = self.store.read()
            if latest['pending'] != pending:
                return
            lines = daily_notice_lines(Calendar(latest['calendar']), pending)
            if lines is not None:
                await channel.send(embed=make_embed(pending['title'], chunks(lines)[0]), allowed_mentions=NOMENTIONS)
            self.store.change(lambda state: finish_post(state, pending))
        except discord.HTTPException as exc:
            LOG.warning('Não foi possível postar o aviso diário. Nova tentativa em 30 s (%s).', exc)
        except Exception:
            LOG.exception('Falha na atualização diária de Arkanis.')

    @daily.before_loop
    async def before_daily(self):
        await self.wait_until_ready()


def install_commands(bot):
    store = bot.store

    def cal():
        return Calendar(store.read()['calendar'])

    calendar = app_commands.Group(name='calendario', description='Consulte as datas de Arkanis.')
    events = app_commands.Group(name='eventos', description='Acontecimentos e agenda de Arkanis.')
    sky = app_commands.Group(name='ceu', description='Luas, sóis e estações de Arkanis.')
    admin = AdminGroup(name='arkanis-admin', description='Edite o calendário e a rotina diária.',
                       default_permissions=discord.Permissions(administrator=True))
    for group in (calendar, events, sky, admin):
        bot.tree.add_command(group, guild=discord.Object(id=bot.guild_id))

    async def month_suggestions(i: discord.Interaction, current: str):
        return [app_commands.Choice(name=month['name'], value=month['name']) for idx, month in
                enumerate(cal().months, start=1)
                if key(current) in key(month['name']) or current == str(idx)][:25]

    @bot.tree.error
    async def command_error(i: discord.Interaction, error: app_commands.AppCommandError):
        cause = getattr(error, 'original', error)
        if isinstance(cause, CalendarError):
            text = str(cause)
        elif isinstance(cause, app_commands.CheckFailure):
            text = 'Você não tem permissão para usar esse comando.'
        else:
            LOG.error('O comando falhou.', exc_info=(type(cause), cause, cause.__traceback__))
            text = 'Não consegui concluir esse comando. Confira os dados e tente novamente.'
        if i.response.is_done():
            await i.followup.send(text, ephemeral=True)
        else:
            await confirm(i, text)

    @calendar.command(description='Mostra a data atual, a estação e os acontecimentos de hoje.')
    async def hoje(i: discord.Interaction):
        c = cal()
        await panel(i, c.date_name(c.current), day_lines(c, c.current))

    @calendar.command(description='Mostra um dia específico de Arkanis.')
    async def dia(i: discord.Interaction, dia: Day, mes: str | None = None, ano: int | None = None):
        c = cal()
        d = c.resolve(dia, mes, ano)
        await panel(i, c.date_name(d), day_lines(c, d))

    @calendar.command(description='Mostra a semana que contém a data escolhida. Sem data, mostra a atual.')
    async def semana(i: discord.Interaction, dia: int | None = None,
                     mes: str | None = None, ano: int | None = None):
        c = cal()
        d = c.resolve(dia if dia is not None else (1 if mes is not None else None), mes, ano)
        await panel(i, f'Semana de {c.date_name(d)}', week_lines(c, d))

    @calendar.command(name='mes', description='Mostra os dias e eventos do mês, em lista para celular.')
    async def month(i: discord.Interaction, mes: str | None = None, ano: int | None = None):
        c = cal()
        d = c.resolve(1, mes, ano)
        await panel(i, f'{c.months[d.month]["name"]} · ano {d.year}', month_lines(c, d))

    @calendar.command(description='Lista todos os meses e dias da semana, em ordem.')
    async def ano(i: discord.Interaction):
        c = cal()
        await panel(i, c.document['name'] + ' · estrutura do ano', year_lines(c))

    @calendar.command(description='Mostra os comandos e o horário do próximo avanço configurado.')
    async def ajuda(i: discord.Interaction):
        state = store.read()
        cfg = state['daily']
        lines = [
            '**Datas** · /calendario hoje, dia, semana, mes e ano',
            '**Eventos** · /eventos hoje, dia, agenda e detalhe',
            '**Céu** · /ceu luas, sois e estacoes',
            '**Administração** · /arkanis-admin configuracoes',
        ]
        if cfg:
            lines.append(f'**Avanço diário** · {cfg["hhmm"]} no fuso {inline(cfg["zone"])}')
        else:
            lines.append('**Avanço diário** · ainda não foi configurado.')
        await panel(i, 'Como consultar Arkanis', lines)

    @events.command(name='hoje', description='Lista os eventos públicos de hoje.')
    async def event_today(i: discord.Interaction):
        c = cal()
        await panel(i, f'Eventos de {c.date_name(c.current)}',
                    event_lines(c.events_on(c.current)) or ['Nenhum evento publicado.'])

    @events.command(name='dia', description='Lista os eventos de uma data.')
    async def event_day(i: discord.Interaction, dia: Day, mes: str | None = None, ano: int | None = None):
        c = cal()
        d = c.resolve(dia, mes, ano)
        await panel(i, f'Eventos de {c.date_name(d)}', event_lines(c.events_on(d)) or ['Nenhum evento publicado.'])

    @events.command(description='Lista os próximos eventos públicos, incluindo hoje.')
    async def agenda(i: discord.Interaction, dias: app_commands.Range[int, 1, 120] = 30):
        c = cal()
        start = c.epoch(c.current)
        lines = []
        for epoch in range(start, start + dias):
            d = c.from_epoch(epoch)
            rows = event_lines(c.events_on(d))
            if rows:
                lines.extend([f'**{inline(c.date_name(d))}**', *rows, ''])
        await panel(i, f'Agenda · próximos {dias} dias de Arkanis', lines or ['Nenhum evento publicado nesse período.'])

    @events.command(description='Abra um evento pelo ID que aparece na lista.')
    async def detalhe(i: discord.Interaction, id: int):
        c = cal()
        e = c.get_event(id)
        hidden = bool(e['settings'].get('hide') or e['settings'].get('hide_full'))
        if hidden and not is_admin(i):
            raise CalendarError('Não encontrei esse ID de evento.')
        when = c.date_name(Date(*e['data']['date'])) if e['data']['date'] else 'Recorrente · início de estação'
        lines = [inline(when), '', description(e['description']) or 'Sem descrição.']
        await panel(i, e['name'], lines, private=hidden)

    @sky.command(description='Mostra as fases das luas de uma data.')
    async def luas(i: discord.Interaction, dia: int | None = None,
                   mes: str | None = None, ano: int | None = None):
        c = cal()
        d = c.resolve(dia if dia is not None else (1 if mes is not None else None), mes, ano)
        await panel(i, f'Luas · {c.date_name(d)}', sky_moons(c, d) or ['Nenhuma lua visível.'])

    @sky.command(description='Veja a posição, o nascer, o pôr e o ciclo dos sóis no horário de Arkanis.')
    async def sois(i: discord.Interaction, hora: str | None = None, dia: int | None = None,
                   mes: str | None = None, ano: int | None = None):
        state = store.read()
        c = Calendar(state['calendar'])
        d = c.resolve(dia if dia is not None else (1 if mes is not None else None), mes, ano)
        t = (c.clock_time(hora) if hora is not None else
             c.dynamic['hour'] * c.clock['minutes'] + c.dynamic['minute'])
        locations = list(solar_positions(c, state['sky'], d, t))
        today = ', '.join(inline(o.name) for o in locations if o.visible_today)
        now = ', '.join(inline(o.name) for o in locations if o.visible_now) or 'nenhum'
        lines = [f'**{c.format_time(t)} em Arkanis**', f'**Acima do horizonte agora** · {now}',
                 f'**Visíveis em algum momento do dia** · {today}', '']
        for sun in locations:
            icon = '☀️' if sun.visible_now else '◌'
            altitude = f' · {sun.altitude:+.1f}°' if sun.altitude is not None else ''
            lines.append(f'{icon} **{inline(sun.name)}** · {sun.segment}{altitude}')
            if sun.horizon == 'normal':
                lines.append(f'Nascer {c.format_time(sun.sunrise)} · Pôr {c.format_time(sun.sunset)}')
            size = f' · Tamanho relativo {sun.size:g}' if sun.size is not None else ''
            period = f'{sun.period:.2f}'.rstrip('0').rstrip('.').replace('.', ',')
            lines.extend([f'Ciclo de {period} dias · Dia {sun.orbit_day}{size}', ''])
        await panel(i, f'Sóis · {c.date_name(d)}', lines)

    @sky.command(description='Mostra a estação atual e quando começam as outras.')
    async def estacoes(i: discord.Interaction):
        c = cal()
        season = c.season(c.current)
        lines = []
        for idx, item in enumerate(c.seasons['data']):
            start = c.from_epoch((c.current.year - 1) * c.year_length + item['start'])
            now = ' ◀ atual' if season and season[0] == idx else ''
            lines.append(f'**{inline(item["name"])}** · {inline(c.date_name(start))} · {item["length"]} dias{now}')
        await panel(i, 'Estações de Arkanis', lines)

    @admin.command(name='evento-criar', description='Cria um evento com data, título e descrição.')
    async def event_create(i: discord.Interaction, titulo: str, dia: Day, mes: str | None = None,
                           ano: int | None = None, descricao: str = '', oculto: bool = False):
        e = store.calendar_change(lambda c: c.add_event(titulo, descricao, c.resolve(dia, mes, ano), oculto))
        await confirm(i, 'Evento criado. ' + event_row(e))

    @admin.command(name='evento-editar', description='Edita o título, descrição, data ou visibilidade de um evento.')
    async def event_edit(i: discord.Interaction, id: int, titulo: str | None = None,
                         descricao: str | None = None, oculto: bool | None = None, dia: int | None = None,
                         mes: str | None = None, ano: int | None = None):
        def edit(c):
            new_date = None
            if any(x is not None for x in (dia, mes, ano)):
                original = c.get_event(id)['data']['date']
                if not original:
                    raise CalendarError('Esse evento é recorrente e não tem data fixa.')
                new_date = Date(
                    original[0] if ano is None else ano,
                    original[1] if mes is None else c.month_index(mes),
                    original[2] if dia is None else dia,
                )
            return c.edit_event(id, titulo, descricao, new_date, oculto)
        e = store.calendar_change(edit)
        await confirm(i, 'Evento atualizado. ' + event_row(e))

    @admin.command(name='evento-remover', description='Remove um evento pelo ID. Use confirmar:sim.')
    async def event_remove(i: discord.Interaction, id: int, confirmar: bool):
        if not confirmar:
            raise CalendarError('Marque confirmar para remover o evento.')
        def remove(c):
            event = c.get_event(id)
            c.events.remove(event)
            for idx, entry in enumerate(c.events):
                entry['sort_by'] = idx
            return event['name']
        name = store.calendar_change(remove)
        await confirm(i, f'Evento removido · {inline(name)}.')

    @admin.command(name='eventos-listar', description='Lista IDs de todos os eventos, inclusive ocultos.')
    async def all_events(i: discord.Interaction):
        c = cal()
        await panel(i, 'Todos os eventos · administração',
                    [event_row(e) for e in c.events], private=True)

    @admin.command(name='mes-adicionar', description='Cria um mês. Informe antes_de para inseri-lo antes de outro.')
    async def add_month(i: discord.Interaction, nome: str, dias: DaysInMonth, antes_de: str | None = None):
        store.calendar_change(lambda c: c.add_month(nome, dias, antes_de))
        await confirm(i, 'Mês adicionado. As datas existentes e os inícios de estação foram recalculados.')

    @admin.command(name='mes-editar', description='Renomeia um mês ou muda a quantidade de dias comuns.')
    async def edit_month(i: discord.Interaction, mes: str, nome: str | None = None,
                         dias: int | None = None):
        if nome is None and dias is None:
            raise CalendarError('Informe um nome novo ou a quantidade de dias.')
        store.calendar_change(lambda c: c.edit_month(mes, nome, dias))
        await confirm(i, 'Mês atualizado. As datas e estações foram recalculadas.')

    @admin.command(name='dia-adicionar', description='Acrescenta um dia especial numerado no fim de um mês, todo ano.')
    async def add_day(i: discord.Interaction, mes: str, nome: str):
        number = store.calendar_change(lambda c: c.add_special_day(mes, nome))
        await confirm(i, f'Dia especial adicionado no dia {number} do mês.')

    @admin.command(name='dia-editar', description='Altera o nome de um dia especial dentro de um mês.')
    async def edit_day(i: discord.Interaction, mes: str, dia: Day, nome: str):
        store.calendar_change(lambda c: c.edit_special_day(mes, dia, nome))
        await confirm(i, 'Dia especial atualizado.')

    @admin.command(name='semana-adicionar', description='Adiciona um dia ao fim da semana.')
    async def add_weekday(i: discord.Interaction, nome: str):
        store.calendar_change(lambda c: c.write_weekday(nome))
        await confirm(i, 'Dia da semana adicionado. Veja a ordem em /calendario ano.')

    @admin.command(name='semana-editar', description='Renomeia um dia da semana pelo número mostrado em /calendario ano.')
    async def edit_weekday(i: discord.Interaction, numero: app_commands.Range[int, 1, 24], nome: str):
        store.calendar_change(lambda c: c.write_weekday(nome, numero))
        await confirm(i, 'Dia da semana renomeado.')

    @admin.command(name='data-definir', description='Define a data atual e, se quiser, o horário dentro de Arkanis.')
    async def set_date(i: discord.Interaction, dia: Day, mes: str, ano: Year, hora: str | None = None):
        def edit(c):
            d = c.set_date(c.resolve(dia, mes, ano))
            if hora is not None:
                c.set_clock(hora)
            return c.date_name(d)
        when = store.calendar_change(edit)
        await confirm(i, f'Data atual · {inline(when)}.')

    @admin.command(name='data-mover', description='Avança ou volta dias manualmente. Use um número negativo para voltar.')
    async def move_date(i: discord.Interaction, dias: app_commands.Range[int, -10000, 10000]):
        when = store.calendar_change(lambda c: c.date_name(c.move(dias)))
        await confirm(i, f'Data atual · {inline(when)}.')

    @admin.command(name='exportar', description='Baixa o calendário no formato JSON do Fantasy Calendar.')
    async def export_calendar(i: discord.Interaction):
        doc = store.read()['calendar']
        file = discord.File(BytesIO(json_bytes(doc)), filename='arkanis.json')
        await i.response.send_message('Exportação do Fantasy Calendar. Inclui eventos ocultos. Os sóis estão no backup do bot.',
                                      file=file, ephemeral=True)

    @admin.command(name='backup', description='Baixa a cópia completa da configuração do bot, incluindo os sóis.')
    async def backup(i: discord.Interaction):
        file = discord.File(BytesIO(json_bytes(store.read())), filename='arkanis-bot-backup.json')
        await i.response.send_message('Backup completo do bot. Não é o arquivo de importação do Fantasy Calendar.',
                                      file=file, ephemeral=True)

    @admin.command(name='configuracoes', description='Abre o painel interativo de configuração de Arkanis.')
    async def settings(i: discord.Interaction):
        await settings_panel(i, bot)

    @admin.command(name='sol-adicionar', description='Cria um sol com semieixo, inclinação, fase e tamanho relativos.')
    async def solar_create(i: discord.Interaction, nome: str, orbita: float, inclinacao: float,
                           fase: float = 0.0, tamanho: float = 0.65):
        store.change(lambda state: write_sun(state, name=nome, orbit=orbita, tilt=inclinacao,
                                             phase=fase, size=tamanho))
        await confirm(i, 'Sol adicionado. Consulte /ceu sois.')

    @admin.command(name='sol-editar', description='Altera os parâmetros de um sol.')
    async def solar_edit(i: discord.Interaction, sol: str, nome: str | None = None,
                         orbita: float | None = None, inclinacao: float | None = None,
                         fase: float | None = None, tamanho: float | None = None):
        store.change(lambda state: write_sun(state, sol, name=nome, orbit=orbita, tilt=inclinacao,
                                             phase=fase, size=tamanho))
        await confirm(i, 'Sol atualizado.')

    @admin.command(name='sol-remover', description='Remove um sol que não seja o de referência.')
    async def solar_delete(i: discord.Interaction, sol: str):
        store.change(lambda state: remove_sun(state, sol))
        await confirm(i, 'Sol removido.')

    @admin.command(name='ceu-latitude', description='Define a latitude do ponto de observação do céu.')
    async def solar_latitude(i: discord.Interaction, latitude: float):
        store.change(lambda state: set_latitude(state, latitude))
        await confirm(i, 'Latitude atualizada. Consulte /ceu sois.')

    @admin.command(name='diario', description='Ativa avanço diário e escolhe onde e quando publicar as notificações.')
    async def daily_cfg(i: discord.Interaction, canal: discord.TextChannel, horario: str = '00:00',
                        fuso: str = 'America/Fortaleza', postar_data: bool = True, avisar_eventos: bool = True):
        me = i.guild.me
        if me is None:
            await i.response.defer(ephemeral=True)
            me = await i.guild.fetch_member(bot.user.id)
        p = canal.permissions_for(me)
        if (postar_data or avisar_eventos) and not (p.view_channel and p.send_messages and p.embed_links):
            raise CalendarError('O bot precisa ver o canal, enviar mensagens e inserir links no canal escolhido.')
        store.change(lambda s: configure_daily(s, utc_now(), canal.id, horario, fuso, postar_data, avisar_eventos))
        message = f'Avanço ativado · 1 dia de Arkanis por dia real às **{horario}** no fuso **{inline(fuso)}**.'
        if postar_data or avisar_eventos:
            message += f' Os avisos vão para {canal.mention}.'
        else:
            message += ' O bot vai avançar em silêncio.'
        if i.response.is_done():
            await i.followup.send(message, ephemeral=True)
        else:
            await confirm(i, message)

    @admin.command(name='pausar', description='Pausa o avanço automático e os avisos. Retome com o comando diario.')
    async def pause(i: discord.Interaction):
        store.change(lambda state: state.update(daily=None, pending=None))
        await confirm(i, 'Avanço diário e avisos pausados.')

    for command in (dia, semana, month, event_day, luas, sois, event_create, event_edit, edit_month,
                    add_day, edit_day, set_date):
        command.autocomplete('mes')(month_suggestions)
    add_month.autocomplete('antes_de')(month_suggestions)


def load_dotenv(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
            continue
        name, value = (part.strip() for part in line.split('=', 1))
        if name in {'DISCORD_TOKEN', 'DISCORD_GUILD_ID', 'ARKANIS_DB'}:
            os.environ.setdefault(name, value.strip(chr(34) + chr(39)))


def main():
    load_dotenv(ROOT / '.env')
    token = os.environ.get('DISCORD_TOKEN', '')
    guild = os.environ.get('DISCORD_GUILD_ID', '')
    if not token or not guild.isdecimal() or int(guild) <= 0:
        raise SystemExit('Preencha DISCORD_TOKEN e DISCORD_GUILD_ID no .env. Veja o README.')
    db = Path(os.environ.get('ARKANIS_DB', 'runtime/arkanis.sqlite3')).expanduser()
    if not db.is_absolute():
        db = ROOT / db
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ArkanisClient(int(guild), Store(db, ROOT / 'data/arkanis.json')).run(token, log_handler=None)


if __name__ == '__main__':
    main()
