"""Modelo celeste de Arkanis. Sóis não fazem parte do formato Fantasy Calendar."""

from dataclasses import dataclass
from math import acos, asin, atan2, cos, degrees, isfinite, radians, sin, tau

from calendar_core import CalendarError, key, label

MAX_SUNS = 8

# Semieixos em unidades da órbita padrão de Reart, fases/inclinações em graus.
DEFAULT_ORBITS = (
    {'name': 'Innart', 'orbit': 0.36, 'tilt': 62.0, 'phase': 245.0, 'size': 0.42, 'reference': False},
    {'name': 'Dinart', 'orbit': 0.65, 'tilt': 55.0, 'phase': 105.0, 'size': 0.72, 'reference': False},
    {'name': 'Reart', 'orbit': 1.0, 'tilt': 19.0, 'phase': 0.0, 'size': 1.0, 'reference': True},
)


@dataclass(frozen=True)
class SolarPosition:
    name: str
    sunrise: int | None
    sunset: int | None
    visible_now: bool
    horizon: str     # normal, always, never
    segment: str
    altitude: float | None
    period: float
    orbit_day: int
    size: float | None

    @property
    def visible_today(self):
        return self.horizon != 'never'


def default_sky():
    return {
        'latitude': 48.0,
        'unit_period': 1008.0,
        'suns': [dict(s, mode='orbit') for s in DEFAULT_ORBITS],
    }


def day_minutes(cal):
    return cal.clock['hours'] * cal.clock['minutes']


def orbital_period(sky, sun):
    return sky['unit_period'] * sun['orbit'] ** 1.5


def reference_sun(sky):
    return next(s for s in sky['suns'] if s.get('reference'))


def bounded(value, low, high, field):
    if not isfinite(value) or not low <= value <= high:
        raise CalendarError(f'{field} precisa ficar entre {low:g} e {high:g}.')
    return float(value)


def validate_reference(latitude, sun):
    # Reart (ou a referência renomeada) deve cruzar o horizonte em todas as fases.
    if abs(latitude) + abs(sun['tilt']) > 89:
        raise CalendarError(
            'A soma da latitude absoluta com a inclinação do sol de referência não pode passar de 89°. '
            'Isso garante pelo menos um nascer do sol por dia.'
        )


def set_latitude(state, latitude):
    sky = state['sky']
    latitude = bounded(latitude, -89, 89, 'A latitude')
    validate_reference(latitude, reference_sun(sky))
    sky['latitude'] = latitude


def find_sun(sky, name):
    for i, sun in enumerate(sky['suns']):
        if key(sun['name']) == key(name):
            return i, sun
    raise CalendarError('Sol não encontrado. Consulte /ceu sois.')


def write_sun(state, old=None, *, name=None, orbit=None, tilt=None, phase=None, size=None):
    sky = state['sky']
    suns = sky['suns']
    if old is None:
        if len(suns) >= MAX_SUNS:
            raise CalendarError(f'Este painel comporta até {MAX_SUNS} sóis.')
        if name is None:
            raise CalendarError('Dê um nome ao novo sol.')
        index = None
        sun = {'name': '', 'orbit': 0.8, 'tilt': 35.0, 'phase': 0.0, 'size': 0.65,
               'reference': False, 'mode': 'orbit'}
    else:
        index, previous = find_sun(sky, old)
        if previous['mode'] != 'orbit':
            if any(v is not None for v in (orbit, tilt, phase, size)):
                raise CalendarError('Este sol é de uma versão antiga e usa horários manuais. Crie outro sol para usar órbitas.')
            sun = dict(previous)
        else:
            sun = dict(previous)
    if name is not None:
        sun['name'] = label(name, 60)
    if any(i != index and key(s['name']) == key(sun['name']) for i, s in enumerate(suns)):
        raise CalendarError('Já existe um sol com esse nome.')
    if orbit is not None:
        sun['orbit'] = bounded(orbit, 0.04, 12, 'O semieixo')
    if tilt is not None:
        sun['tilt'] = bounded(tilt, -89, 89, 'A inclinação')
    if phase is not None:
        sun['phase'] = bounded(phase, -360, 360, 'A fase inicial') % 360
    if size is not None:
        sun['size'] = bounded(size, 0.05, 4, 'O tamanho relativo')
    if sun.get('reference'):
        validate_reference(sky['latitude'], sun)
    if index is None:
        suns.append(sun)
    else:
        suns[index] = sun
    return sun


def remove_sun(state, name):
    index, sun = find_sun(state['sky'], name)
    if sun.get('reference'):
        raise CalendarError('O sol de referência não pode ser removido. Ele garante luz em todos os dias de Arkanis.')
    state['sky']['suns'].pop(index)


def equatorial(sky, sun, epoch):
    period = orbital_period(sky, sun)
    lon = tau * ((epoch % period) / period) + radians(sun['phase'])
    tilt = radians(sun['tilt'])
    return atan2(sin(lon) * cos(tilt), cos(lon)), asin(sin(lon) * sin(tilt)), period


def segment(hour_angle, visible):
    if not visible:
        return 'Abaixo do horizonte'
    if abs(hour_angle) < tau / 40:
        return 'No ponto mais alto do dia'
    return 'Subindo' if hour_angle < 0 else 'Descendo'


def orbital_position(sky, sun, ref_ra, epoch, total, now):
    ra, decl, period = equatorial(sky, sun, epoch)
    latitude = radians(sky['latitude'])
    offset = sin(latitude) * sin(decl)
    amplitude = cos(latitude) * cos(decl)
    transit = (total / 2 + (ra - ref_ra) * total / tau) % total
    hour_angle = (tau * (now - transit) / total + tau / 2) % tau - tau / 2
    altitude = degrees(asin(max(-1.0, min(1.0, offset + amplitude * cos(hour_angle)))))
    sunrise = sunset = None
    if offset + amplitude <= 0:
        horizon, visible, stage = 'never', False, 'Não nasce neste dia'
    elif offset - amplitude >= 0:
        horizon, visible, stage = 'always', True, 'Acima do horizonte o dia inteiro'
    else:
        horizon = 'normal'
        half_daylight = acos(-offset / amplitude) * total / tau
        sunrise = round(transit - half_daylight) % total
        sunset = round(transit + half_daylight) % total
        visible = altitude >= 0
        stage = segment(hour_angle, visible)
    return SolarPosition(sun['name'], sunrise, sunset, visible, horizon, stage, altitude,
                         period, int(epoch % period) + 1, sun['size'])


def manual_position(sun, epoch, total, now):
    """Lê configurações que já existiam no bot anterior sem inventar órbitas para elas."""
    phase = (epoch - sun['shift']) % sun['period']
    variance = round(sun['variation'] * cos(tau * phase / sun['period']))
    sunrise = (sun['sunrise'] - variance) % total
    sunset = (sun['sunset'] + variance) % total
    span = (sunset - sunrise) % total
    since = (now - sunrise) % total
    visible = since < span
    if since == 0:
        stage = 'Nascendo'
    elif since == span:
        stage = 'Se pondo'
    else:
        hour_angle = tau * ((since / span) - 0.5)
        stage = segment(hour_angle, visible)
    return SolarPosition(sun['name'], sunrise, sunset, visible, 'normal', stage, None,
                         sun['period'], phase + 1, None)


def solar_positions(cal, sky, date, now):
    total = day_minutes(cal)
    epoch = cal.epoch(date)
    # A hora local é expressa em dia solar de Reart: sua passagem marca o meio-dia
    # (16:00 em Arkanis). As outras passagens usam a diferença de ascensão reta.
    ref_ra = equatorial(sky, reference_sun(sky), epoch)[0]
    for sun in sky['suns']:
        if sun['mode'] == 'orbit':
            yield orbital_position(sky, sun, ref_ra, epoch, total, now)
        else:
            yield manual_position(sun, epoch, total, now)
