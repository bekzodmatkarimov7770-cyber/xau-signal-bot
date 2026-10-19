"""XAUUSD: Auto Fibo (M15, M5, M1 trend bo'yicha) + SNR konfluens signal.

Toza Python. Faqat YOPILGAN shamlar bilan ishlaydi, kelajakka qaramaydi.
Bir xil shamlar berilsa, Fibo har doim aynan bir xil nuqtalarga tortiladi (qoidalar qat'iy).

Qanday ishlaydi (BUY uchun; SELL narxni ko'zguda aks ettirib xuddi shu kod bilan topiladi):
  1. Swing'lar : har taymfreymda fractal swing high/low (chap va o'ngda n sham), zigzag qilib
                 navbatma-navbat (high, low, high, ...) terib chiqiladi.
  2. Impuls    : oxirgi strukturani buzgan (BOS) oyoq. A = impuls boshlangan swing low (buzilmagan),
                 B = undan keyingi eng baland narx, oyoq kamida min_leg_atr ATR.
                 Xuddi shunday pastga impuls ham qidiriladi.
  3. Trend     : eng oxirgi impuls qarama-qarshi impulsning boshini (himoyalangan swing'ni) buzgan
                 bo'lsa, trend o'sha tomonga. Buzmagan bo'lsa, u katta oyoq ichidagi qaytish, trend
                 katta oyoq tomonida qoladi. Fibo trend tomonidagi impulsga tortiladi. Darajalar B dan A ga: 0.236 ... 1.0,
                 kengaytma (TP): 1.272 va 1.618.
  4. Signal    : M15 va M5 trendi bir xil (majburiy), M15 da narx 0.5-0.786 (golden zone + OTE)
                 ga qaytgan, shu qaytish nuqtasi SNR zonasida (H4 yoki M15), keyin M1 trend
                 qayta shu tomonga burilgan (M1 BOS: M1 impulsi qaytish
                 cho'qqisidan keyin boshlangan). Kirish: M1 yopilish narxi.
                 SL: qaytish cho'qqisi ortida. TP1: B (0 daraja), TP2: 1.272, TP3: 1.618.
"""
import _smc as smc

CFG = {
    'n': {'M1': 3, 'M5': 3, 'M15': 3},   # swing uchun chap/o'ngda nechta sham
    'lookback': 400,         # zigzag uchun nechta sham orqaga qaraladi
    'min_leg_atr': 2.0,      # Fibo oyog'i kamida shuncha ATR (shu taymfreym ATR'i)
    'gz_lo': 0.5,            # golden zone boshi
    'gz_hi': 0.786,          # golden zone oxiri (OTE chegarasi)
    'max_depth': 0.886,      # qaytish bundan chuqur bo'lsa, impuls kuchsizlangan (signal yo'q)
    'max_retr_entry': 0.382,  # narx zonadan qaytib, bundan yuqori ketsa, kirish kech (o'tkazib yuborilgan)
    'snr_n': 3,              # M15 SNR swing'lari
    'snr_lookback': 500,
    'snr_tol_atr': 0.3,
    'snr_min_touches': 2,
    'pad_atr': 0.5,          # zona chegarasi zaxirasi (M15 ATR)
    'sl_buf_atr': 0.15,
    'sl_min_buf': 0.30,
    'tp_buf_atr': 0.05,
    'min_risk_atr': 0.3,
    'max_risk_atr': 3.0,
    'min_rr': 2.0,           # TP2 (1.272) gacha minimal foyda/xavf
    'min_rr1': 1.0,          # TP1 (B) gacha minimal foyda/xavf
    'min_score': 7,
}
LEVELS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
EXT = (1.272, 1.618)
MAX_SCORE = 10
STEP = {'M1': 60, 'M5': 300, 'M15': 900}


def _arrays(c, sign):
    if sign == 1:
        return [b['h'] for b in c], [b['l'] for b in c], [b['c'] for b in c]
    return [-b['l'] for b in c], [-b['h'] for b in c], [-b['c'] for b in c]


def zigzag(H, L, n, start=0):
    """Fractal swing'lardan navbatma-navbat zigzag: [(indeks, narx, +1 high | -1 low)]."""
    hi, lo = smc.pivots(H, L, n)
    pts = [(i, p, 1) for i, p in hi if i >= start] + [(i, p, -1) for i, p in lo if i >= start]
    pts.sort(key=lambda x: (x[0], x[2]))
    zz = []
    for i, p, k in pts:
        if zz and zz[-1][2] == k:
            if (k == 1 and p > zz[-1][1]) or (k == -1 and p < zz[-1][1]):
                zz[-1] = (i, p, k)
            continue
        zz.append((i, p, k))
    return zz


def _leg_up(H, L, zz, min_leg):
    """Yuqoriga impuls oyog'i (ko'zgu ko'rinishida pastga uchun ham ishlaydi): (Ai, Ap, Bi, Bp) | None.

    Nomzod: zigzag low (A), undan keyin narx A ostiga tushmagan, A dan keyingi eng baland narx (B)
    A dan oldingi swing high'dan yuqori (BOS) va oyoq kamida min_leg. Nomzodlardan B eng baland
    bo'lgani olinadi (asosiy impuls, ichki kichik sakrash emas); bir xil B ga olib kelgan A lardan
    eng yaqini (eng oxirgisi) olinadi."""
    N = len(H)
    best = None
    for j in range(len(zz) - 1, -1, -1):
        ai, ap, k = zz[j]
        if k != -1 or ai + 1 >= N:
            continue
        if min(L[ai + 1:]) < ap:
            continue                         # A buzilgan
        bi = max(range(ai + 1, N), key=lambda q: (H[q], -q))
        bp = H[bi]
        prev_hi = next((zz[q][1] for q in range(j - 1, -1, -1) if zz[q][2] == 1), None)
        if prev_hi is not None and bp <= prev_hi:
            continue                         # bu low dan keyin struktura buzilmagan (oddiy qaytish)
        if bp - ap < min_leg:
            continue
        if best is None or bp > best[3]:
            best = (ai, ap, bi, bp)
    return best


def analyze_tf(c, tf, cfg=CFG):
    """Bitta taymfreym: trend va avtomatik Fibo. c: yopilgan shamlar (eskidan yangiga)."""
    N = len(c)
    out = {'tf': tf, 'trend': None, 'fib': None, 'bos': {}, 'price': c[-1]['c'] if c else None}
    n = cfg['n'][tf]
    if N < max(60, 4 * n + 10):
        return out
    H, L, C = _arrays(c, 1)
    a = smc._atr(H, L, C, N - 1)
    out['atr'] = a
    start = max(0, N - cfg['lookback'])
    zz = zigzag(H, L, n, start)
    hs = [(i, p) for i, p, k in zz if k == 1]
    ls = [(i, p) for i, p, k in zz if k == -1]
    out['last_hi'] = {'p': hs[-1][1], 't': c[hs[-1][0]]['t']} if hs else None
    out['last_lo'] = {'p': ls[-1][1], 't': c[ls[-1][0]]['t']} if ls else None
    if a <= 0:
        return out
    legs = {}
    for d in (1, -1):
        Hs, Ls, _ = _arrays(c, d)
        zzs = zz if d == 1 else [(i, -p, -k) for i, p, k in zz]
        lg = _leg_up(Hs, Ls, zzs, cfg['min_leg_atr'] * a)
        if lg:
            legs[d] = lg
    out['bos'] = {('up' if k == 1 else 'down'): {'A': k * v[1], 'B': k * v[3], 'A_t': c[v[0]]['t'], 'B_t': c[v[2]]['t']}
                  for k, v in legs.items()}
    if not legs:
        return out
    if len(legs) == 1:
        d = next(iter(legs))
    else:
        # Eng oxirgi impuls (B vaqti keyinroq) qarama-qarshi oyoqning boshini (A) buzgan bo'lsa,
        # trend o'zgargan. Buzmagan bo'lsa, bu shunchaki katta oyoq ichidagi qaytish.
        new_d = 1 if legs[1][2] > legs[-1][2] else -1
        old = legs[-new_d]
        d = new_d if legs[new_d][3] > -old[1] else -new_d
    out['trend'] = 'up' if d == 1 else 'down'
    Hs, Ls, Cs = _arrays(c, d)
    leg = legs[d]
    ai, ap, bi, bp = leg
    A, B = d * ap, d * bp
    R = bp - ap
    lv = {r: B - d * r * R for r in LEVELS}
    ext = {e: A + d * e * R for e in EXT}
    retr = (bp - Cs[-1]) / R
    pl = pl_t = pl_i = depth = None
    if bi + 1 < N:
        pi = min(range(bi + 1, N), key=lambda q: (Ls[q], -q))
        pl, pl_i, pl_t = d * Ls[pi], pi, c[pi]['t']
        depth = (bp - Ls[pi]) / R
    out['fib'] = {'dir': d, 'A': A, 'B': B, 'A_t': c[ai]['t'], 'B_t': c[bi]['t'], 'A_i': ai, 'B_i': bi,
                  'R': R, 'levels': lv, 'ext': ext, 'retr': retr, 'pl': pl, 'pl_t': pl_t, 'pl_i': pl_i,
                  'depth': depth,
                  'gz': (min(lv[cfg['gz_lo']], lv[cfg['gz_hi']]), max(lv[cfg['gz_lo']], lv[cfg['gz_hi']]))}
    return out


def m15_zones(c, cfg=CFG):
    """M15 dagi lokal SNR zonalar (narx kamida 2 marta qaytgan joy)."""
    N = len(c)
    if N < 60:
        return []
    H, L, C = _arrays(c, 1)
    a = smc._atr(H, L, C, N - 1)
    s = max(0, N - cfg['snr_lookback'])
    hi, lo = smc.pivots(H, L, cfg['snr_n'])
    piv = sorted([x for x in hi + lo if x[0] >= s], key=lambda x: x[1])
    gap = a * cfg['snr_tol_atr']
    out = []
    for cl in smc._cluster(piv, gap, gap * 1.5):
        if len(cl) >= cfg['snr_min_touches']:
            ps = [x[1] for x in cl]
            out.append({'lo': min(ps), 'hi': max(ps), 'touches': len(cl), 'src': 'M15'})
    return out


def _side(d):
    return 'BUY' if d == 1 else 'SELL'


def find_signal(f15, f5, f1, zones, cfg=CFG, h4_bias=None, entry_price=None):
    """Fibo + SNR signal. f15/f5/f1: analyze_tf natijalari; zones: [{'lo','hi','touches','src'}].
    h4_bias: 'up' | 'down' | None. Qaytaradi: (signal | None, diag)."""
    diag = {'stage': 'data'}
    if not f15.get('fib') and f15.get('trend') is None:
        diag['stage'] = 'no_trend' if f15.get('atr') else 'data'
        return None, diag
    fb = f15.get('fib')
    if fb is None:
        diag['stage'] = 'no_leg'
        return None, diag
    d = fb['dir']
    diag.update(side=_side(d), gz=fb['gz'])
    if f5.get('trend') != f15['trend']:
        diag['stage'] = 'tf_conflict'
        return None, diag
    if fb['pl'] is None or fb['depth'] < cfg['gz_lo']:
        diag['stage'] = 'not_in_zone'
        return None, diag
    if fb['depth'] > cfg['max_depth']:
        diag['stage'] = 'too_deep'
        return None, diag
    if fb['retr'] < cfg['max_retr_entry']:
        diag['stage'] = 'ran_away'
        return None, diag
    a = f15['atr']
    pad = cfg['pad_atr'] * a
    glo, ghi = fb['gz']
    pl = fb['pl']
    zone = None
    for z in zones:
        if z['lo'] - pad <= pl <= z['hi'] + pad and z['hi'] + pad >= glo and z['lo'] - pad <= ghi:
            if zone is None or (z['src'] == 'H4', z['touches']) > (zone['src'] == 'H4', zone['touches']):
                zone = z
    if zone is None:
        diag['stage'] = 'no_snr'
        return None, diag
    diag['zone'] = zone
    f1b = f1.get('bos', {}).get(f15['trend'])        # M1 da shu tomonga ichki BOS (tasdiq)
    if not f1b or f1b['A_t'] < fb['pl_t']:
        diag['stage'] = 'no_trigger'
        diag['m1_level'] = (f1.get('last_hi') if d == 1 else f1.get('last_lo'))
        return None, diag
    entry = f1['price'] if entry_price is None else entry_price
    if not ((entry - pl) * d > 0 and (fb['B'] - entry) * d > 0):
        diag['stage'] = 'ran_away'
        return None, diag
    sl = pl - d * max(cfg['sl_buf_atr'] * a, cfg['sl_min_buf'])
    risk = (entry - sl) * d
    if not (cfg['min_risk_atr'] * a <= risk <= cfg['max_risk_atr'] * a):
        diag['stage'] = 'risk_fail'
        return None, diag
    tp1, tp1k = fb['B'] - d * cfg['tp_buf_atr'] * a, 'Fibo 0 (B)'
    opp = [z for z in zones if z is not zone and
           (((z['lo'] - entry) * d > 0.3 * risk and (tp1 - z['lo']) * d > 0) if d == 1 else
            ((entry - z['hi']) > 0.3 * risk and (z['hi'] - tp1) > 0))]
    if opp:
        near = min(opp, key=lambda z: abs((z['lo'] if d == 1 else z['hi']) - entry))
        tp1, tp1k = (near['lo'] if d == 1 else near['hi']) - d * cfg['tp_buf_atr'] * a, 'SNR zona'
    tp2, tp3 = fb['ext'][1.272], fb['ext'][1.618]
    rr1, rr2 = (tp1 - entry) * d / risk, (tp2 - entry) * d / risk
    if rr1 < cfg['min_rr1'] or rr2 < cfg['min_rr']:
        diag['stage'] = 'rr_fail'
        return None, diag
    f5b = f5.get('fib')
    m5_gz = bool(f5b and f5b['dir'] == d and f5b['gz'][0] - pad <= pl <= f5b['gz'][1] + pad)
    deep = fb['depth'] >= 0.618
    bias_ok = h4_bias == f15['trend']
    score = (6 + (1 if zone['src'] == 'H4' else 0) + (1 if zone['touches'] >= 3 else 0)
             + (1 if deep or m5_gz else 0) + (1 if bias_ok else 0))
    diag['score'] = score
    if score < cfg['min_score']:
        diag['stage'] = 'score_low'
        return None, diag
    diag['stage'] = 'ok'
    sig = {'side': _side(d), 'key': 'FIBO:%s:%d:%d' % (_side(d), fb['A_t'], fb['B_t']),
           'entry': entry, 'sl': sl, 'tp': tp1, 'tp1k': tp1k, 'tp2': tp2, 'tp3': tp3, 'rr': rr1, 'rr2': rr2,
           'score': score, 'risk': risk, 'zone': dict(zone), 'pl': pl, 'pl_t': fb['pl_t'], 'depth': fb['depth'],
           'A': fb['A'], 'B': fb['B'], 'm5_gz': m5_gz, 'deep': deep, 'bias_ok': bias_ok}
    return sig, diag
