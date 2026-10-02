"""XAUUSD signal bot (SMC + Liquidity Sweep + Classic SNR). Vercel + Flask + Telegram.

Muhit o'zgaruvchilari (Vercel > Settings > Environment Variables):
  BOT_TOKEN        - @BotFather dan olingan bot tokeni (MAJBURIY)
  ADMIN_ID         - signal keladigan Telegram ID (MAJBURIY)
  WEBHOOK_SECRET   - istalgan uzun tasodifiy satr, faqat A-Z a-z 0-9 _ - (MAJBURIY)
  TWELVE_API_KEY   - twelvedata.com bepul kaliti (tavsiya; bo'lmasa Yahoo GC=F ishlatiladi)
  CRON_SECRET      - /scan uchun kalit (bo'lmasa WEBHOOK_SECRET ishlatiladi)
  MIN_SCORE, MIN_RR, ENTRY_TIMEOUT - ixtiyoriy sozlamalar
  UPSTASH_REDIS_REST_URL / _TOKEN yoki KV_REST_API_URL / _TOKEN - ixtiyoriy doimiy baza
"""
import hmac
import html
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telebot  # noqa: E402
from flask import Flask, jsonify, request  # noqa: E402

import _data as data  # noqa: E402
import _smc as smc  # noqa: E402
import _store as store  # noqa: E402

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

bot = telebot.TeleBot(BOT_TOKEN or '0:missing', threaded=False)
app = Flask(__name__)

KIND_UZ = {'PDH': 'kecha high', 'PDL': 'kecha low', 'PWH': "o'tgan hafta high", 'PWL': "o'tgan hafta low",
           'ASH': 'Osiyo sessiyasi high', 'ASL': 'Osiyo sessiyasi low',
           'EQH': 'teng yuqorilar (EQH)', 'EQL': 'teng pastlar (EQL)', 'SNR': 'SNR zona', 'RR': 'RR chegarasi'}
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
    res = sorted([z for z in mp['zones'] if z['lo'] > p], key=lambda z: z['lo'])[:2]
    sup = sorted([z for z in mp['zones'] if z['hi'] < p], key=lambda z: -z['hi'])[:2]
    inside = [z for z in mp['zones'] if z['lo'] <= p <= z['hi']]
    L += ['', '🧱 <b>Eng yaqin SNR zonalar:</b>']
    for z in reversed(res):
        L.append('⬆️ qarshilik %.2f - %.2f  (+%.1f$, %d marta)' % (z['lo'], z['hi'], z['lo'] - p, z['touches']))
    for z in inside:
        L.append('➡️ narx zona ichida: %.2f - %.2f (%d marta)' % (z['lo'], z['hi'], z['touches']))
    for z in sup:
        L.append('⬇️ qo\'llab-quvvat %.2f - %.2f  (-%.1f$, %d marta)' % (z['lo'], z['hi'], p - z['hi'], z['touches']))
    if not (res or sup or inside):
        L.append("yaqin zona topilmadi")
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
              "<b>XAUUSD signal bot</b>\nSMC + Liquidity Sweep + Classic SNR\n\n"
              "<b>signal</b> deb yozing: bozor shu zahoti jonli narx bilan tahlil qilinadi\n"
              "/zones - narx atrofidagi SNR zonalar va likvidlik\n"
              "/backtest - strategiyani tarixiy ma'lumotda sinash\n"
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
