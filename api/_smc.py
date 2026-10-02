"""XAUUSD: SMC + Liquidity Sweep + Classic SNR signal mexanizmi.

Toza Python (tashqi kutubxonasiz). Hamma hisob-kitob faqat YOPILGAN shamlar bilan
qilinadi va kelajakka qaramaydi (look-ahead yo'q): har bir funksiya `now` indeksidan
keyingi ma'lumotga tegmaydi. Buni tests/test_smc.py tekshiradi.

Mantiq (SELL uchun; BUY esa narxlarni ko'zguda aks ettirib, xuddi shu kod bilan topiladi):
  1. SNR      : H4 da narx kamida 2 marta qaytgan zona (klassik support/resistance).
  2. Liquidity: teng yuqorilar (EQH), kecha/o'tgan hafta high'i, Osiyo sessiyasi high'i.
  3. Sweep    : M15 sham likvidlik ustiga soya chiqarib, qaytib uning ostida yopiladi,
                va bu soya SNR zonasi ichida bo'ladi.
  4. MSS/CHoCH: sweepdan oldingi oxirgi swing-low pastga yopilish bilan buziladi.
  5. Kirish   : displacement qoldirgan FVG va/yoki Order Block zonasi (limit order).
  6. SL       : sweep soyasining tashqarisida; TP: qarama-qarshi likvidlik yoki SNR zona,
                kamida 1:min_rr.
"""
import bisect

CFG = {
    'swing_n': 3,            # M15 swing (MSS uchun): chap/o'ngda nechta sham
    'liq_n': 2,              # M15 swing (teng high/low topish uchun)
    'htf_n': 2,              # H4 swing (SNR uchun)
    'snr_lookback': 220,     # SNR uchun nechta H4 sham orqaga qaraladi
    'snr_tol_atr': 0.35,     # bir zonaga yig'ish masofasi (H4 ATR ulushi)
    'snr_min_touches': 2,    # zona kamida necha marta tegilgan bo'lishi kerak
    'snr_pad_atr': 1.0,      # sweep zonaga "yaqin" hisoblanadigan masofa (M15 ATR)
    'eq_tol_atr': 0.15,      # teng high/low bo'lish tolerantligi (M15 ATR)
    'liq_lookback': 300,     # teng high/low uchun orqaga (M15 sham)
    'mss_max_bars': 16,      # sweepdan keyin MSS uchun maksimal sham (4 soat)
    'max_sweep_atr': 2.5,    # sweep soyasi bundan chuqur bo'lsa, bu sweep emas, breakout
    'sl_buf_atr': 0.15,      # SL zaxirasi (ATR ulushi)
    'sl_min_buf': 0.30,      # SL zaxirasi minimal ($, spredni qoplaydi)
    'fvg_min_atr': 0.2,      # FVG minimal kattaligi (ATR ulushi)
    'tp_buf_atr': 0.10,      # TP darajadan biroz oldin qo'yiladi
    'min_risk_atr': 0.5,     # SL masofasi minimal (ATR)
    'max_risk_atr': 4.0,     # SL masofasi maksimal (ATR)
    'min_rr': 2.0,           # minimal foyda/xavf nisbati
    'rr_cap': 5.0,           # TP bundan uzoq bo'lsa, shu RR ga qisqartiriladi
    'min_score': 8,          # 12 balldan minimal ball
    'min_h4': 50,            # kamida shuncha yopilgan H4 sham kerak
    'entry_timeout': 12,     # limit order shuncha shamda to'lmasa bekor (3 soat)
    'max_hold': 96,          # pozitsiya maksimal shuncha sham (24 soat)
    'cost': 0.30,            # backtestda har savdoga spred+slippage ($)
}
MAX_SCORE = 12
RANK = {'no_sweep': 0, 'sweep_no_snr': 1, 'sweep_no_mss': 2, 'no_zone': 3,
        'risk_fail': 4, 'rr_fail': 5, 'score_low': 6, 'ok': 7}
M15 = 900


# ----------------------------------------------------------------- yordamchilar
def resample(c, sec=14400):
    """M15 -> H4 (UTC bo'yicha 00,04,08,... qutilar)."""
    out, cur = [], None
    for b in c:
        k = b['t'] // sec
        if cur is None or cur['k'] != k:
            if cur is not None:
                out.append(cur)
            cur = {'k': k, 't': k * sec, 'end': (k + 1) * sec,
                   'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']}
        else:
            if b['h'] > cur['h']:
                cur['h'] = b['h']
            if b['l'] < cur['l']:
                cur['l'] = b['l']
            cur['c'] = b['c']
    if cur is not None:
        out.append(cur)
    return out


def ema(vals, n):
    k, e, out = 2.0 / (n + 1), None, []
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def pivots(H, L, n):
    """Fractal swing'lar. (indeks, narx). Tasdiq indeksi = indeks + n."""
    hi, lo = [], []
    for i in range(n, len(H) - n):
        h, l = H[i], L[i]
        okh = okl = True
        for k in range(1, n + 1):
            if okh and (H[i - k] >= h or H[i + k] > h):
                okh = False
            if okl and (L[i - k] <= l or L[i + k] < l):
                okl = False
            if not okh and not okl:
                break
        if okh:
            hi.append((i, h))
        if okl:
            lo.append((i, l))
    return hi, lo


def _atr(H, L, C, end, period=14):
    s = max(1, end - period + 1)
    tot, n = 0.0, 0
    for i in range(s, end + 1):
        pc = C[i - 1]
        tot += max(H[i] - L[i], abs(H[i] - pc), abs(L[i] - pc))
        n += 1
    return tot / n if n else 0.0


def _day(t):   # savdo kuni: 22:00 UTC da almashadi (forex/oltin rollover)
    return (t + 7200) // 86400


def _week(t):
    return (_day(t) + 3) // 7


# ----------------------------------------------------------------- ma'lumot tayyorlash
class View:
    """Narx ko'rinishi. sign=-1 bo'lsa narxlar ko'zguda aks ettiriladi (BUY -> SELL)."""

    def __init__(self, c, sign, cfg):
        if sign == 1:
            self.O = [b['o'] for b in c]
            self.H = [b['h'] for b in c]
            self.L = [b['l'] for b in c]
            self.C = [b['c'] for b in c]
        else:
            self.O = [-b['o'] for b in c]
            self.H = [-b['l'] for b in c]
            self.L = [-b['h'] for b in c]
            self.C = [-b['c'] for b in c]
        self.pm_hi, self.pm_lo = pivots(self.H, self.L, cfg['swing_n'])
        self.pm_lo_idx = [i for i, _ in self.pm_lo]


class Prep:
    """Bir seriya uchun bir marta hisoblanadi; keyin har bir `now` uchun tez ishlaydi."""

    def __init__(self, c, cfg=CFG):
        self.c, self.cfg = c, cfg
        self.T = [b['t'] for b in c]
        self.v = {1: View(c, 1, cfg), -1: View(c, -1, cfg)}
        V = self.v[1]
        self.pl_hi, self.pl_lo = pivots(V.H, V.L, cfg['liq_n'])
        self.pl_hi_idx = [i for i, _ in self.pl_hi]
        self.pl_lo_idx = [i for i, _ in self.pl_lo]
        self.h4 = resample(c)
        self.h4_end = [x['end'] for x in self.h4]
        self.h4H = [x['h'] for x in self.h4]
        self.h4L = [x['l'] for x in self.h4]
        self.h4C = [x['c'] for x in self.h4]
        self.h4_hi, self.h4_lo = pivots(self.h4H, self.h4L, cfg['htf_n'])
        self.ema4 = ema(self.h4C, 50)
        self.zone_cache = {}
        self.days, self.weeks, self.asia = {}, {}, {}
        for i, b in enumerate(c):
            t = b['t']
            d = _day(t)
            for st, key in ((self.days, d), (self.weeks, _week(t))):
                e = st.get(key)
                if e is None:
                    st[key] = {'h': b['h'], 'l': b['l'], 'n': 1, 'last': i}
                else:
                    if b['h'] > e['h']:
                        e['h'] = b['h']
                    if b['l'] < e['l']:
                        e['l'] = b['l']
                    e['n'] += 1
                    e['last'] = i
            if (t % 86400) // 3600 < 7:
                e = self.asia.get(d)
                if e is None:
                    self.asia[d] = {'h': b['h'], 'l': b['l'], 'n': 1, 'last': i}
                else:
                    e['h'] = max(e['h'], b['h'])
                    e['l'] = min(e['l'], b['l'])
                    e['n'] += 1
                    e['last'] = i
        self.days_sorted = sorted(k for k, e in self.days.items() if e['n'] >= 40)
        self.weeks_sorted = sorted(k for k, e in self.weeks.items() if e['n'] >= 150)

    def atr4(self, k):
        return _atr(self.h4H, self.h4L, self.h4C, k - 1)


def _cluster(pts, gap, maxw):
    """Narx bo'yicha tartiblangan nuqtalarni guruhlaydi: ketma-ket orasi `gap` dan katta bo'lsa
    ajratadi, guruh `maxw` dan keng bo'lsa eng katta oraliqda bo'ladi. Yo'nalishga bog'liq emas."""
    groups, cur = [], []
    for x in pts:
        if cur and x[1] - cur[-1][1] > gap:
            groups.append(cur)
            cur = []
        cur.append(x)
    if cur:
        groups.append(cur)
    out = []

    def split(cl):
        if len(cl) < 2 or cl[-1][1] - cl[0][1] <= maxw:
            out.append(cl)
            return
        k = max(range(1, len(cl)), key=lambda q: cl[q][1] - cl[q - 1][1])
        split(cl[:k])
        split(cl[k:])
    for cl in groups:
        split(cl)
    return out


# ----------------------------------------------------------------- SNR zonalar (H4)
def _zones(P, k, cfg):
    z = P.zone_cache.get(k)
    if z is not None:
        return z
    zones = []
    if k >= cfg['min_h4']:
        a = P.atr4(k)
        n, lo_i = cfg['htf_n'], k - cfg['snr_lookback']
        piv = [x for x in P.h4_hi if lo_i <= x[0] <= k - 1 - n]
        piv += [x for x in P.h4_lo if lo_i <= x[0] <= k - 1 - n]
        piv.sort(key=lambda x: x[1])
        gap = a * cfg['snr_tol_atr']
        for cl in _cluster(piv, gap, gap * 1.5):
            if len(cl) >= cfg['snr_min_touches']:
                ps = [x[1] for x in cl]
                zones.append({'lo': min(ps), 'hi': max(ps), 'touches': len(cl),
                              'last': max(x[0] for x in cl)})
    P.zone_cache[k] = zones
    return zones


# ----------------------------------------------------------------- likvidlik darajalari
def _levels(P, now, a, cfg):
    T = P.T
    lv = []
    cur_d = _day(T[now])
    pos = bisect.bisect_left(P.days_sorted, cur_d) - 1
    if pos >= 0:
        e = P.days[P.days_sorted[pos]]
        lv.append({'price': e['h'], 'side': 'high', 'kind': 'PDH', 'f': e['last'], 'grade': 1})
        lv.append({'price': e['l'], 'side': 'low', 'kind': 'PDL', 'f': e['last'], 'grade': 1})
    pos = bisect.bisect_left(P.weeks_sorted, _week(T[now])) - 1
    if pos >= 0:
        e = P.weeks[P.weeks_sorted[pos]]
        lv.append({'price': e['h'], 'side': 'high', 'kind': 'PWH', 'f': e['last'], 'grade': 1})
        lv.append({'price': e['l'], 'side': 'low', 'kind': 'PWL', 'f': e['last'], 'grade': 1})
    hr = (T[now] % 86400) // 3600
    if 7 <= hr < 22:
        e = P.asia.get(cur_d)
        if e and e['n'] >= 8:
            lv.append({'price': e['h'], 'side': 'high', 'kind': 'ASH', 'f': e['last'], 'grade': 1})
            lv.append({'price': e['l'], 'side': 'low', 'kind': 'ASL', 'f': e['last'], 'grade': 1})
    n_, lo_i, tol = cfg['liq_n'], max(0, now - cfg['liq_lookback']), a * cfg['eq_tol_atr']
    for side, idxs, piv in (('high', P.pl_hi_idx, P.pl_hi), ('low', P.pl_lo_idx, P.pl_lo)):
        pts = piv[bisect.bisect_left(idxs, lo_i):bisect.bisect_right(idxs, now - n_)]
        pts = sorted(pts, key=lambda x: x[1])
        for g in _cluster(pts, tol, tol * 2):
            if len(g) < 2:
                continue
            g.sort(key=lambda x: x[0])
            sp = [g[0]]
            for x in g[1:]:
                if x[0] - sp[-1][0] >= 3:
                    sp.append(x)
            if len(sp) >= 2:
                price = max(x[1] for x in sp) if side == 'high' else min(x[1] for x in sp)
                lv.append({'price': price, 'side': side, 'kind': 'EQH' if side == 'high' else 'EQL',
                           'f': sp[-1][0], 'grade': 1 if len(sp) >= 3 else 0})
    return lv


def _mirror_levels(lv):
    return [{**x, 'price': -x['price'], 'side': 'low' if x['side'] == 'high' else 'high'} for x in lv]


def _mirror_zones(zs):
    return [{**z, 'lo': -z['hi'], 'hi': -z['lo']} for z in zs]


def _context(P, now, cfg):
    T, V = P.T, P.v[1]
    k = bisect.bisect_right(P.h4_end, T[now] + M15)
    if k < cfg['min_h4'] or now < 50:
        return None
    a = _atr(V.H, V.L, V.C, now)
    if a <= 0:
        return None
    hr = (T[now] % 86400) // 3600
    return {'a': a, 'k': k, 'zones': _zones(P, k, cfg), 'levels': _levels(P, now, a, cfg),
            'bias_sell': P.h4C[k - 1] < P.ema4[k - 1], 'bias_buy': P.h4C[k - 1] > P.ema4[k - 1],
            'sess': 7 <= hr < 20}


# ----------------------------------------------------------------- asosiy qidiruv (SELL ko'rinishida)
def _scan_dir(V, T, now, levels, zones, a, bias_ok, sess_ok, cfg, watch=None):
    O, H, L, C = V.O, V.H, V.L, V.C
    stage = 'no_sweep'

    def up(s):
        nonlocal stage
        if RANK[s] > RANK[stage]:
            stage = s

    j_min = now - cfg['mss_max_bars'] - 1
    pad = cfg['snr_pad_atr'] * a
    cands = []
    for lev in levels:
        if lev['side'] != 'high':
            continue
        p, start = lev['price'], lev['f'] + 1
        if start > now - 1:
            continue
        mid = max(start, j_min)
        if mid > start and max(H[start:mid]) > p:
            continue                      # daraja avval tegilgan: birinchi sweep emas
        for j in range(mid, now):
            if H[j] > p:
                if C[j] < p and H[j] - p <= cfg['max_sweep_atr'] * a:
                    cands.append((j, lev))
                break                     # yo'qsa daraja buzilgan (breakout)

    best = None
    for j, lev in cands:
        up('sweep_no_snr')
        sw_hi = H[j]
        zone = None
        for z in zones:
            if (z['lo'] - pad <= sw_hi <= z['hi'] + pad) or (z['lo'] - pad <= lev['price'] <= z['hi'] + pad):
                if zone is None or z['touches'] > zone['touches']:
                    zone = z
        if zone is None:
            continue
        up('sweep_no_mss')
        pi = bisect.bisect_left(V.pm_lo_idx, j) - 1
        piv = None
        while pi >= 0:
            i_, p_ = V.pm_lo[pi]
            if i_ + cfg['swing_n'] <= now:      # faqat tasdiqlangan swing
                if i_ >= j - 40:
                    piv = (i_, p_)
                break
            pi -= 1
        if piv is None:
            continue
        m = None
        for k_ in range(j + 1, min(now, j + cfg['mss_max_bars']) + 1):
            if H[k_] > sw_hi:
                break                           # sweep balandligi buzildi
            if C[k_] < piv[1]:
                m = k_
                break
        if m is None and watch is not None and j + cfg['mss_max_bars'] >= now and j > watch.get('j', -1):
            watch.update({'j': j, 'level_kind': lev['kind'], 'level_price': lev['price'], 'sweep_ext': sw_hi,
                          'mss_level': piv[1], 'bars_left': j + cfg['mss_max_bars'] - now})
        if m is None or m < now - 1:
            continue
        up('no_zone')
        fvg = None                              # sweepga eng yaqin (birinchi) yetarli kattalikdagi FVG
        for k_ in range(max(j + 1, 2), m + 1):
            top, bot = L[k_ - 2], H[k_]
            if bot < top and top - bot >= cfg['fvg_min_atr'] * a:
                fvg = (bot, top)
                break
        ob = None
        for i_ in range(m - 1, max(j - 3, -1), -1):
            if C[i_] > O[i_]:
                ob = (L[i_], H[i_])
                break
        if fvg is None and ob is None:
            continue
        ov = False
        if fvg and ob and max(fvg[0], ob[0]) <= min(fvg[1], ob[1]):
            ez, ov = (max(fvg[0], ob[0]), min(fvg[1], ob[1])), True
        else:
            ez = fvg or ob
        entry = (ez[0] + ez[1]) / 2.0
        if C[now] >= entry:
            continue                            # narx allaqachon zonaga qaytib bo'lgan
        up('risk_fail')
        sl = sw_hi + max(cfg['sl_buf_atr'] * a, cfg['sl_min_buf'])
        risk = sl - entry
        if not (cfg['min_risk_atr'] * a <= risk <= cfg['max_risk_atr'] * a):
            continue
        up('rr_fail')
        tg = [(l2['price'] + cfg['tp_buf_atr'] * a, l2['kind']) for l2 in levels
              if l2['side'] == 'low' and l2['price'] < entry - 0.5 * risk]
        tg += [(z['hi'] + cfg['tp_buf_atr'] * a, 'SNR') for z in zones
               if z['hi'] < entry - 0.5 * risk and z['hi'] < sw_hi]
        tg.sort(key=lambda x: -x[0])
        sel = [x for x in tg if (entry - x[0]) / risk >= cfg['min_rr']]
        if not sel:
            continue
        tp, tpk = sel[0]
        rr = (entry - tp) / risk
        if rr > cfg['rr_cap']:
            tp, rr, tpk = entry - cfg['rr_cap'] * risk, cfg['rr_cap'], 'RR'
        tp2 = sel[1][0] if len(sel) > 1 and sel[1][0] < tp - 0.25 * risk else None
        score = (6 + (1 if fvg else 0) + (1 if ov else 0) + (1 if zone['touches'] >= 3 else 0)
                 + (1 if lev['grade'] else 0) + (1 if sess_ok else 0) + (1 if bias_ok else 0))
        up('score_low')
        if score < cfg['min_score']:
            continue
        up('ok')
        cand = {'entry': entry, 'sl': sl, 'tp': tp, 'tp2': tp2, 'tpk': tpk, 'rr': rr, 'score': score,
                'level': dict(lev), 'zone': dict(zone), 'ez': ez, 'fvg': bool(fvg), 'ov': ov,
                'sweep_i': j, 'sweep_ext': sw_hi, 'mss_i': m, 'mss_level': piv[1], 'risk': risk}
        if best is None or (score, j) > (best['score'], best['sweep_i']):
            best = cand
    return best, stage


def _finish(c, s, name, P):
    """Ko'zgodagi natijani asl narxga qaytarish."""
    T = P.T
    f = (lambda x: x) if s == 1 else (lambda x: -x)
    z, ez, lev = c['zone'], c['ez'], c['level']
    zlo, zhi = (z['lo'], z['hi']) if s == 1 else (-z['hi'], -z['lo'])
    elo, ehi = (ez[0], ez[1]) if s == 1 else (-ez[1], -ez[0])
    return {
        'side': name, 'key': f"{name}:{T[c['sweep_i']]}",
        'entry': f(c['entry']), 'sl': f(c['sl']), 'tp': f(c['tp']),
        'tp2': None if c['tp2'] is None else f(c['tp2']), 'tpk': c['tpk'],
        'rr': c['rr'], 'score': c['score'], 'risk': c['risk'],
        'level': {'kind': lev['kind'], 'price': f(lev['price']), 'grade': lev['grade']},
        'zone': {'lo': zlo, 'hi': zhi, 'touches': z['touches']},
        'ez': {'lo': elo, 'hi': ehi}, 'fvg': c['fvg'], 'ov': c['ov'],
        'sweep_t': T[c['sweep_i']], 'sweep_ext': f(c['sweep_ext']),
        'mss_t': T[c['mss_i']], 'mss_level': f(c['mss_level']), 'mss_i': c['mss_i'],
    }


def find_signal(P, now, cfg=CFG):
    """`now` indeksidagi YOPILGAN sham holatiga ko'ra signalni qaytaradi: (signal|None, diag)."""
    ctx = _context(P, now, cfg)
    if ctx is None:
        return None, {'stage': 'data'}
    diag = {'stage': 'ok', 'price': P.c[now]['c'], 'atr': ctx['a'],
            'zones': len(ctx['zones']), 'levels': len(ctx['levels'])}
    res = []
    for s, name in ((1, 'SELL'), (-1, 'BUY')):
        lv = ctx['levels'] if s == 1 else _mirror_levels(ctx['levels'])
        zn = ctx['zones'] if s == 1 else _mirror_zones(ctx['zones'])
        bias = ctx['bias_sell'] if s == 1 else ctx['bias_buy']
        w = {}
        cand, stage = _scan_dir(P.v[s], P.T, now, lv, zn, ctx['a'], bias, ctx['sess'], cfg, watch=w)
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


def market_map(P, now, cfg=CFG):
    """Jonli tahlil uchun: narx atrofidagi SNR zonalar va hali tegilmagan likvidlik darajalari."""
    ctx = _context(P, now, cfg)
    if ctx is None:
        return None
    price = P.c[now]['c']
    H, L = P.v[1].H, P.v[1].L
    live = []
    for lv in ctx['levels']:
        st = lv['f'] + 1
        if st <= now:
            if lv['side'] == 'high' and max(H[st:now + 1]) > lv['price']:
                continue
            if lv['side'] == 'low' and min(L[st:now + 1]) < lv['price']:
                continue
        live.append(lv)
    zs = sorted(ctx['zones'], key=lambda z: abs((z['lo'] + z['hi']) / 2 - price))[:6]
    return {'price': price, 'atr': ctx['a'], 'zones': sorted(zs, key=lambda z: -z['hi']),
            'all_zones': ctx['zones'], 'pad': cfg['snr_pad_atr'] * ctx['a'],
            'levels': sorted(live, key=lambda x: -x['price']),
            'bias': 'pastga' if ctx['bias_sell'] else 'yuqoriga'}


# ----------------------------------------------------------------- backtest
def _stats(trades, extra):
    rs = [t['r'] for t in trades]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    loss = [r for r in rs if r <= 0]
    cum = peak = dd = 0.0
    streak = best_streak = 0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if r <= 0 else 0
        best_streak = max(best_streak, streak)
    out = {'n': n, 'wins': len(wins), 'win_rate': (len(wins) / n * 100) if n else 0.0,
           'avg_r': (sum(rs) / n) if n else 0.0, 'total_r': sum(rs),
           'pf': (sum(wins) / abs(sum(loss))) if loss and sum(loss) != 0 else (float('inf') if wins else 0.0),
           'max_dd_r': dd, 'max_loss_streak': best_streak, 'trades': trades}
    out.update(extra)
    return out


def backtest(c, cfg=CFG, cost=None):
    """Tarixiy M15 seriyada signallarni sham-sham o'ynatadi (kelajakka qaramasdan).
    Bir vaqtda bitta pozitsiya. Noaniq holatda (bir shamda ham SL, ham TP) SL hisoblanadi."""
    cost = cfg['cost'] if cost is None else cost
    P = Prep(c, cfg)
    T, V, N = P.T, P.v[1], len(c)
    extra = {'signals': 0, 'expired': 0, 'missed': 0, 'bars': 0, 'from': T[0] if T else 0,
             'to': T[-1] if T else 0}
    if len(P.h4) <= cfg['min_h4']:
        return _stats([], extra)
    i0 = bisect.bisect_left(T, P.h4_end[cfg['min_h4'] - 1] - M15)
    extra['from'], extra['bars'] = T[i0], N - i0
    trades, seen, pend, pos = [], set(), None, None

    def close(p, why, px, i):
        risk = abs(p['sl'] - p['entry'])
        if why == 'SL':
            gross = -1.0
        elif why == 'TP':
            gross = abs(p['tp'] - p['entry']) / risk
        else:
            gross = ((p['entry'] - px) if p['dir'] == 1 else (px - p['entry'])) / risk
        trades.append({'side': p['side'], 't': T[p['fill_i']], 'exit_t': T[i], 'why': why,
                       'r': gross - cost / risk, 'score': p['score'], 'level': p['level'], 'rr': p['rr']})

    for i in range(i0, N):
        h_, l_, c_ = V.H[i], V.L[i], V.C[i]
        if pend is not None:
            s = pend['dir']
            if i - pend['born'] > cfg['entry_timeout']:
                extra['expired'] += 1
                pend = None
            elif (h_ >= pend['entry']) if s == 1 else (l_ <= pend['entry']):
                pos, pend = {**pend, 'fill_i': i, 'held': 0}, None
                if (h_ >= pos['sl']) if s == 1 else (l_ <= pos['sl']):
                    close(pos, 'SL', pos['sl'], i)
                    pos = None
            elif (l_ <= pend['tp']) if s == 1 else (h_ >= pend['tp']):
                extra['missed'] += 1
                pend = None
        elif pos is not None:
            s = pos['dir']
            sl_hit = (h_ >= pos['sl']) if s == 1 else (l_ <= pos['sl'])
            tp_hit = (l_ <= pos['tp']) if s == 1 else (h_ >= pos['tp'])
            if sl_hit:
                close(pos, 'SL', pos['sl'], i)
                pos = None
            elif tp_hit:
                close(pos, 'TP', pos['tp'], i)
                pos = None
            else:
                pos['held'] += 1
                if pos['held'] >= cfg['max_hold']:
                    close(pos, 'TIME', c_, i)
                    pos = None
        if pend is None and pos is None and i < N - 1:
            sig, _ = find_signal(P, i, cfg)
            if sig and sig['key'] not in seen:
                seen.add(sig['key'])
                extra['signals'] += 1
                pend = {'dir': 1 if sig['side'] == 'SELL' else -1, 'side': sig['side'], 'entry': sig['entry'],
                        'sl': sig['sl'], 'tp': sig['tp'], 'born': i, 'score': sig['score'],
                        'level': sig['level']['kind'], 'rr': sig['rr']}
    return _stats(trades, extra)
