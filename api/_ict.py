"""XAUUSD: ICT (Inner Circle Trader) signal mexanizmi. SMC mexanizmidan alohida ishlaydi.

Toza Python. Faqat YOPILGAN shamlar bilan ishlaydi, kelajakka qaramaydi (look-ahead yo'q).
Vaqtlar ICT an'anasiga ko'ra Nyu-York vaqti bilan hisoblanadi (yozgi/qishki vaqt hisobga olinadi).

Model (SELL uchun; BUY narxni ko'zguda aks ettirib xuddi shu kod bilan topiladi):
  1. Killzone : sweep London (02:00-05:00 NY) yoki Nyu-York AM (07:00-11:00 NY, Silver Bullet
                10:00-11:00 ham shu ichida) oynasida bo'lishi shart.
  2. Likvidlik: Osiyo diapazoni high'i (20:00-00:00 NY), kechagi kun high'i (PDH),
                NY sessiyasida esa London high'i ham.
  3. Sweep    : narx daraja ustiga chiqadi (Judas swing), keyin qaytadi.
  4. MSS      : sweep cho'qqisidan oldingi swing-low displacement bilan pastga yopilib buziladi.
  5. FVG      : shu displacement oyog'ida Fair Value Gap qolishi SHART. Kirish = FVG o'rtasi (CE).
  6. SL       : sweep cho'qqisi ustida. TP: qarama-qarshi likvidlik (Osiyo low, PDL, ...), kamida 1:2.
  Qo'shimcha ball: OTE (0.62-0.79 retracement) bilan ustma-ust tushish, kuchli displacement,
  H4 struktura yo'nalishi, Midnight Open'dan yuqorida sotish (premium), tashqi likvidlik.
"""
import bisect
import calendar
import time

import _smc as smc

CFG = {**smc.CFG,
       'kz_mss_extra': 8,       # sweep killzone ichida, MSS undan keyin shuncha sham ichida bo'lishi mumkin
       'mss_max_bars': 12,      # sweepdan MSS gacha maksimal sham (3 soat)
       'max_sweep_atr': 3.0,    # sweep bundan chuqur bo'lsa breakout hisoblanadi
       'disp_atr': 1.0,         # displacement shami tanasi kamida shuncha ATR
       'disp_strong_atr': 1.6,  # kuchli displacement (qo'shimcha ball)
       'fvg_min_atr': 0.15,
       'min_rr': 2.0,
       'rr_cap': 5.0,
       'min_score': 7,
       'entry_timeout': 12,
       'max_hold': 64}
MAX_SCORE = 10
RANK = {'no_sweep': 0, 'sweep_no_mss': 1, 'no_fvg': 2, 'filled': 3, 'risk_fail': 4,
        'rr_fail': 5, 'score_low': 6, 'ok': 7}
KZ = (('London', 2.0, 5.0), ('NY AM', 7.0, 10.0), ('Silver Bullet', 10.0, 11.0))
M15 = 900


# ----------------------------------------------------------------- Nyu-York vaqti
def _nth_sunday(y, m, n):
    wd = calendar.weekday(y, m, 1)                # 0=dushanba, 6=yakshanba
    return 1 + (6 - wd) % 7 + 7 * (n - 1)


_DST = {}


def ny_off(t):
    """Nyu-York UTC farqi (soat): yozda -4, qishda -5."""
    y = time.gmtime(t).tm_year
    if y not in _DST:
        a = calendar.timegm((y, 3, _nth_sunday(y, 3, 2), 7, 0, 0))
        b = calendar.timegm((y, 11, _nth_sunday(y, 11, 1), 6, 0, 0))
        _DST[y] = (a, b)
    a, b = _DST[y]
    return -4 if a <= t < b else -5


def ny_hour(t):
    s = (t + ny_off(t) * 3600) % 86400
    return s / 3600.0


def ny_day(t):
    return (t + ny_off(t) * 3600) // 86400


def killzone(t):
    h = ny_hour(t)
    for name, a, b in KZ:
        if a <= h < b:
            return name
    return None


def next_killzone(t):
    """(nomi, boshlanish epoch): hozirgidan keyin boshlanadigan eng yaqin killzone."""
    t0 = t - t % M15
    for step in range(1, 48 * 4):
        tt = t0 + step * M15
        z = killzone(tt)
        if z and killzone(tt - M15) != z:
            return z, tt
    return None, None


# ----------------------------------------------------------------- tayyorlash (Prep ga biriktiriladi)
class _Days:
    def __init__(self, c):
        self.day = []          # har bir sham uchun NY kuni
        self.stat = {}         # kun -> {'h','l','o','c','n','first','last'}
        self.asia = {}         # kun -> Osiyo diapazoni (oldingi kechqurun 20:00-00:00 NY)
        self.london = {}       # kun -> London sessiyasi (02:00-05:00 NY)
        for i, b in enumerate(c):
            t = b['t']
            d, h = ny_day(t), ny_hour(t)
            self.day.append(d)
            self._upd(self.stat, d, b, i)
            if h >= 20.0:
                self._upd(self.asia, d + 1, b, i)
            elif 2.0 <= h < 5.0:
                self._upd(self.london, d, b, i)
        self.days = sorted(k for k, e in self.stat.items() if e['n'] >= 40)

    @staticmethod
    def _upd(st, key, b, i):
        e = st.get(key)
        if e is None:
            st[key] = {'h': b['h'], 'l': b['l'], 'o': b['o'], 'c': b['c'], 'n': 1, 'first': i, 'last': i,
                       'hi_i': i, 'lo_i': i}
        else:
            if b['h'] > e['h']:
                e['h'], e['hi_i'] = b['h'], i
            if b['l'] < e['l']:
                e['l'], e['lo_i'] = b['l'], i
            e['c'] = b['c']
            e['n'] += 1
            e['last'] = i


def _days(P):
    d = getattr(P, '_ict_days', None)
    if d is None:
        d = _Days(P.c)
        P._ict_days = d
    return d


def _h4_bias(P, k):
    """H4 struktura: 'down', 'up' yoki None. Oxirgi ikki tasdiqlangan swing high/low bo'yicha."""
    n = P.cfg['htf_n']
    hs = [x for x in P.h4_hi if x[0] <= k - 1 - n][-2:]
    ls = [x for x in P.h4_lo if x[0] <= k - 1 - n][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return None
    c = P.h4C[k - 1]
    if c < ls[-1][1]:
        return 'down'
    if c > hs[-1][1]:
        return 'up'
    if hs[-1][1] < hs[-2][1] and ls[-1][1] < ls[-2][1]:
        return 'down'
    if hs[-1][1] > hs[-2][1] and ls[-1][1] > ls[-2][1]:
        return 'up'
    return None


def _ctx(P, now, cfg):
    T, V = P.T, P.v[1]
    k = bisect.bisect_right(P.h4_end, T[now] + M15)
    if k < cfg['min_h4'] or now < 60:
        return None
    a = smc._atr(V.H, V.L, V.C, now)
    if a <= 0:
        return None
    D = _days(P)
    d = D.day[now]
    st = D.stat.get(d)
    mo = st['o'] if st else None
    lv = []
    asia = D.asia.get(d)
    if asia and asia['n'] >= 8:
        lv.append({'price': asia['h'], 'side': 'high', 'kind': 'ASH', 'f': asia['hi_i'], 'ext': 1})
        lv.append({'price': asia['l'], 'side': 'low', 'kind': 'ASL', 'f': asia['lo_i'], 'ext': 1})
    pos = bisect.bisect_left(D.days, d) - 1
    if pos >= 0:
        e = D.stat[D.days[pos]]
        lv.append({'price': e['h'], 'side': 'high', 'kind': 'PDH', 'f': e['hi_i'], 'ext': 1})
        lv.append({'price': e['l'], 'side': 'low', 'kind': 'PDL', 'f': e['lo_i'], 'ext': 1})
    ld = D.london.get(d)
    if ld and ld['n'] >= 8 and ny_hour(T[now]) >= 5.0:
        lv.append({'price': ld['h'], 'side': 'high', 'kind': 'LOH', 'f': ld['hi_i'], 'ext': 0})
        lv.append({'price': ld['l'], 'side': 'low', 'kind': 'LOL', 'f': ld['lo_i'], 'ext': 0})
    # TP uchun qo'shimcha nishonlar (SMC likvidligi: o'tgan hafta, teng high/low)
    tp_extra = [x for x in smc._levels(P, now, a, P.cfg) if x['kind'] in ('PWH', 'PWL', 'EQH', 'EQL')]
    return {'a': a, 'k': k, 'levels': lv, 'tp_extra': tp_extra, 'mo': mo, 'bias': _h4_bias(P, k),
            'day': d, 'asia': asia, 'london': ld, 'pd': D.stat[D.days[pos]] if pos >= 0 else None}


def _mirror(lv):
    return [{**x, 'price': -x['price'], 'side': 'low' if x['side'] == 'high' else 'high'} for x in lv]


# ----------------------------------------------------------------- asosiy qidiruv (SELL ko'rinishida)
def _scan_dir(P, V, now, levels, targets, mo, a, bias_ok, cfg, watch=None):
    T = P.T
    O, H, L, C = V.O, V.H, V.L, V.C
    stage = 'no_sweep'

    def up(s):
        nonlocal stage
        if RANK[s] > RANK[stage]:
            stage = s

    j_min = max(0, now - cfg['mss_max_bars'] - cfg['kz_mss_extra'])
    best = None
    for lev in levels:
        if lev['side'] != 'high':
            continue
        p, start = lev['price'], lev['f'] + 1
        if start > now - 2:
            continue
        mid = max(start, j_min)
        if mid > start and max(H[start:mid]) > p:
            continue                                  # daraja avval olingan
        j = None
        for q in range(mid, now):
            if H[q] > p:
                j = q
                break
        if j is None or killzone(T[j]) is None:
            continue
        up('sweep_no_mss')
        # sweep cho'qqisi: j dan keyingi eng baland nuqta (MSS gacha)
        hi_i, sw_hi, m = j, H[j], None
        piv = None
        for q in range(j, min(now, j + cfg['mss_max_bars']) + 1):
            if H[q] > sw_hi:
                hi_i, sw_hi = q, H[q]
            if sw_hi - p > cfg['max_sweep_atr'] * a:
                break                                 # juda chuqur: bu breakout
            pi = bisect.bisect_left(V.pm_lo_idx, hi_i) - 1
            cand = None
            while pi >= 0:
                i_, p_ = V.pm_lo[pi]
                if i_ + cfg['swing_n'] <= q:
                    if i_ >= j - 40:
                        cand = (i_, p_)
                    break
                pi -= 1
            if cand and q > hi_i and C[q] < cand[1]:
                m, piv = q, cand
                break
        if sw_hi - p > cfg['max_sweep_atr'] * a:
            continue
        if m is None:
            if watch is not None and j + cfg['mss_max_bars'] >= now and j > watch.get('j', -1):
                pi = bisect.bisect_left(V.pm_lo_idx, hi_i) - 1
                while pi >= 0 and V.pm_lo[pi][0] + cfg['swing_n'] > now:
                    pi -= 1
                if pi >= 0 and V.pm_lo[pi][0] >= j - 40:
                    watch.update({'j': j, 'level_kind': lev['kind'], 'level_price': p, 'sweep_ext': sw_hi,
                                  'mss_level': V.pm_lo[pi][1], 'bars_left': j + cfg['mss_max_bars'] - now})
            continue
        if m < now - 1:
            continue
        # displacement
        body = max(O[q] - C[q] for q in range(hi_i, m + 1))
        if body < cfg['disp_atr'] * a:
            continue
        up('no_fvg')
        fvgs = []
        for q in range(max(hi_i + 2, 2), min(now, m + 1) + 1):
            top, bot = L[q - 2], H[q]
            if bot < top and top - bot >= cfg['fvg_min_atr'] * a:
                fvgs.append((bot, top, q))
        if not fvgs:
            continue
        leg_lo = min(L[hi_i:now + 1])
        rng = sw_hi - leg_lo
        ote = (leg_lo + 0.62 * rng, leg_lo + 0.79 * rng)
        fv = None
        for f in fvgs:
            if f[0] <= ote[1] and f[1] >= ote[0]:
                fv = f
                break
        in_ote = fv is not None
        fv = fv or fvgs[0]
        entry = (fv[0] + fv[1]) / 2.0
        if fv[2] < now and max(H[fv[2] + 1:now + 1]) >= entry:
            up('filled')
            continue                                  # narx kirishga allaqachon qaytgan
        up('risk_fail')
        sl = sw_hi + max(cfg['sl_buf_atr'] * a, cfg['sl_min_buf'])
        risk = sl - entry
        if not (cfg['min_risk_atr'] * a <= risk <= cfg['max_risk_atr'] * a):
            continue
        up('rr_fail')
        tg = [(x['price'] + cfg['tp_buf_atr'] * a, x['kind']) for x in targets
              if x['side'] == 'low' and x['price'] < entry - 0.5 * risk and x['price'] < leg_lo + 0.01]
        tg.sort(key=lambda x: -x[0])
        sel = [x for x in tg if (entry - x[0]) / risk >= cfg['min_rr']]
        if not sel:
            continue
        tp, tpk = sel[0]
        rr = (entry - tp) / risk
        if rr > cfg['rr_cap']:
            tp, rr, tpk = entry - cfg['rr_cap'] * risk, cfg['rr_cap'], 'RR'
        tp2 = sel[1][0] if len(sel) > 1 and sel[1][0] < tp - 0.25 * risk else None
        strong = body >= cfg['disp_strong_atr'] * a
        prem = mo is not None and entry > mo
        score = 5 + int(strong) + int(in_ote) + int(bias_ok) + int(prem) + int(lev['ext'])
        up('score_low')
        if score < cfg['min_score']:
            continue
        up('ok')
        cand = {'entry': entry, 'sl': sl, 'tp': tp, 'tp2': tp2, 'tpk': tpk, 'rr': rr, 'score': score,
                'level': dict(lev), 'fvg': (fv[0], fv[1]), 'fvg_i': fv[2], 'ote': ote, 'in_ote': in_ote,
                'strong': strong, 'prem': prem, 'bias_ok': bias_ok, 'kz': killzone(T[j]),
                'sweep_i': j, 'hi_i': hi_i, 'sweep_ext': sw_hi, 'mss_i': m, 'mss_level': piv[1], 'risk': risk}
        if best is None or (score, j) > (best['score'], best['sweep_i']):
            best = cand
    return best, stage


def _finish(c, s, name, P):
    T = P.T
    f = (lambda x: x) if s == 1 else (lambda x: -x)
    pair = (lambda lo, hi: (lo, hi)) if s == 1 else (lambda lo, hi: (-hi, -lo))
    flo, fhi = pair(*c['fvg'])
    olo, ohi = pair(*c['ote'])
    lev = c['level']
    return {
        'strategy': 'ICT', 'side': name, 'key': 'ICT:%s:%s' % (name, T[c['sweep_i']]),
        'entry': f(c['entry']), 'sl': f(c['sl']), 'tp': f(c['tp']),
        'tp2': None if c['tp2'] is None else f(c['tp2']), 'tpk': c['tpk'],
        'rr': c['rr'], 'score': c['score'], 'risk': c['risk'],
        'level': {'kind': lev['kind'], 'price': f(lev['price']), 'grade': lev['ext']},
        'ez': {'lo': flo, 'hi': fhi}, 'ote': {'lo': olo, 'hi': ohi}, 'in_ote': c['in_ote'],
        'strong': c['strong'], 'prem': c['prem'], 'bias_ok': c['bias_ok'], 'kz': c['kz'],
        'sweep_t': T[c['sweep_i']], 'sweep_i': c['sweep_i'], 'sweep_ext': f(c['sweep_ext']), 'hi_t': T[c['hi_i']],
        'mss_t': T[c['mss_i']], 'mss_i': c['mss_i'], 'mss_level': f(c['mss_level']), 'fvg_i': c['fvg_i'],
    }


def find_signal(P, now, cfg=CFG):
    """(signal|None, diag). P = smc.Prep (cfg=ict.CFG bilan qurilgani ma'qul)."""
    ctx = _ctx(P, now, cfg)
    if ctx is None:
        return None, {'stage': 'data'}
    diag = {'stage': 'ok', 'kz': killzone(P.T[now])}
    res = []
    for s, name in ((1, 'SELL'), (-1, 'BUY')):
        lv = ctx['levels'] if s == 1 else _mirror(ctx['levels'])
        tg = ctx['levels'] + ctx['tp_extra']
        tg = tg if s == 1 else _mirror(tg)
        mo = None if ctx['mo'] is None else (ctx['mo'] if s == 1 else -ctx['mo'])
        bias_ok = ctx['bias'] == ('down' if s == 1 else 'up')
        w = {}
        cand, stage = _scan_dir(P, P.v[s], now, lv, tg, mo, ctx['a'], bias_ok, cfg, watch=w)
        diag[name.lower()] = stage
        if w:
            f = (lambda x: x) if s == 1 else (lambda x: -x)
            diag[name.lower() + '_watch'] = {'level_kind': w['level_kind'], 'level_price': f(w['level_price']),
                                             'sweep_ext': f(w['sweep_ext']), 'mss_level': f(w['mss_level']),
                                             'bars_left': w['bars_left']}
        if cand:
            res.append(_finish(cand, s, name, P))
    if not res:
        return None, diag
    res.sort(key=lambda x: (x['score'], x['mss_i']), reverse=True)
    return res[0], diag


def ict_map(P, now, cfg=CFG):
    """Jonli ICT konteksti: sessiya, Midnight Open, Osiyo/London diapazoni, PDH/PDL, premium/discount."""
    ctx = _ctx(P, now, cfg)
    if ctx is None:
        return None
    price = P.c[now]['c']
    H, L = P.v[1].H, P.v[1].L
    live = []
    for lv in ctx['levels'] + [x for x in ctx['tp_extra'] if x['kind'] in ('PWH', 'PWL')]:
        st = lv['f'] + 1
        taken = st <= now and ((max(H[st:now + 1]) > lv['price']) if lv['side'] == 'high'
                               else (min(L[st:now + 1]) < lv['price']))
        live.append({**lv, 'taken': taken})
    pd = ctx['pd']
    eq = None
    zone = None
    if pd:
        eq = (pd['h'] + pd['l']) / 2.0
        zone = 'premium' if price > eq else 'discount'
    t_next = P.T[now] + M15
    return {'price': price, 'atr': ctx['a'], 'mo': ctx['mo'], 'bias': ctx['bias'],
            'asia': ctx['asia'], 'london': ctx['london'], 'pd': pd, 'eq': eq, 'pd_zone': zone,
            'levels': sorted(live, key=lambda x: -x['price']),
            'kz_now': killzone(t_next), 'kz_next': next_killzone(t_next)}
