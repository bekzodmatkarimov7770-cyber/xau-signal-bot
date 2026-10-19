"""XAUUSD shamlarini olish (M15 asosiy, Fibo uchun M1 va M5 ham).

Asosiy manba: Twelve Data (XAU/USD spot, bepul API kalit bilan).
Zaxira manba: Yahoo Finance GC=F (COMEX oltin fyucherslari, kalitsiz; narxi spotdan biroz farq qiladi).
Qaytadigan shakl: [{'t': epoch_sek, 'o':, 'h':, 'l':, 'c':}, ...] eskidan yangiga.
"""
import calendar
import os
import time

import requests

UA = {'User-Agent': 'Mozilla/5.0 (compatible; xau-signal-bot/1.0)'}


def parse_twelve(payload):
    if not isinstance(payload, dict) or payload.get('status') == 'error' or 'values' not in payload:
        raise RuntimeError('Twelve Data: %s' % (payload.get('message') if isinstance(payload, dict) else payload))
    out = []
    for v in payload['values']:
        t = calendar.timegm(time.strptime(v['datetime'][:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S'))
        out.append({'t': t, 'o': float(v['open']), 'h': float(v['high']),
                    'l': float(v['low']), 'c': float(v['close'])})
    out.sort(key=lambda x: x['t'])
    return out


def parse_yahoo(payload):
    try:
        res = payload['chart']['result'][0]
        ts, q = res['timestamp'], res['indicators']['quote'][0]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError('Yahoo: javob noto\'g\'ri')
    out = []
    for i, t in enumerate(ts):
        o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
        if None in (o, h, l, c):
            continue
        out.append({'t': int(t), 'o': float(o), 'h': float(h), 'l': float(l), 'c': float(c)})
    out.sort(key=lambda x: x['t'])
    return out


def clean(c):
    """Takroriy vaqtlarni olib tashlash va aniq xato (h<l va h.k.) shamlarni tuzatish."""
    seen, out = set(), []
    for b in c:
        if b['t'] in seen:
            continue
        seen.add(b['t'])
        hi, lo = max(b['h'], b['o'], b['c']), min(b['l'], b['o'], b['c'])
        out.append({'t': b['t'], 'o': b['o'], 'h': hi, 'l': lo, 'c': b['c']})
    return out


def drop_incomplete(c, now=None, step=900):
    """Hali yopilmagan oxirgi shamni olib tashlaydi. step: sham uzunligi (sekund)."""
    now = time.time() if now is None else now
    while c and c[-1]['t'] + step > now:
        c = c[:-1]
    return c


YAHOO_RANGE = {1: '7d', 5: '60d', 15: '60d'}


def fetch_tf(minutes, n=3000, timeout=15):
    """Istalgan taymfreym (1, 5 yoki 15 daqiqa): (shamlar, manba_nomi).
    Kalit bo'lmasa yoki xato bo'lsa, zaxira manbaga (Yahoo GC=F) o'tadi."""
    key = os.environ.get('TWELVE_API_KEY')
    err = None
    if key:
        try:
            r = requests.get('https://api.twelvedata.com/time_series', timeout=timeout, headers=UA,
                             params={'symbol': 'XAU/USD', 'interval': '%dmin' % minutes, 'outputsize': min(n, 5000),
                                     'timezone': 'UTC', 'order': 'ASC', 'apikey': key})
            return clean(parse_twelve(r.json()))[-n:], 'XAU/USD (Twelve Data)'
        except Exception as e:  # noqa: BLE001
            err = str(e)
    try:
        r = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/GC=F', timeout=timeout,
                         headers=UA, params={'interval': '%dm' % minutes, 'range': YAHOO_RANGE.get(minutes, '60d')})
        c = clean(parse_yahoo(r.json()))
        return c[-n:], 'GC=F (Yahoo, fyuchers)'
    except Exception as e:  # noqa: BLE001
        raise RuntimeError('Ma\'lumot olib bo\'lmadi. %s | Yahoo: %s' % (err or 'TWELVE_API_KEY yo\'q', e))


def fetch_m15(n=3000, timeout=15):
    """(shamlar, manba_nomi). Kalit bo'lmasa yoki xato bo'lsa, zaxira manbaga o'tadi."""
    return fetch_tf(15, n, timeout)
