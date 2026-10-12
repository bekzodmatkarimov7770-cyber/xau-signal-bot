"""ICT mexanizmi testi. Ishga tushirish: python3 tests/test_ict.py"""
import calendar
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.dirname(__file__))
import _ict as I  # noqa: E402
import _smc as S  # noqa: E402
import test_smc as T  # noqa: E402


def utc(s):
    return calendar.timegm(__import__('time').strptime(s, '%Y-%m-%d %H:%M'))


def test_ny_time():
    # 2026: yozgi vaqt 8-mart 07:00 UTC dan 1-noyabr 06:00 UTC gacha
    assert I.ny_off(utc('2026-03-08 06:59')) == -5 and I.ny_off(utc('2026-03-08 07:00')) == -4
    assert I.ny_off(utc('2026-11-01 05:59')) == -4 and I.ny_off(utc('2026-11-01 06:00')) == -5
    assert I.killzone(utc('2026-10-12 06:30')) == 'London'          # 02:30 NY
    assert I.killzone(utc('2026-10-12 11:15')) == 'NY AM'           # 07:15 NY
    assert I.killzone(utc('2026-10-12 14:15')) == 'Silver Bullet'   # 10:15 NY
    assert I.killzone(utc('2026-12-14 14:15')) == 'NY AM'           # qishda 09:15 NY
    assert I.killzone(utc('2026-10-12 16:00')) is None
    z, t = I.next_killzone(utc('2026-10-12 16:00'))
    assert z == 'London' and t == utc('2026-10-13 06:00'), (z, t)
    print('OK   test_ny_time')


def signals(c, cfg=I.CFG):
    P = S.Prep(c, cfg)
    out = {}
    for i in range(900, len(c)):
        s, _ = I.find_signal(P, i, cfg)
        if s:
            out[i] = s
    return out


def test_signals_valid_and_in_killzone():
    n = 0
    for seed in (1, 2, 3, 5, 7):
        for i, s in signals(T.rw(4000, seed)).items():
            n += 1
            sell = s['side'] == 'SELL'
            assert (s['sl'] > s['entry'] > s['tp']) if sell else (s['sl'] < s['entry'] < s['tp']), s
            assert s['rr'] >= I.CFG['min_rr'] - 1e-9 and s['score'] >= I.CFG['min_score']
            assert s['ez']['lo'] <= s['entry'] <= s['ez']['hi']
            assert I.killzone(s['sweep_t']) is not None
            assert (s['sweep_ext'] > s['level']['price']) if sell else (s['sweep_ext'] < s['level']['price'])
    assert n >= 5, n
    print('OK   test_signals_valid_and_in_killzone (%d signal)' % n)


def test_no_lookahead():
    c = T.rw(3000, 1)
    full = signals(c)
    assert full
    for i, s in list(full.items())[:6]:
        P = S.Prep(c[:i + 1], I.CFG)
        s2, _ = I.find_signal(P, i, I.CFG)
        assert s2 and s2['key'] == s['key'] and abs(s2['entry'] - s['entry']) < 1e-9, (i, s, s2)
    print('OK   test_no_lookahead')


def test_mirror():
    c = T.rw(3000, 1)
    a, b = signals(c), signals(T.flip(c))
    assert set(a) == set(b), (sorted(a), sorted(b))
    for i in a:
        assert a[i]['side'] != b[i]['side'] and abs(a[i]['entry'] + b[i]['entry'] - 8000) < 1e-6
    print('OK   test_mirror (BUY/SELL simmetrik)')


def test_map_and_backtest():
    c = T.rw(4000, 2)
    P = S.Prep(c, I.CFG)
    m = I.ict_map(P, len(c) - 1)
    assert m and m['mo'] is not None and m['levels'] and m['pd_zone'] in ('premium', 'discount')
    r = S.backtest(c, I.CFG, finder=I.find_signal)
    assert r['bars'] > 0 and r['n'] <= r['signals']
    print('OK   test_map_and_backtest (signal %d, savdo %d)' % (r['signals'], r['n']))


if __name__ == '__main__':
    test_ny_time()
    test_signals_valid_and_in_killzone()
    test_no_lookahead()
    test_mirror()
    test_map_and_backtest()
    print('\nICT TESTLARI: HAMMASI O\'TDI')
