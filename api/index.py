"""XAUUSD signal bot (SMC + Liquidity Sweep + Classic SNR, alohida ICT modeli). Vercel + Flask + Telegram.

Muhit o'zgaruvchilari (Vercel > Settings > Environment Variables):
  BOT_TOKEN        - @BotFather dan olingan bot tokeni (MAJBURIY)
  ADMIN_ID         - signal keladigan Telegram ID (MAJBURIY)
  WEBHOOK_SECRET   - istalgan uzun tasodifiy satr, faqat A-Z a-z 0-9 _ - (MAJBURIY)
  TWELVE_API_KEY   - twelvedata.com bepul kaliti (tavsiya; bo'lmasa Yahoo GC=F ishlatiladi)
  CRON_SECRET      - /scan uchun kalit (bo'lmasa WEBHOOK_SECRET ishlatiladi)
  MIN_SCORE, MIN_RR, ENTRY_TIMEOUT - ixtiyoriy sozlamalar
  ICT_MIN_SCORE    - ICT signal uchun minimal ball (10 dan, standart 7)
  FIBO_MIN_SCORE   - Fibo + SNR signal uchun minimal ball (10 dan, standart 7)
  ANTHROPIC_API_KEY - skrinshot tahlili uchun (ixtiyoriy; bo'lmasa rasm tahlili o'chiq)
  UPSTASH_REDIS_REST_URL / _TOKEN yoki KV_REST_API_URL / _TOKEN - ixtiyoriy doimiy baza
"""
import hmac
import html
import io
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telebot  # noqa: E402
from flask import Flask, jsonify, request  # noqa: E402

import _data as data  # noqa: E402
import _fibo as fibo  # noqa: E402
import _ict as ict  # noqa: E402
import _smc as smc  # noqa: E402
import _store as store  # noqa: E402
import _vision as vision  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('signalbot')

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0') or 0)
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')
CRON_SECRET = os.environ.get('CRON_SECRET') or WEBHOOK_SECRET

CFG = dict(smc.CFG)
for env, key, cast in (('MIN_SCORE', 'min_score', int), ('MIN_RR', 'min_rr', float),
                       ('ENTRY_TIMEOUT', 'entry_timeout', int)):
    if os.environ.get(env):
        try:
            CFG[key] = cast(os.environ[env])
        except ValueError:
            log.warning('%s noto\'g\'ri: %s', env, os.environ[env])

ICT_CFG = dict(ict.CFG)
if os.environ.get('ICT_MIN_SCORE'):
    try:
        ICT_CFG['min_score'] = int(os.environ['ICT_MIN_SCORE'])
    except ValueError:
        log.warning("ICT_MIN_SCORE noto'g'ri: %s", os.environ['ICT_MIN_SCORE'])

FIBO_CFG = dict(fibo.CFG)
if os.environ.get('FIBO_MIN_SCORE'):
    try:
        FIBO_CFG['min_score'] = int(os.environ['FIBO_MIN_SCORE'])
    except ValueError:
        log.warning("FIBO_MIN_SCORE noto'g'ri: %s", os.environ['FIBO_MIN_SCORE'])

bot = telebot.TeleBot(BOT_TOKEN or '0:missing', threaded=False)
app = Flask(__name__)

KIND_UZ = {'PDH': 'kecha high', 'PDL': 'kecha low', 'PWH': "o'tgan hafta high", 'PWL': "o'tgan hafta low",
           'ASH': 'Osiyo sessiyasi high', 'ASL': 'Osiyo sessiyasi low',
           'EQH': 'teng yuqorilar (EQH)', 'EQL': 'teng pastlar (EQL)', 'SNR': 'SNR zona', 'RR': 'RR chegarasi',
           'LOH': 'London high', 'LOL': 'London low'}
STAGE_UZ = {
    'no_sweep': "likvidlik sweep'i yo'q", 'sweep_no_snr': "sweep bor, lekin SNR zonasida emas",
    'sweep_no_mss': 'sweep va SNR bor, MSS (struktura buzilishi) kutilmoqda',
    'no_zone': "MSS bor, lekin FVG/OB zonasi yo'q yoki narx zonadan o'tib ketgan",
    'risk_fail': "SL masofasi mos kelmadi", 'rr_fail': "TP nishon 1:%s ga yetmaydi",
    'score_low': "ball yetarli emas", 'ok': 'signal bor', 'data': "ma'lumot yetarli emas",
}


def esc(x):
    return html.escape(str(x), quote=False)


def tk(t):    # Toshkent vaqti (UTC+5)
    return time.strftime('%d.%m %H:%M', time.gmtime(t + 5 * 3600))


def tkh(t):
    return time.strftime('%H:%M', time.gmtime(t + 5 * 3600))


def is_admin(uid):
    return ADMIN_ID != 0 and int(uid) == ADMIN_ID


def safe_send(chat, text, **kw):
    try:
        return bot.send_message(int(chat), text, parse_mode='HTML', **kw)
    except Exception:  # noqa: BLE001
        log.exception('send_message')


def send_photo(chat, png, caption=''):
    try:
        return bot.send_photo(int(chat), io.BytesIO(png), caption=caption[:1024] or None, parse_mode='HTML')
    except Exception:  # noqa: BLE001
        log.exception('send_photo')


def fmt_signal(s, source=''):
    sell = s['side'] == 'SELL'
    em = '🟥' if sell else '🟩'
    ez = s['ez']
    risk = abs(s['sl'] - s['entry'])
    zone_kind = 'FVG + Order Block' if s['ov'] else ('FVG' if s['fvg'] else 'Order Block')
    lines = [
        f"{em} <b>{s['side']} XAUUSD</b>  |  ball {s['score']}/{smc.MAX_SCORE}",
        f"📍 Kirish (limit): <b>{s['entry']:.2f}</b>  (zona {ez['lo']:.2f} - {ez['hi']:.2f})",
        f"🛑 SL: <b>{s['sl']:.2f}</b>  (xavf ${risk:.2f})",
        f"🎯 TP: <b>{s['tp']:.2f}</b>  (1:{s['rr']:.1f}, {esc(KIND_UZ.get(s['tpk'], s['tpk']))})",
    ]
    if s.get('tp2'):
        lines.append(f"🎯 TP2: <b>{s['tp2']:.2f}</b>")
    lines += [
        '',
        '<b>Nima uchun:</b>',
        f"• Likvidlik: {esc(KIND_UZ.get(s['level']['kind'], s['level']['kind']))} {s['level']['price']:.2f} "
        f"{'ustiga' if sell else 'ostiga'} sweep (soya {s['sweep_ext']:.2f})",
        f"• SNR: {s['zone']['lo']:.2f} - {s['zone']['hi']:.2f} zona, {s['zone']['touches']} marta qaytgan",
        f"• MSS: {s['mss_level']:.2f} {'ostiga' if sell else 'ustiga'} yopildi",
        f"• Kirish zonasi: {zone_kind}",
        '',
        f"⏱ Sweep {tkh(s['sweep_t'])}, MSS {tkh(s['mss_t'])} (Toshkent vaqti)",
        "❗️ Limit order 3 soat ichida to'lmasa yoki narx avval TP ga yetib borsa, bekor qiling.",
        "⚠️ Kafolat yo'q. Bitta savdoda balansning 0.5-1% idan ko'p xavf qilmang.",
    ]
    if source:
        lines.append(f"<i>Manba: {esc(source)}</i>")
    return '\n'.join(lines)


def load(n):
    c, src = data.fetch_m15(n)
    return data.drop_incomplete(c), src


def load_live(n=3000):
    """(yopilgan shamlar, manba, hozirgi shakllanayotgan sham yoki None, jonli narx)."""
    raw, src = data.fetch_m15(n)
    if not raw:
        raise RuntimeError("narx ma'lumoti kelmadi")
    forming = raw[-1] if raw[-1]['t'] + 900 > time.time() else None
    return data.drop_incomplete(raw), src, forming, raw[-1]['c']


def _near_zone(mp, x):
    return any(z['lo'] - mp['pad'] <= x <= z['hi'] + mp['pad'] for z in mp['all_zones'])


def _plan(side, diag, mp, p, forming):
    """Shu yo'nalishda signal chiqishi uchun nima kutilayotgani."""
    sell = side == 'SELL'
    w = diag.get(side.lower() + '_watch')
    if w:
        return ("sweep BO'LDI: %s %.2f %s soya %.2f. Endi M15 sham <b>%.2f</b> %s yopilsa (MSS), signal chiqadi. Muddat: %d sham."
                % (KIND_UZ.get(w['level_kind'], w['level_kind']), w['level_price'], 'ustiga' if sell else 'ostiga',
                   w['sweep_ext'], w['mss_level'], 'ostida' if sell else 'ustida', max(w['bars_left'], 0)))
    if sell:
        cands = sorted([l for l in mp['levels'] if l['side'] == 'high' and l['price'] > p], key=lambda l: l['price'])
    else:
        cands = sorted([l for l in mp['levels'] if l['side'] == 'low' and l['price'] < p], key=lambda l: -l['price'])
    cands = [l for l in cands if abs(l['price'] - p) <= mp['atr'] * 6]
    if not cands:
        return "yaqin atrofda (6 ATR ichida) sweep nishoni yo'q."
    lv = cands[0]
    dist = abs(lv['price'] - p)
    name = '%s %.2f (%s%.1f$)' % (KIND_UZ.get(lv['kind'], lv['kind']), lv['price'], '+' if sell else '-', dist)
    snr = ('SNR zonada ✅' if _near_zone(mp, lv['price'])
           else "SNR zonadan tashqarida ⚠️ (bu holatda signal chiqmaydi)")
    pierced = forming and ((forming['h'] > lv['price']) if sell else (forming['l'] < lv['price']))
    if pierced:
        return ("hozirgi sham %s allaqachon %s chiqdi. Yopilishini kuting: %s yopilsa sweep, %s yopilsa breakout (signal yo'q). %s"
                % (name, 'ustiga' if sell else 'ostiga', 'ostida' if sell else 'ustida',
                   'ustida' if sell else 'ostida', snr))
    return ("narx %s %s soya chiqarib, qaytib %s yopilsa sweep bo'ladi. %s. Keyin MSS va FVG/OB kutiladi."
            % (name, 'ustiga' if sell else 'ostiga', 'ostida' if sell else 'ustida', snr))


def build_analysis(c, src, forming, price):
    """Jonli narx va oxirgi yopilgan shamlar asosida to'liq bozor tahlili (matn)."""
    P = smc.Prep(c, CFG)
    now = len(c) - 1
    sig, diag = smc.find_signal(P, now, CFG)
    mp = smc.market_map(P, now, CFG)
    if mp is None:
        return "Tahlil uchun ma'lumot yetarli emas.", None
    last_close_t = c[-1]['t'] + 900
    head = ("📡 <b>XAUUSD jonli tahlil</b>\nNarx: <b>%.2f</b>  (%s, Toshkent)\nStruktura oxirgi yopilgan sham (%s) bo'yicha."
            % (price, tkh(time.time()), tkh(last_close_t)))
    L = [head, '']
    L.append("📈 H4 yo'nalish: <b>%s</b> (narx EMA50 %s)" % (mp['bias'], 'ostida' if mp['bias'] == 'pastga' else 'ustida'))
    L.append('')
    if sig:
        gap = abs(sig['entry'] - price)
        L.append("🔔 <b>SIGNAL BOR</b>\n")
        L.append(fmt_signal(sig, ''))
        L.append("\n<i>Narx kirishdan hozir $%.2f uzoqda.</i>" % gap)
    else:
        L.append("🎯 <b>Signal: hozircha yo'q</b>")
        for side, em in (('SELL', '🟥'), ('BUY', '🟩')):
            st = diag.get(side.lower(), 'no_sweep')
            txt = STAGE_UZ.get(st, st)
            if '%s' in txt:
                txt = txt % ('%.1f' % CFG['min_rr'])
            L.append('\n%s <b>%s</b>: %s\n   ➜ %s' % (em, side, txt, _plan(side, diag, mp, price, forming)))
    p = price
    res = sorted([z for z in mp['all_zones'] if z['lo'] > p], key=lambda z: z['lo'])[:2]
    sup = sorted([z for z in mp['all_zones'] if z['hi'] < p], key=lambda z: -z['hi'])[:2]
    inside = [z for z in mp['all_zones'] if z['lo'] <= p <= z['hi']]
    L += ['', '🧱 <b>Eng yaqin SNR zonalar:</b>']
    for z in reversed(res):
        L.append('⬆️ qarshilik %.2f - %.2f  (+%.1f$, %d marta)' % (z['lo'], z['hi'], z['lo'] - p, z['touches']))
    for z in inside:
        L.append('➡️ narx zona ichida: %.2f - %.2f (%d marta)' % (z['lo'], z['hi'], z['touches']))
    for z in sup:
        L.append('⬇️ qo\'llab-quvvat %.2f - %.2f  (-%.1f$, %d marta)' % (z['lo'], z['hi'], p - z['hi'], z['touches']))
    if not res:
        L.insert(L.index('🧱 <b>Eng yaqin SNR zonalar:</b>') + 1, "⬆️ yuqorida tasdiqlangan SNR zona yo'q (SELL uchun SNR sharti bajarilmaydi)")
    if not sup:
        L.append("⬇️ pastda tasdiqlangan SNR zona yo'q (BUY uchun SNR sharti bajarilmaydi)")
    ab = sorted([l for l in mp['levels'] if l['side'] == 'high' and l['price'] > p], key=lambda l: l['price'])[:2]
    be = sorted([l for l in mp['levels'] if l['side'] == 'low' and l['price'] < p], key=lambda l: -l['price'])[:2]
    L += ['', '💧 <b>Tegilmagan likvidlik (sweep nishonlari):</b>']
    for l in reversed(ab):
        L.append('⬆️ %s %.2f  (+%.1f$)' % (KIND_UZ.get(l['kind'], l['kind']), l['price'], l['price'] - p))
    for l in be:
        L.append('⬇️ %s %.2f  (-%.1f$)' % (KIND_UZ.get(l['kind'], l['kind']), l['price'], p - l['price']))
    if not (ab or be):
        L.append('yaqin likvidlik yo\'q')
    L += ['', "<i>Bu tahlil, kafolat emas. Manba: %s</i>" % esc(src)]
    return '\n'.join(L), sig


def run_scan(notify=True):
    c, src = load(3000)
    if len(c) < 900:
        return {'ok': False, 'msg': "ma'lumot yetarli emas (%d sham)" % len(c)}, None, None
    age_min = (time.time() - (c[-1]['t'] + 900)) / 60.0
    if age_min > 90:
        return {'ok': True, 'msg': 'bozor yopiq (oxirgi sham %d daqiqa oldin)' % age_min}, None, None
    P = smc.Prep(c, CFG)
    now = len(c) - 1
    sig, diag = smc.find_signal(P, now, CFG)
    sent = False
    if sig and notify and store.claim(sig['key']):
        store.save_signal(sig)
        if ADMIN_ID:
            safe_send(ADMIN_ID, fmt_signal(sig, src))
            sent = True
    return {'ok': True, 'signal': bool(sig), 'sent': sent, 'diag': diag, 'last': c[-1]['t']}, sig, (P, now, src)


# ----------------------------------------------------------------- Telegram buyruqlari
def guard(m):
    if not is_admin(m.chat.id):
        safe_send(m.chat.id, "Bu shaxsiy signal bot. Kirish yopiq. Sizning ID: <code>%s</code>" % m.chat.id)
        return False
    return True


@bot.message_handler(commands=['start', 'help'])
def cmd_start(m):
    if not guard(m):
        return
    safe_send(m.chat.id,
              "<b>XAUUSD signal bot</b>\nSMC + Liquidity Sweep + Classic SNR, alohida ICT va Fibo modellari\n\n"
              "<b>signal</b> deb yozing: SMC bo'yicha jonli tahlil\n"
              "<b>ict</b> deb yozing: ICT bo'yicha alohida tahlil, signal va grafik rasm\n"
              "<b>rasm</b> deb yozing: zonalar va likvidlik chizilgan grafik\n"
              "<b>fibo</b> deb yozing: M15/M5/M1 trend bo'yicha avto Fibo + SNR signal va grafik\n"
              "📷 Grafik skrinshotini yuboring: AI uni ICT/SMC bo'yicha tahlil qiladi\n\n"
              "/zones - narx atrofidagi SNR zonalar va likvidlik\n"
              "/backtest - SMC strategiyasini tarixda sinash\n"
              "/ictbacktest - ICT modelini tarixda sinash\n"
              "/tarix - oxirgi signallar\n"
              "/sozlama - joriy sozlamalar\n\n"
              "Bot o'zi xabar yubormaydi, faqat siz so'raganda tahlil qiladi.")


def _analyze_reply(chat_id):
    try:
        c, src, forming, price = load_live(3000)
    except Exception as e:  # noqa: BLE001
        log.exception('analyze load')
        return safe_send(chat_id, "⚠️ Narx ma'lumotini olib bo'lmadi: %s" % esc(e))
    if len(c) < 900:
        return safe_send(chat_id, "⚠️ Tahlil uchun ma'lumot yetarli emas (%d sham)." % len(c))
    age_min = (time.time() - (c[-1]['t'] + 900)) / 60.0
    if age_min > 90:
        return safe_send(chat_id, "⏸ Oltin bozori hozir yopiq (oxirgi sham %d daqiqa oldin, narx %.2f). Dushanba ertalab ochiladi."
                         % (age_min, price))
    try:
        text, sig = build_analysis(c, src, forming, price)
    except Exception as e:  # noqa: BLE001
        log.exception('analyze')
        return safe_send(chat_id, "⚠️ Tahlil bajarilmadi: %s" % esc(e))
    if sig:
        store.save_signal(sig)
    safe_send(chat_id, text)


@bot.message_handler(commands=['signal'])
def cmd_signal(m):
    if guard(m):
        _analyze_reply(m.chat.id)


@bot.message_handler(func=lambda m: bool(getattr(m, 'text', None)) and m.text.strip().lower().strip('!.?') in ('signal', 'сигнал', 'analiz', 'tahlil'))
def txt_signal(m):
    if guard(m):
        _analyze_reply(m.chat.id)


@bot.message_handler(commands=['zones'])
def cmd_zones(m):
    if not guard(m):
        return
    try:
        c, src = load(3000)
        P = smc.Prep(c, CFG)
        mp = smc.market_map(P, len(c) - 1, CFG)
    except Exception as e:  # noqa: BLE001
        log.exception('zones')
        return safe_send(m.chat.id, "⚠️ %s" % esc(e))
    if not mp:
        return safe_send(m.chat.id, "Ma'lumot yetarli emas.")
    p = mp['price']
    L = ["🗺 <b>XAUUSD xaritasi</b>  (narx %.2f, H4 yo'nalish: %s)" % (p, mp['bias']), '', '<b>SNR zonalar (H4):</b>']
    for z in mp['zones']:
        mark = '⬆️' if z['lo'] > p else ('⬇️' if z['hi'] < p else '➡️')
        L.append('%s %.2f - %.2f  (%d marta)' % (mark, z['lo'], z['hi'], z['touches']))
    L += ['', '<b>Likvidlik:</b>']
    for lv in mp['levels']:
        if lv['kind'] in ('EQH', 'EQL') and abs(lv['price'] - p) > mp['atr'] * 12:
            continue
        L.append('%s %s %.2f' % ('⬆️' if lv['price'] > p else '⬇️', KIND_UZ.get(lv['kind'], lv['kind']), lv['price']))
    safe_send(m.chat.id, '\n'.join(L[:40]) + '\n\n<i>%s</i>' % esc(src))


def fmt_backtest(r, src):
    if not r['n']:
        return ("📊 <b>Backtest</b>: %d sham (%s dan)\nSignal: %d, lekin bitta ham savdo to'liq bajarilmadi.\n"
                "Bu bu davrda strategiya juda kam signal berganini bildiradi." % (r['bars'], tk(r['from']), r['signals']))
    pf = '∞' if r['pf'] == float('inf') else '%.2f' % r['pf']
    by = {}
    for t in r['trades']:
        by.setdefault(t['side'], []).append(t['r'])
    side_txt = ', '.join('%s: %d ta (%.2f R)' % (k, len(v), sum(v)) for k, v in sorted(by.items()))
    verdict = ''
    if r['n'] < 30:
        verdict = "\n⚠️ Savdolar soni %d ta: bu ishonchli xulosa uchun juda kam (kamida 30-50 kerak)." % r['n']
    return (
        "📊 <b>Backtest XAUUSD M15</b>\n%s dan %s gacha (%d sham)\n\n"
        "Signallar: %d, bajarilgan savdolar: <b>%d</b>\n"
        "To'lmay qolgan: %d, TP ga avval yetgan: %d\n\n"
        "G'alaba: <b>%.0f%%</b> (%d/%d)\n"
        "O'rtacha natija: <b>%+.2f R</b> (spred $%.2f hisobga olingan)\n"
        "Jami: <b>%+.1f R</b>\n"
        "Profit factor: %s\n"
        "Maksimal pasayish: %.1f R, ketma-ket yutqazish: %d\n"
        "%s%s\n\n"
        "<i>1 R = savdodagi xavf (SL masofasi). Bu o'tmish natijasi, kelajakni kafolatlamaydi.</i>\n"
        "<i>Manba: %s</i>"
        % (tk(r['from']), tk(r['to']), r['bars'], r['signals'], r['n'], r['expired'], r['missed'],
           r['win_rate'], r['wins'], r['n'], r['avg_r'], CFG['cost'], r['total_r'], pf,
           r['max_dd_r'], r['max_loss_streak'], side_txt, verdict, esc(src)))


@bot.message_handler(commands=['backtest'])
def cmd_backtest(m):
    if not guard(m):
        return
    safe_send(m.chat.id, '⏳ Tarixiy ma\'lumot olinmoqda va sinalmoqda...')
    try:
        c, src = load(5000)
        r = smc.backtest(c, CFG)
    except Exception as e:  # noqa: BLE001
        log.exception('backtest')
        return safe_send(m.chat.id, "⚠️ Backtest bajarilmadi: %s" % esc(e))
    safe_send(m.chat.id, fmt_backtest(r, src))


@bot.message_handler(commands=['tarix'])
def cmd_history(m):
    if not guard(m):
        return
    recs = store.last_signals(10)
    if not recs:
        return safe_send(m.chat.id, "Hali signal bo'lmagan.")
    L = ['🗂 <b>Oxirgi signallar</b>']
    for r in recs:
        L.append('%s  %s  kirish %.2f, SL %.2f, TP %.2f (ball %d)'
                 % (tk(r.get('saved', 0)), r['side'], r['entry'], r['sl'], r['tp'], r['score']))
    safe_send(m.chat.id, '\n'.join(L))


@bot.message_handler(commands=['sozlama'])
def cmd_cfg(m):
    if not guard(m):
        return
    keys = ('min_score', 'min_rr', 'rr_cap', 'mss_max_bars', 'entry_timeout', 'snr_min_touches', 'cost')
    safe_send(m.chat.id, '⚙️ <b>Sozlamalar</b>\n' + '\n'.join('%s = %s' % (k, CFG[k]) for k in keys)
              + "\n\n<i>MIN_SCORE, MIN_RR, ENTRY_TIMEOUT ni Vercel muhit o'zgaruvchilari orqali o'zgartirasiz.</i>"
              + ("\n\n✅ Doimiy baza ulangan." if store.persistent() else "\n\n⚠️ Doimiy baza yo'q: takroriy signal himoyasi vaqtincha."))


# ----------------------------------------------------------------- ICT (alohida model)
ICT_STAGE_UZ = {
    'no_sweep': "killzone ichida likvidlik sweep'i yo'q",
    'sweep_no_mss': "sweep bor, MSS (displacement bilan struktura buzilishi) kutilmoqda",
    'no_fvg': "MSS bor, lekin displacement FVG qoldirmadi",
    'filled': "setup bo'ldi, narx FVG ga allaqachon qaytib ketgan",
    'risk_fail': "SL masofasi mos kelmadi", 'rr_fail': "TP nishon 1:%s ga yetmaydi",
    'score_low': "ball yetarli emas", 'ok': 'signal bor', 'data': "ma'lumot yetarli emas",
}
BIAS_UZ = {'down': 'pastga (bearish)', 'up': 'yuqoriga (bullish)', None: "aniq emas (range)"}


def fmt_ict_signal(s):
    sell = s['side'] == 'SELL'
    em = '🟥' if sell else '🟩'
    risk = abs(s['sl'] - s['entry'])
    why = [
        "• Killzone: %s" % s['kz'],
        "• Likvidlik: %s %.2f %s olindi (cho'qqi %.2f)" % (esc(KIND_UZ.get(s['level']['kind'], s['level']['kind'])),
                                                      s['level']['price'], 'ustidan' if sell else 'ostidan', s['sweep_ext']),
        "• MSS: %.2f %s displacement bilan yopildi%s" % (s['mss_level'], 'ostiga' if sell else 'ustiga',
                                                        ' (kuchli)' if s['strong'] else ''),
        "• FVG: %.2f - %.2f, kirish CE (o'rtasi)%s" % (s['ez']['lo'], s['ez']['hi'],
                                                       ', OTE bilan ustma-ust ✅' if s['in_ote'] else ''),
        "• OTE zona (0.62-0.79): %.2f - %.2f" % (s['ote']['lo'], s['ote']['hi']),
        "• H4 struktura %s" % ("mos ✅" if s['bias_ok'] else "mos emas ⚠️"),
        "• Midnight Open'ga nisbatan %s" % (("premium'da sotish ✅" if sell else "discount'da sotib olish ✅")
                                            if s['prem'] else "noqulay tomonda ⚠️"),
    ]
    L = ["%s <b>ICT %s XAUUSD</b>  |  ball %d/%d" % (em, s['side'], s['score'], ict.MAX_SCORE),
         "📍 Kirish (limit): <b>%.2f</b>" % s['entry'],
         "🛑 SL: <b>%.2f</b>  (xavf $%.2f)" % (s['sl'], risk),
         "🎯 TP: <b>%.2f</b>  (1:%.1f, %s)" % (s['tp'], s['rr'], esc(KIND_UZ.get(s['tpk'], s['tpk'])))]
    if s.get('tp2'):
        L.append("🎯 TP2: <b>%.2f</b>" % s['tp2'])
    L += ['', '<b>Nima uchun:</b>'] + why
    L += ['', "⏱ Sweep %s, MSS %s (Toshkent vaqti)" % (tkh(s['sweep_t']), tkh(s['mss_t'])),
          "❗️ Limit 3 soatda to'lmasa yoki narx avval TP ga yetsa, bekor qiling.",
          "⚠️ Kafolat yo'q. Bitta savdoda balansning 0.5-1% idan ko'p xavf qilmang."]
    return '\n'.join(L)


def _ict_plan(side, diag):
    w = diag.get(side.lower() + '_watch')
    if not w:
        return ''
    sell = side == 'SELL'
    return ("\n   ➜ sweep BO'LDI: %s %.2f %s (cho'qqi %.2f). M15 sham <b>%.2f</b> %s displacement bilan yopilsa MSS. Muddat: %d sham."
            % (KIND_UZ.get(w['level_kind'], w['level_kind']), w['level_price'], 'ustidan' if sell else 'ostidan',
               w['sweep_ext'], w['mss_level'], 'ostida' if sell else 'ustida', max(w['bars_left'], 0)))


def build_ict(c, src, price):
    """ICT tahlil matni. Qaytaradi: (matn, signal|None, ict_map|None, Prep, now)."""
    P = smc.Prep(c, ICT_CFG)
    now = len(c) - 1
    sig, diag = ict.find_signal(P, now, ICT_CFG)
    im = ict.ict_map(P, now, ICT_CFG)
    if im is None:
        return "ICT tahlil uchun ma'lumot yetarli emas.", None, None, P, now
    L = ["🕯 <b>XAUUSD ICT tahlil</b>", "Narx: <b>%.2f</b>  (%s, Toshkent)" % (price, tkh(time.time())), '']
    if im['kz_now']:
        L.append("⏰ Hozir killzone: <b>%s</b> ✅" % im['kz_now'])
    else:
        nz, nt = im['kz_next']
        L.append("⏰ Killzone emas. Keyingisi: <b>%s</b> %s da (Toshkent)" % (nz, tkh(nt)) if nz else "⏰ Killzone emas")
    L.append("📈 H4 struktura: <b>%s</b>" % BIAS_UZ[im['bias']])
    if im['mo'] is not None:
        L.append("🌙 Midnight Open: %.2f (narx %s, %+.1f$)" % (im['mo'], 'ustida' if price > im['mo'] else 'ostida', price - im['mo']))
    if im['eq'] is not None:
        L.append("⚖️ Kechagi diapazon bo'yicha: <b>%s</b> (o'rtasi %.2f)" % (im['pd_zone'], im['eq']))
    if im['asia']:
        L.append("🌏 Osiyo diapazoni: %.2f - %.2f" % (im['asia']['l'], im['asia']['h']))
    if im['london']:
        L.append("🇬🇧 London diapazoni: %.2f - %.2f" % (im['london']['l'], im['london']['h']))
    L.append('')
    if sig:
        L += ["🔔 <b>ICT SIGNAL BOR</b>", '', fmt_ict_signal(sig),
              "\n<i>Narx kirishdan hozir $%.2f uzoqda.</i>" % abs(sig['entry'] - price)]
    else:
        L.append("🎯 <b>ICT signal: hozircha yo'q</b>")
        for side, em in (('SELL', '🟥'), ('BUY', '🟩')):
            st = diag.get(side.lower(), 'no_sweep')
            txt = ICT_STAGE_UZ.get(st, st)
            if '%s' in txt:
                txt = txt % ('%.1f' % ICT_CFG['min_rr'])
            L.append("%s <b>%s</b>: %s%s" % (em, side, txt, _ict_plan(side, diag)))
    L += ['', "💧 <b>Likvidlik (ICT darajalari):</b>"]
    for lv in im['levels']:
        if abs(lv['price'] - price) > im['atr'] * 25:
            continue
        L.append("%s %s %.2f  (%+.1f$)%s" % ('⬆️' if lv['price'] > price else '⬇️', KIND_UZ.get(lv['kind'], lv['kind']),
                                            lv['price'], lv['price'] - price, '  olingan ✔︎' if lv['taken'] else ''))
    L += ['', "<i>Bu tahlil, kafolat emas. Manba: %s</i>" % esc(src)]
    return '\n'.join(L), sig, im, P, now


def chart_ict(c, im, sig):
    import _chart as chart
    levels, boxes, marks = [], [], []
    if im:
        if im['mo'] is not None:
            levels.append((im['mo'], 'Midnight Open', chart.COL['mo'], '--'))
        for lv in im['levels']:
            col = chart.COL['taken'] if lv['taken'] else chart.COL['liq']
            levels.append((lv['price'], lv['kind'], col, ':' if lv['taken'] else '-'))
        for key, name, col in (('asia', 'Osiyo', chart.COL['asia']), ('london', 'London', chart.COL['london'])):
            r = im.get(key)
            if r:
                boxes.append((c[r['first']]['t'], c[r['last']]['t'] + 900, r['l'], r['h'], name, col))
    if sig:
        boxes.append((c[sig['fvg_i'] - 2]['t'], c[-1]['t'] + 900 * 6, sig['ez']['lo'], sig['ez']['hi'], 'FVG', chart.COL['fvg']))
        boxes.append((sig['sweep_t'], c[-1]['t'] + 900 * 6, sig['ote']['lo'], sig['ote']['hi'], 'OTE', chart.COL['ote']))
        marks.append((sig['hi_t'], sig['sweep_ext'], 'sweep'))
        marks.append((sig['mss_t'], sig['mss_level'], 'MSS'))
    title = 'XAUUSD ICT' + (' | %s signal, ball %d/%d' % (sig['side'], sig['score'], ict.MAX_SCORE) if sig else '')
    note = 'Sariq: olinmagan likvidlik, kulrang: olingan. Ko\'k quti: Osiyo, havorang: London.'
    return chart.render(c, title=title, levels=levels, boxes=boxes, signal=sig, marks=marks, note=note)


def chart_smc(c, mp, sig):
    import _chart as chart
    zones, levels, boxes, marks = [], [], [], []
    if mp:
        for z in mp['all_zones']:
            zones.append((z['lo'], z['hi'], 'SNR x%d' % z['touches'], chart.COL['zone']))
        for lv in mp['levels']:
            levels.append((lv['price'], lv['kind'], chart.COL['liq'], '-'))
    if sig:
        boxes.append((sig['mss_t'] - 900 * 3, c[-1]['t'] + 900 * 6, sig['ez']['lo'], sig['ez']['hi'],
                      'FVG/OB', chart.COL['fvg']))
        marks.append((sig['sweep_t'], sig['sweep_ext'], 'sweep'))
        marks.append((sig['mss_t'], sig['mss_level'], 'MSS'))
    title = 'XAUUSD SMC | H4 yo\'nalish: %s' % (mp['bias'] if mp else '?')
    if sig:
        title += ' | %s signal, ball %d/%d' % (sig['side'], sig['score'], smc.MAX_SCORE)
    return chart.render(c, title=title, zones=zones, levels=levels, boxes=boxes, signal=sig, marks=marks,
                        note='Binafsha: SNR zonalar (H4), sariq: tegilmagan likvidlik.')


def _live_or_reply(chat_id):
    """Jonli ma'lumot; xato yoki bozor yopiq bo'lsa foydalanuvchiga yozib, None qaytaradi."""
    try:
        c, src, forming, price = load_live(3000)
    except Exception as e:  # noqa: BLE001
        log.exception('load_live')
        safe_send(chat_id, "⚠️ Narx ma'lumotini olib bo'lmadi: %s" % esc(e))
        return None
    if len(c) < 900:
        safe_send(chat_id, "⚠️ Tahlil uchun ma'lumot yetarli emas (%d sham)." % len(c))
        return None
    age_min = (time.time() - (c[-1]['t'] + 900)) / 60.0
    if age_min > 90:
        safe_send(chat_id, "⏸ Oltin bozori hozir yopiq (oxirgi sham %d daqiqa oldin, narx %.2f)." % (age_min, price))
        return None
    return c, src, forming, price


def _ict_reply(chat_id):
    live = _live_or_reply(chat_id)
    if not live:
        return
    c, src, _, price = live
    try:
        text, sig, im, _, _ = build_ict(c, src, price)
    except Exception as e:  # noqa: BLE001
        log.exception('ict')
        return safe_send(chat_id, "⚠️ ICT tahlil bajarilmadi: %s" % esc(e))
    if sig:
        store.save_signal(sig)
    safe_send(chat_id, text)
    try:
        send_photo(chat_id, chart_ict(c, im, sig), caption='🕯 ICT grafik (M15)')
    except Exception:  # noqa: BLE001
        log.exception('chart_ict')


def _chart_reply(chat_id):
    live = _live_or_reply(chat_id)
    if not live:
        return
    c, src, _, price = live
    try:
        P = smc.Prep(c, CFG)
        now = len(c) - 1
        sig, _ = smc.find_signal(P, now, CFG)
        mp = smc.market_map(P, now, CFG)
        png = chart_smc(c, mp, sig)
    except Exception as e:  # noqa: BLE001
        log.exception('chart')
        return safe_send(chat_id, "⚠️ Grafik chizilmadi: %s" % esc(e))
    cap = "🖼 <b>XAUUSD</b> %.2f | H4: %s | SMC signal: %s" % (price, mp['bias'] if mp else '?',
                                                            ('%s %.2f' % (sig['side'], sig['entry'])) if sig else "yo'q")
    send_photo(chat_id, png, caption=cap)


def _ict_context():
    """AI ga qo'shimcha ma'lumot: jonli narx va ICT darajalari (xato bo'lsa bo'sh)."""
    try:
        c, src, _, price = load_live(3000)
        P = smc.Prep(c, ICT_CFG)
        im = ict.ict_map(P, len(c) - 1, ICT_CFG)
        if not im:
            return ''
        parts = ['XAUUSD hozir %.2f (%s)' % (price, src), 'H4 struktura: %s' % BIAS_UZ[im['bias']]]
        if im['mo'] is not None:
            parts.append('Midnight Open %.2f' % im['mo'])
        parts += ['%s %.2f%s' % (lv['kind'], lv['price'], ' (olingan)' if lv['taken'] else '') for lv in im['levels']]
        return '; '.join(parts)
    except Exception:  # noqa: BLE001
        log.exception('ict_context')
        return ''


@bot.message_handler(commands=['ict'])
def cmd_ict(m):
    if guard(m):
        _ict_reply(m.chat.id)


@bot.message_handler(func=lambda m: bool(getattr(m, 'text', None)) and m.text.strip().lower().strip('!.?') in ('ict', 'ict signal', 'айсити'))
def txt_ict(m):
    if guard(m):
        _ict_reply(m.chat.id)


@bot.message_handler(commands=['rasm', 'chart', 'grafik'])
def cmd_chart(m):
    if guard(m):
        _chart_reply(m.chat.id)


@bot.message_handler(func=lambda m: bool(getattr(m, 'text', None)) and m.text.strip().lower().strip('!.?') in ('rasm', 'grafik', 'chart'))
def txt_chart(m):
    if guard(m):
        _chart_reply(m.chat.id)


@bot.message_handler(commands=['ictbacktest'])
def cmd_ict_backtest(m):
    if not guard(m):
        return
    safe_send(m.chat.id, "⏳ ICT modeli tarixiy ma'lumotda sinalmoqda...")
    try:
        c, src = load(5000)
        r = smc.backtest(c, ICT_CFG, finder=ict.find_signal)
    except Exception as e:  # noqa: BLE001
        log.exception('ictbacktest')
        return safe_send(m.chat.id, "⚠️ Backtest bajarilmadi: %s" % esc(e))
    safe_send(m.chat.id, fmt_backtest(r, src).replace('📊 <b>Backtest', '📊 <b>ICT backtest', 1))


# ----------------------------------------------------------------- Auto Fibo + SNR (alohida model)
TREND_UZ = {'up': 'yuqoriga 📈', 'down': 'pastga 📉', None: "aniq emas (range)"}
FIB_SHOW = (0.236, 0.382, 0.5, 0.618, 0.786)


def load_fibo():
    """M15 (jonli), M5 va M1 yopilgan shamlar. Bozor yopiq yoki xato bo'lsa RuntimeError."""
    c15, src, forming, price = load_live(3000)
    if len(c15) < 900:
        raise RuntimeError("tahlil uchun ma'lumot yetarli emas (%d sham)" % len(c15))
    age_min = (time.time() - (c15[-1]['t'] + 900)) / 60.0
    if age_min > 90:
        raise RuntimeError("⏸ Oltin bozori hozir yopiq (oxirgi sham %d daqiqa oldin, narx %.2f)." % (age_min, price))
    raw5, _ = data.fetch_tf(5, 600)
    raw1, _ = data.fetch_tf(1, 600)
    c5, c1 = data.drop_incomplete(raw5, step=300), data.drop_incomplete(raw1, step=60)
    if len(c5) < 100 or len(c1) < 100:
        raise RuntimeError("M5/M1 ma'lumoti yetarli emas (M5 %d, M1 %d sham)" % (len(c5), len(c1)))
    return {'c15': c15, 'c5': c5, 'c1': c1, 'src': src, 'price': raw1[-1]['c'] if raw1 else price}


def _fib_zones(c15):
    """SNR zonalar: H4 (asosiy) + M15 (lokal). Qaytaradi: (zonalar, H4 yo'nalish)."""
    P = smc.Prep(c15, CFG)
    mp = smc.market_map(P, len(c15) - 1, CFG)
    zones, bias = [], None
    if mp:
        zones = [dict(z, src='H4') for z in mp['all_zones']]
        bias = 'down' if mp['bias'] == 'pastga' else 'up'
    return zones + fibo.m15_zones(c15, FIBO_CFG), bias


def fibo_analyze(c15, c5, c1):
    f15 = fibo.analyze_tf(c15, 'M15', FIBO_CFG)
    f5 = fibo.analyze_tf(c5, 'M5', FIBO_CFG)
    f1 = fibo.analyze_tf(c1, 'M1', FIBO_CFG)
    zones, bias = _fib_zones(c15)
    sig, diag = fibo.find_signal(f15, f5, f1, zones, FIBO_CFG, h4_bias=bias)
    return f15, f5, f1, zones, bias, sig, diag


def _fmt_tf(f):
    L = ["<b>%s</b>: trend %s" % (f['tf'], TREND_UZ[f['trend']])]
    fb = f.get('fib')
    if not fb:
        L.append("   Fibo tortilmadi (aniq impuls yo'q)")
        return L
    up = fb['dir'] == 1
    L.append("   Fibo: %s %.2f ➜ %s %.2f  (oyoq $%.1f)" % ('low' if up else 'high', fb['A'], 'high' if up else 'low',
                                                     fb['B'], fb['R']))
    L.append('   ' + ' | '.join('%s: %.2f' % (('%g' % r), fb['levels'][r]) for r in FIB_SHOW))
    L.append("   Golden zone (0.5-0.786): <b>%.2f - %.2f</b>" % fb['gz'])
    if fb['depth'] is not None:
        L.append("   Qaytish: eng chuqur %.3f (%.2f), hozir %.3f" % (fb['depth'], fb['pl'], fb['retr']))
    else:
        L.append("   Narx hali impuls cho'qqisida, qaytish yo'q")
    return L


def fmt_fibo_signal(s):
    sell = s['side'] == 'SELL'
    em = '🟥' if sell else '🟩'
    z = s['zone']
    L = ["%s <b>FIBO %s XAUUSD</b>  |  ball %d/%d" % (em, s['side'], s['score'], fibo.MAX_SCORE),
         "📍 Kirish (hozir): <b>%.2f</b>" % s['entry'],
         "🛑 SL: <b>%.2f</b>  (xavf $%.2f)" % (s['sl'], s['risk']),
         "🎯 TP1: <b>%.2f</b>  (1:%.1f, %s)" % (s['tp'], s['rr'], esc(s['tp1k'])),
         "🎯 TP2: <b>%.2f</b>  (1:%.1f, Fibo 1.272)" % (s['tp2'], s['rr2']),
         "🎯 TP3: <b>%.2f</b>  (Fibo 1.618)" % s['tp3'],
         '', '<b>Nima uchun:</b>',
         "• M15 va M5 trend: %s" % ('pastga' if sell else 'yuqoriga'),
         "• M15 Fibo %.2f ➜ %.2f, narx %.3f gacha qaytdi (%.2f)" % (s['A'], s['B'], s['depth'], s['pl']),
         "• SNR (%s): %.2f - %.2f, %d marta qaytgan" % (z['src'], z['lo'], z['hi'], z['touches']),
         "• M1 da %s BOS bo'ldi (tasdiq)" % ('pastga' if sell else 'yuqoriga'),
         "• Chuqur qaytish (0.618+) yoki M5 golden zone: %s" % ('✅' if s['deep'] or s['m5_gz'] else '—'),
         "• H4 yo'nalish mos: %s" % ('✅' if s['bias_ok'] else '⚠️ mos emas'),
         '',
         "❗️ TP1 da yarmini yoping va SL ni kirishga suring.",
         "⚠️ Kafolat yo'q. Bitta savdoda balansning 0.5-1% idan ko'p xavf qilmang."]
    return '\n'.join(L)


def _fibo_plan(diag, f15, f5, f1, zones):
    st = diag.get('stage')
    fb = f15.get('fib')
    sell = diag.get('side') == 'SELL'
    if st == 'data':
        return "ma'lumot yetarli emas."
    if st == 'no_trend':
        return "M15 da aniq trend yo'q (range). Trend bo'lmasa Fibo tortilmaydi."
    if st == 'no_leg':
        return "M15 trend bor, lekin Fibo uchun yetarli impuls yo'q."
    if st == 'tf_conflict':
        return ("M15 %s, M5 %s: trendlar mos emas. M5 ham M15 tomonga burilishini kuting."
                % (TREND_UZ[f15['trend']], TREND_UZ[f5['trend']]))
    if st == 'not_in_zone':
        return ("narx M15 golden zone <b>%.2f - %.2f</b> ga qaytishini kuting (%s uchun)."
                % (fb['gz'][0], fb['gz'][1], 'SELL' if sell else 'BUY'))
    if st == 'too_deep':
        return ("qaytish 0.886 dan chuqur: impuls kuchsizlangan. %.2f buzilsa trend o'zgaradi."
                % fb['A'])
    if st == 'ran_away':
        return "narx zonadan qaytib ketgan, kirish kech. Yangi qaytishni kuting."
    if st == 'no_snr':
        glo, ghi = fb['gz']
        inz = [z for z in zones if z['hi'] >= glo and z['lo'] <= ghi]
        txt = ("qaytish nuqtasi %.2f da SNR zona yo'q, shuning uchun signal yo'q." % fb['pl'])
        if inz:
            txt += " Golden zone ichidagi SNR: " + ', '.join('%.2f-%.2f (%s)' % (z['lo'], z['hi'], z['src']) for z in inz[:3])
        return txt
    if st == 'no_trigger':
        z = diag.get('zone', {})
        lv = diag.get('m1_level')
        txt = ("narx Fibo + SNR zonasida (%.2f - %.2f, %s) ✅. Endi M1 da %s BOS kutiladi"
               % (z.get('lo', 0), z.get('hi', 0), z.get('src', ''), 'pastga' if sell else 'yuqoriga'))
        if lv:
            txt += ": M1 sham <b>%.2f</b> %s yopilsa tasdiq" % (lv['p'], 'ostida' if sell else 'ustida')
        return txt + '.'
    if st == 'risk_fail':
        return "SL masofasi mos kelmadi (juda yaqin yoki juda uzoq)."
    if st == 'rr_fail':
        return "TP gacha foyda/xavf nisbati yetarli emas (TP2 kamida 1:%.1f)." % FIBO_CFG['min_rr']
    if st == 'score_low':
        return "ball yetarli emas (%s/%d)." % (diag.get('score', '?'), fibo.MAX_SCORE)
    return st or ''


def build_fibo(c15, c5, c1, src, price):
    """Qaytaradi: (matn, signal|None, (f15, f5, f1, zones))."""
    f15, f5, f1, zones, bias, sig, diag = fibo_analyze(c15, c5, c1)
    L = ["📐 <b>XAUUSD Auto Fibo + SNR</b>", "Narx: <b>%.2f</b>  (%s, Toshkent)" % (price, tkh(time.time())),
         "H4 yo'nalish: %s" % TREND_UZ[bias], '']
    for f in (f15, f5, f1):
        L += _fmt_tf(f)
    L.append('')
    if sig:
        L += ["🔔 <b>FIBO SIGNAL BOR</b>", '', fmt_fibo_signal(sig)]
    else:
        L += ["🎯 <b>Fibo signal: hozircha yo'q</b>", "   ➜ " + _fibo_plan(diag, f15, f5, f1, zones)]
    near = sorted(zones, key=lambda z: abs((z['lo'] + z['hi']) / 2 - price))[:4]
    if near:
        L += ['', '🧱 <b>Eng yaqin SNR zonalar:</b>']
        for z in sorted(near, key=lambda z: -z['hi']):
            mark = '⬆️' if z['lo'] > price else ('⬇️' if z['hi'] < price else '➡️')
            L.append('%s %.2f - %.2f  (%s, %d marta)' % (mark, z['lo'], z['hi'], z['src'], z['touches']))
    L += ['', "<i>Fibo qoidalar asosida avtomatik tortiladi (bir xil shamlar = bir xil Fibo). "
              "Signal kafolat emas. Manba: %s</i>" % esc(src)]
    return '\n'.join(L), sig, (f15, f5, f1, zones)


def chart_fibo(c, f, zones, sig, step):
    import _chart as chart
    levels, boxes, marks, zs = [], [], [], []
    fb = f.get('fib')
    if fb:
        for r in fibo.LEVELS:
            col = chart.COL['gz'] if r in (0.5, 0.618, 0.786) else chart.COL['fib']
            levels.append((fb['levels'][r], '%g  %.2f' % (r, fb['levels'][r]), col, '-' if r in (0.0, 1.0) else '--'))
        for e in fibo.EXT:
            levels.append((fb['ext'][e], '%g  %.2f' % (e, fb['ext'][e]), chart.COL['ext'], ':'))
        boxes.append((fb['A_t'], c[-1]['t'] + step * 6, fb['gz'][0], fb['gz'][1], 'Golden zone', chart.COL['gz']))
        marks += [(fb['A_t'], fb['A'], 'A'), (fb['B_t'], fb['B'], 'B')]
    for z in zones:
        zs.append((z['lo'], z['hi'], 'SNR %s x%d' % (z['src'], z['touches']), chart.COL['zone']))
    title = 'XAUUSD %s Auto Fibo | trend: %s' % (f['tf'], {'up': 'yuqoriga', 'down': 'pastga'}.get(f['trend'], 'range'))
    if sig:
        title += ' | %s signal, ball %d/%d' % (sig['side'], sig['score'], fibo.MAX_SCORE)
    return chart.render(c, title=title, zones=zs, levels=levels, boxes=boxes, signal=sig, marks=marks,
                        note="Sariq: golden zone (0.5-0.786), yashil: TP kengaytmalari, binafsha: SNR.",
                        step=step, tf=f['tf'])


def _fibo_reply(chat_id):
    try:
        d = load_fibo()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if msg.startswith('⏸'):
            log.info('load_fibo: %s', msg)
        else:
            log.exception('load_fibo')
        return safe_send(chat_id, esc(msg) if msg.startswith('⏸') else "⚠️ Narx ma'lumotini olib bo'lmadi: %s" % esc(msg))
    try:
        text, sig, (f15, f5, _, zones) = build_fibo(d['c15'], d['c5'], d['c1'], d['src'], d['price'])
    except Exception as e:  # noqa: BLE001
        log.exception('fibo')
        return safe_send(chat_id, "⚠️ Fibo tahlil bajarilmadi: %s" % esc(e))
    if sig and store.claim(sig['key']):
        store.save_signal(sig)
    safe_send(chat_id, text)
    for c, f, step in ((d['c15'], f15, 900), (d['c5'], f5, 300)):
        try:
            near = sorted(zones, key=lambda z: abs((z['lo'] + z['hi']) / 2 - d['price']))[:6]
            send_photo(chat_id, chart_fibo(c, f, near, sig, step), caption='📐 %s Auto Fibo' % f['tf'])
        except Exception:  # noqa: BLE001
            log.exception('chart_fibo')


@bot.message_handler(commands=['fibo', 'fib'])
def cmd_fibo(m):
    if guard(m):
        _fibo_reply(m.chat.id)


@bot.message_handler(func=lambda m: bool(getattr(m, 'text', None)) and m.text.strip().lower().strip('!.?') in ('fibo', 'fib', 'фибо', 'fibo signal'))
def txt_fibo(m):
    if guard(m):
        _fibo_reply(m.chat.id)


def _image_bytes(m):
    """Xabardagi rasmni yuklab oladi (photo yoki rasm-fayl). Yo'q bo'lsa None."""
    if getattr(m, 'photo', None):
        fid = m.photo[-1].file_id
    elif getattr(m, 'document', None) and (m.document.mime_type or '').startswith('image/'):
        if (m.document.file_size or 0) > 8 * 1024 * 1024:
            raise RuntimeError('rasm juda katta (8 MB dan oshmasin)')
        fid = m.document.file_id
    else:
        return None
    f = bot.get_file(fid)
    return bot.download_file(f.file_path)


@bot.message_handler(content_types=['photo', 'document'])
def on_image(m):
    if not guard(m):
        return
    if not vision.enabled():
        return safe_send(m.chat.id, "🖼 Skrinshot tahlili o'chiq: Vercel'da <code>ANTHROPIC_API_KEY</code> o'rnatilmagan.\n"
                                    "Bot o'zi chizgan grafik uchun <b>rasm</b> yoki <b>ict</b> deb yozing.")
    try:
        img = _image_bytes(m)
    except Exception as e:  # noqa: BLE001
        log.exception('download')
        return safe_send(m.chat.id, "⚠️ Rasmni yuklab bo'lmadi: %s" % esc(e))
    if img is None:
        return safe_send(m.chat.id, "Faqat rasm (skrinshot) yuboring.")
    safe_send(m.chat.id, "🔍 Skrinshot ICT/SMC bo'yicha tahlil qilinmoqda, 10-30 soniya...")
    try:
        txt = vision.analyze(img, question=(m.caption or '').strip(), context=_ict_context())
    except Exception as e:  # noqa: BLE001
        log.exception('vision')
        return safe_send(m.chat.id, "⚠️ Tahlil bajarilmadi: %s" % esc(e))
    body = esc(txt)
    for i in range(0, len(body), 3800):
        safe_send(m.chat.id, ("🖼 <b>Skrinshot tahlili</b>\n\n" if i == 0 else '') + body[i:i + 3800]
                  + ("\n\n<i>AI tahlili, kafolat emas.</i>" if i + 3800 >= len(body) else ''))


# ----------------------------------------------------------------- HTTP
def _ok(secret):
    key = request.args.get('key', '') or request.headers.get('X-Cron-Key', '')
    return bool(secret) and hmac.compare_digest(key, secret)


def do_ulash():
    if not _ok(WEBHOOK_SECRET):
        return "Forbidden: kalit noto'g'ri", 403
    try:
        url = 'https://%s/api/index' % request.host
        ok = bot.set_webhook(url=url, secret_token=WEBHOOK_SECRET, allowed_updates=['message'])
        me = bot.get_me()
        return ('✅ Bot @%s %s manziliga ulandi. Telegramda /start yozing.' % (esc(me.username), esc(url)), 200) if ok \
            else ('❌ Webhook ulanmadi', 500)
    except Exception as e:  # noqa: BLE001
        log.exception('ulash')
        return 'XATOLIK: %s' % esc(e), 500


def do_scan():
    if not _ok(CRON_SECRET):
        return 'Forbidden', 403
    try:
        res, _, _ = run_scan(notify=True)
        return jsonify(res)
    except Exception as e:  # noqa: BLE001
        log.exception('scan')
        return jsonify({'ok': False, 'error': str(e)}), 500


def do_analyze():
    if not _ok(CRON_SECRET):
        return 'Forbidden', 403
    try:
        c, src, forming, price = load_live(3000)
        text, _ = build_analysis(c, src, forming, price)
        return jsonify({'ok': True, 'text': text})
    except Exception as e:  # noqa: BLE001
        log.exception('analyze')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def entry(path):
    if request.method == 'GET':
        if 'ulash' in request.args:
            return do_ulash()
        if 'scan' in request.args:
            return do_scan()
        if 'analyze' in request.args:
            return do_analyze()
        return 'XAUUSD signal bot ishlayapti.', 200
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not WEBHOOK_SECRET or not hmac.compare_digest(token, WEBHOOK_SECRET):
        return 'Forbidden', 403
    try:
        bot.process_new_updates([telebot.types.Update.de_json(request.get_data().decode('utf-8'))])
    except Exception:  # noqa: BLE001
        log.exception('update')
    return jsonify({'status': 'ok'}), 200
