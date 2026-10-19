"""Auto Fibo + SNR testi. Ishga tushirish: python3 tests/test_fibo.py

M1 narx yo'li qo'lda chiziladi, M5 va M15 shundan yig'iladi (uchala taymfreym bir-biriga mos).
Ssenariy (BUY): 2016 da ikki marta qaytgan SNR, 2000 (A) dan 2040 (B) ga impuls,
2016 ga (0.6 Fibo + SNR) qaytish, keyin M1 da yuqoriga BOS.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
import _fibo as F  # noqa: E402

T0 = 1704189600  # 2024-01-02 10:00 UTC (seshanba)
WP = [(0, 2008), (10, 2016), (20, 2010), (30, 2016), (40, 2006), (50, 2000), (65, 2030),
      (72, 2024), (85, 2040), (100, 2016)]                # (M15 sham raqami, narx)


def path(extra=()):
    """Minut -> narx. extra: (minut, narx) qo'shimcha nuqtalar (qaytishdan keyingi M1 harakati)."""
    pts = [(m * 15, p) for m, p in WP] + list(extra)
    out = []
    for (m0, p0), (m1, p1) in zip(pts, pts[1:]):
        for m in range(m0, m1):
            base = p0 + (p1 - p0) * (m - m0) / float(m1 - m0)
            out.append(base + 0.6 * math.sin(2 * math.pi * m / 12.0))
    out.append(pts[-1][1])
    return out


def m1_bars(prices):
    bars, prev = [], prices[0]
    for i, p in enumerate(prices):
        o, c = prev, p
        bars.append({'t': T0 + 60 * i, 'o': o, 'c': c, 'h': max(o, c) + 0.12, 'l': min(o, c) - 0.12})
        prev = p
    return bars


def resample(c, sec):
    out = []
    for b in c:
        k = b['t'] // sec * sec
        if out and out[-1]['t'] == k:
            o = out[-1]
            o['h'], o['l'], o['c'] = max(o['h'], b['h']), min(o['l'], b['l']), b['c']
        else:
            out.append({'t': k, 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c']})
    return out


def mirror(c, k=8000.0):
    return [{'t': b['t'], 'o': k - b['o'], 'h': k - b['l'], 'l': k - b['h'], 'c': k - b['c']} for b in c]


def run(c1, bias='up'):
    c5, c15 = resample(c1, 300), resample(c1, 900)
    f15, f5, f1 = F.analyze_tf(c15, 'M15'), F.analyze_tf(c5, 'M5'), F.analyze_tf(c1, 'M1')
    zones = F.m15_zones(c15)
    sig, diag = F.find_signal(f15, f5, f1, zones, h4_bias=bias)
    return f15, f5, f1, zones, sig, diag


BOUNCE = ((1520, 2019.5), (1530, 2017.6), (1555, 2022.0))

# 1) qaytish bor, M1 tasdiq bor: BUY signal
c1 = m1_bars(path(BOUNCE))
f15, f5, f1, zones, sig, diag = run(c1)
fb = f15['fib']
print('M15:', f15['trend'], 'A %.2f B %.2f depth %.3f' % (fb['A'], fb['B'], fb['depth']), '| M5:', f5['trend'], '| diag:', diag)
assert f15['trend'] == 'up' and f5['trend'] == 'up', 'chuqur qaytish trendni buzmasligi kerak'
assert abs(fb['A'] - 2000) < 1.5 and abs(fb['B'] - 2040) < 1.5, fb
assert abs(fb['levels'][0.618] - (fb['B'] - 0.618 * (fb['B'] - fb['A']))) < 1e-9
assert abs(fb['ext'][1.272] - (fb['A'] + 1.272 * (fb['B'] - fb['A']))) < 1e-9
assert 0.5 <= fb['depth'] <= 0.7
assert any(z['lo'] - 1 <= 2016 <= z['hi'] + 1 for z in zones), zones
assert sig and sig['side'] == 'BUY', diag
assert sig['sl'] < sig['pl'] < sig['entry'] < sig['tp'] < sig['tp2'] < sig['tp3']
assert sig['rr2'] >= F.CFG['min_rr'] and sig['score'] >= F.CFG['min_score']
print('OK BUY:', {k: round(v, 2) if isinstance(v, float) else v for k, v in sig.items() if k in ('entry', 'sl', 'tp', 'tp2', 'tp3', 'rr', 'rr2', 'score')})

# 2) bir xil ma'lumot -> aynan bir xil Fibo (qat'iy qoidalar)
assert F.analyze_tf(resample(c1, 900), 'M15')['fib'] == fb
print('OK takrorlanuvchanlik: bir xil shamlar, bir xil Fibo')

# 3) ko'zgu: xuddi shu holat teskari bo'lsa, SELL chiqadi va darajalar ko'zguda
_, _, _, _, sig_s, diag_s = run(mirror(c1), bias='down')
assert sig_s and sig_s['side'] == 'SELL', diag_s
assert abs((8000 - sig_s['entry']) - sig['entry']) < 1e-6 and abs((8000 - sig_s['sl']) - sig['sl']) < 1e-6
print('OK SELL (ko\'zgu):', round(sig_s['entry'], 2), round(sig_s['sl'], 2), round(sig_s['tp'], 2))

# 4) M1 tasdig'i hali yo'q (narx zonada, endi tushib keldi): signal yo'q, sabab no_trigger
c1b = m1_bars(path(((1506, 2016.6),)))
*_, sig_b, diag_b = run(c1b)
assert sig_b is None and diag_b['stage'] == 'no_trigger', diag_b
print('OK M1 tasdiq kutilmoqda:', diag_b['stage'])

# 5) qaytish sayoz (2024 HL buzilmagan, Fibo 2024->2040 ga tortiladi), hali 0.5 ga yetmagan: not_in_zone
saved = list(WP)
WP[-1] = (100, 2035)
f15c, *_, sig_c, diag_c = run(m1_bars(path(((1520, 2036),))))
assert abs(f15c['fib']['A'] - 2024) < 1.5, f15c['fib']['A']
assert sig_c is None and diag_c['stage'] == 'not_in_zone', diag_c
print('OK zonaga yetmagan:', diag_c['stage'])

# 6) qaytish SNR zonasida emas (2016 da qaytish nuqtasi yo'q): no_snr
WP[:] = [(0, 2008), (10, 2026), (20, 2022), (30, 2027), (40, 2006), (50, 2000), (65, 2030),
         (72, 2024), (85, 2040), (100, 2016)]
*_, sig_d, diag_d = run(m1_bars(path(BOUNCE)))
assert sig_d is None and diag_d['stage'] == 'no_snr', diag_d
print('OK SNR yo\'q:', diag_d['stage'])

# 7) qaytish A (2000) ni buzsa: trend pastga o'tadi
WP[:] = saved[:-1] + [(100, 1994)]
f15e, *_ = run(m1_bars(path(((1520, 1996),))))
assert f15e['trend'] == 'down', f15e['trend']
print('OK A buzildi: trend pastga')
WP[:] = saved

# 8) yangi oddiy sham (yangi cho'qqi yo'q) Fibo nuqtalarini (A, B) siljitmaydi
c15 = resample(c1, 900)
full = F.analyze_tf(c15, 'M15')['fib']
cut = F.analyze_tf(c15 + [dict(c15[-1], t=c15[-1]['t'] + 900)], 'M15')['fib']
assert (cut['A_t'], cut['B_t']) == (full['A_t'], full['B_t'])
print('OK Fibo nuqtalari barqaror')
print('\nFIBO TESTLARI: HAMMASI O\'TDI')
