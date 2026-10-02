"""Ishga tushirish:  python3 tests/test_smc.py"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
import _smc as S  # noqa: E402

T0 = 1704067200  # 2024-01-01 00:00 UTC (dushanba)


def mk(o, c, t, w=0.3):
    return {'t': t, 'o': o, 'c': c, 'h': max(o, c) + w, 'l': min(o, c) - w}


def scenario(mirror=False, break_mss=True, start_hour=12):
    """Qo'lda chizilgan SELL holati: swing-low 2029.7, likvidlik 2040 ustiga sweep, keyin MSS."""
    t = T0 + start_hour * 3600
    bars = []
    def add(o, c, h=None, l=None):
        b = mk(o, c, t + 900 * len(bars))
        if h is not None:
            b['h'] = h
        if l is not None:
            b['l'] = l
        bars.append(b)
    for i in range(10):                       # 0..9 yon
        add(2033 + (i % 2) * 0.5, 2033.5 - (i % 2) * 0.5)
    for o, c in ((2033.0, 2031.5), (2031.5, 2030.5), (2030.5, 2030.0), (2030.0, 2030.2)):
        add(o, c)                              # 10..13 tushish, swing low 13
    # 13 ni swing low qilish uchun pastroq low
    bars[13]['l'] = 2029.7
    for o, c in ((2030.2, 2031.5), (2031.5, 2032.5)):
        add(o, c)                              # 14,15
    p = 2032.5
    while p < 2039.0:                          # rally
        add(p, p + 0.5)
        p += 0.5
    j = len(bars)
    add(2039.3, 2039.5, h=2041.6, l=2039.0)    # sweep sham (2040 ustiga soya, ostida yopilgan)
    add(2039.4, 2038.2, h=2039.6, l=2038.0)
    add(2038.2, 2031.0, h=2038.3, l=2030.8)    # displacement + FVG
    add(2031.0, 2029.0 if break_mss else 2030.5, h=2031.2, l=2028.7)   # MSS
    if mirror:
        bars = [{'t': b['t'], 'o': 8000 - b['o'], 'c': 8000 - b['c'], 'h': 8000 - b['l'], 'l': 8000 - b['h']} for b in bars]
    return bars, j


def run_scan(bars, levels, zones, sign=1, cfg=S.CFG):
    P = S.Prep(bars, cfg)
    V = P.v[sign]
    now = len(bars) - 1
    a = S._atr(V.H, V.L, V.C, now)
    return S._scan_dir(V, P.T, now, levels, zones, a, True, True, cfg)


LEV = [{'price': 2040.0, 'side': 'high', 'kind': 'PDH', 'f': 5, 'grade': 1},
       {'price': 2020.0, 'side': 'low', 'kind': 'PDL', 'f': 5, 'grade': 1}]
ZONE = [{'lo': 2039.0, 'hi': 2041.5, 'touches': 3, 'last': 5}]


def test_sell_pipeline():
    bars, j = scenario()
    sig, stage = run_scan(bars, LEV, ZONE)
    assert sig is not None and stage == 'ok', stage
    assert sig['sweep_i'] == j and sig['entry'] > bars[-1]['c']
    assert sig['sl'] > sig['sweep_ext'] > sig['entry'] > sig['tp']
    assert sig['rr'] >= S.CFG['min_rr'] - 1e-9 and sig['score'] == 12 and sig['fvg'] and sig['ov']
    print('  SELL: entry %.2f sl %.2f tp %.2f rr %.2f ball %d' % (sig['entry'], sig['sl'], sig['tp'], sig['rr'], sig['score']))


def test_no_snr_blocks_signal():
    bars, _ = scenario()
    sig, stage = run_scan(bars, LEV, [{'lo': 2000.0, 'hi': 2005.0, 'touches': 3, 'last': 1}])
    assert sig is None and stage == 'sweep_no_snr', stage


def test_no_mss_blocks_signal():
    bars, _ = scenario(break_mss=False)
    sig, stage = run_scan(bars, LEV, ZONE)
    assert sig is None and stage == 'sweep_no_mss', stage


def test_no_sweep_when_level_not_touched():
    bars, _ = scenario()
    sig, stage = run_scan(bars, [{'price': 2060.0, 'side': 'high', 'kind': 'PDH', 'f': 5, 'grade': 1},
                                 LEV[1]], ZONE)
    assert sig is None and stage == 'no_sweep', stage


def test_breakout_is_not_sweep():
    bars, j = scenario()
    bars[j]['c'] = 2041.0   # sham 2040 dan yuqorida yopildi: bu breakout, sweep emas
    sig, stage = run_scan(bars, LEV, ZONE)
    assert sig is None and stage == 'no_sweep', stage


def test_buy_is_exact_mirror():
    bars, j = scenario(mirror=True)
    lv = [{'price': 8000 - 2040.0, 'side': 'low', 'kind': 'PDL', 'f': 5, 'grade': 1},
          {'price': 8000 - 2020.0, 'side': 'high', 'kind': 'PDH', 'f': 5, 'grade': 1}]
    zn = [{'lo': 8000 - 2041.5, 'hi': 8000 - 2039.0, 'touches': 3, 'last': 5}]
    P = S.Prep(bars)
    now = len(bars) - 1
    cand, stage = S._scan_dir(P.v[-1], P.T, now, S._mirror_levels(lv), S._mirror_zones(zn),
                              S._atr(P.v[-1].H, P.v[-1].L, P.v[-1].C, now), True, True, S.CFG)
    assert cand is not None, stage
    sig = S._finish(cand, -1, 'BUY', P)
    assert sig['side'] == 'BUY' and sig['tp'] > sig['entry'] > sig['sweep_ext'] > sig['sl'] or \
        sig['tp'] > sig['entry'] > sig['sl']
    assert sig['sl'] < sig['sweep_ext'] < sig['entry'] < sig['tp'], sig
    print('  BUY : entry %.2f sl %.2f tp %.2f rr %.2f' % (sig['entry'], sig['sl'], sig['tp'], sig['rr']))


def rw(n, seed, start=2000.0, vol=1.2, flip=None):
    rnd = random.Random(seed)
    out, p, t = [], start, T0
    while len(out) < n:
        wd, hr = ((t // 86400) + 3) % 7, (t % 86400) // 3600
        if wd == 5 or (wd == 6 and hr < 22) or (wd == 4 and hr >= 22):
            t += 900
            continue
        o = p
        c = o + rnd.gauss(0, vol)
        h = max(o, c) + abs(rnd.gauss(0, vol * 0.4))
        l = min(o, c) - abs(rnd.gauss(0, vol * 0.4))
        out.append({'t': t, 'o': o, 'h': h, 'l': l, 'c': c})
        p, t = c, t + 900
    return out


def flip(c, k=8000.0):
    return [{'t': b['t'], 'o': k - b['o'], 'h': k - b['l'], 'l': k - b['h'], 'c': k - b['c']} for b in c]


def collect(c, cfg=S.CFG, upto=None):
    P = S.Prep(c, cfg)
    i0 = S.bisect.bisect_left(P.T, P.h4_end[cfg['min_h4'] - 1] - 900)
    out = {}
    for i in range(i0, upto if upto is not None else len(c)):
        sig, _ = S.find_signal(P, i, cfg)
        if sig:
            out[i] = sig
    return out


def test_no_lookahead():
    cfg = dict(S.CFG, min_score=7)
    a = rw(2200, 11)
    b = a + rw(600, 99, start=a[-1]['c'])
    # b ning davomi a ning oxiridan boshlanishi uchun vaqtni moslash
    t_last = a[-1]['t']
    for k, x in enumerate(b[len(a):]):
        x['t'] = t_last + 900 * (k + 1)
    ra, rb = collect(a, cfg), collect(b, cfg, upto=len(a) - 1)
    ra = {i: s for i, s in ra.items() if i < len(a) - 1}
    assert ra.keys() == rb.keys(), (sorted(ra), sorted(rb))
    for i in ra:
        assert abs(ra[i]['entry'] - rb[i]['entry']) < 1e-9 and ra[i]['side'] == rb[i]['side']
    print('  look-ahead yo\'q: %d ta signal ikki xil seriyada bir xil' % len(ra))


def test_buy_sell_symmetry():
    cfg = dict(S.CFG, min_score=7)
    tot = 0
    for seed in (1, 2, 3):
        a = rw(2600, seed)
        ra, rb = collect(a, cfg), collect(flip(a), cfg)
        assert ra.keys() == rb.keys(), (seed, sorted(ra), sorted(rb))
        for i in ra:
            assert ra[i]['side'] != rb[i]['side']
            assert abs(ra[i]['entry'] - (8000 - rb[i]['entry'])) < 1e-6
            assert abs(ra[i]['sl'] - (8000 - rb[i]['sl'])) < 1e-6
            assert abs(ra[i]['tp'] - (8000 - rb[i]['tp'])) < 1e-6
        tot += len(ra)
    print('  simmetriya: %d ta signal, BUY va SELL bir-birining ko\'zgusi' % tot)


def test_backtest_runs_and_is_fast():
    c = rw(5000, 5)
    t = time.time()
    r = S.backtest(c, dict(S.CFG, min_score=7))
    dt = time.time() - t
    assert dt < 25, dt
    for tr in r['trades']:
        assert tr['why'] in ('SL', 'TP', 'TIME')
    print('  backtest 5000 sham: %.1f s, signal %d, savdo %d, o\'rtacha R %.2f' % (dt, r['signals'], r['n'], r['avg_r']))


def test_random_walk_has_no_edge():
    """Tasodifiy narxda strategiya pul ishlamasligi kerak. Agar ishlasa, demak xatolik (look-ahead) bor."""
    tot_r = n = 0
    for seed in range(20, 26):
        r = S.backtest(rw(5000, seed), dict(S.CFG, min_score=7))
        tot_r += r['total_r']
        n += r['n']
    avg = tot_r / n if n else 0.0
    print('  tasodifiy narx: %d savdo, o\'rtacha R = %.3f (0 dan kichik yoki 0 atrofida bo\'lishi kerak)' % (n, avg))
    assert n == 0 or avg < 0.25, avg


if __name__ == '__main__':
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('OK  ', name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print('FAIL', name, repr(e))
    print('\nNatija:', 'HAMMASI O\'TDI' if not fails else '%d ta xato' % fails)
    sys.exit(1 if fails else 0)
