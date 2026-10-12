"""Grafik rasm: M15 shamlar + zonalar, likvidlik, FVG, kirish/SL/TP. PNG bayt qaytaradi.

matplotlib faqat chizish paytida yuklanadi (sovuq ishga tushish tezroq bo'lishi uchun).
"""
import io
import os
import time

os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib')

BG, FG, GRID = '#0f1419', '#d7dde4', '#1e2630'
UP, DOWN = '#26a69a', '#ef5350'
COL = {'zone': '#8e7cc3', 'liq': '#f6c343', 'taken': '#6b7280', 'fvg': '#29b6f6', 'ote': '#ab47bc',
       'asia': '#5c6bc0', 'london': '#26c6da', 'mo': '#ff9800', 'entry': '#e0e0e0', 'sl': DOWN, 'tp': UP}


def tk(t):
    return time.strftime('%H:%M', time.gmtime(t + 5 * 3600))


def render(c, title='', zones=(), levels=(), boxes=(), signal=None, marks=(), note=''):
    """
    c      : yopilgan shamlar [{'t','o','h','l','c'}] (oxirgi ~120 tasi chiziladi)
    zones  : [(lo, hi, matn, rang)]        - butun kenglik bo'ylab gorizontal zona
    levels : [(narx, matn, rang, uslub)]   - gorizontal chiziq ('-', '--', ':')
    boxes  : [(t0, t1, lo, hi, matn, rang)] - vaqt oralig'idagi to'rtburchak (FVG, Osiyo diapazoni)
    signal : {'side','entry','sl','tp','tp2'} yoki None
    marks  : [(t, narx, matn)]              - strelka bilan belgi (sweep, MSS)
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    c = c[-120:]
    if not c:
        raise ValueError("grafik uchun sham yo'q")
    t0 = c[0]['t']
    T = [b['t'] for b in c]

    def xi(t):  # vaqt -> x (sham indeksi), oraliqdan tashqarisi chekkaga yopishadi
        if t <= T[0]:
            return -0.5
        if t >= T[-1]:
            return len(T) - 1 + (t - T[-1]) / 900.0
        lo, hi = 0, len(T) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if T[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return lo + (t - T[lo]) / 900.0

    n = len(c)
    right = n + 14
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=FG, labelsize=8)
    ax.grid(color=GRID, linewidth=0.6)
    ax.yaxis.tick_right()

    lo_p = min(b['l'] for b in c)
    hi_p = max(b['h'] for b in c)
    ys = [lo_p, hi_p]
    if signal:
        ys += [signal['sl'], signal['tp'], signal['entry']]
    span = max(ys) - min(ys)
    ymin, ymax = min(ys) - span * 0.06, max(ys) + span * 0.06

    def visible(*ps):
        return any(ymin - span * 0.02 <= p <= ymax + span * 0.02 for p in ps)

    for lo, hi, txt, col in zones:
        if not visible(lo, hi) and not (lo < ymin and hi > ymax):
            continue
        ax.add_patch(Rectangle((-0.5, lo), right, hi - lo, color=col, alpha=0.16, lw=0))
        ax.text(1, hi, txt, color=col, fontsize=7, va='bottom', alpha=0.95)
    for t_a, t_b, lo, hi, txt, col in boxes:
        x0, x1 = xi(t_a), xi(t_b)
        if x1 < 0:
            continue
        ax.add_patch(Rectangle((max(x0, -0.5), lo), max(x1 - max(x0, -0.5), 0.6), hi - lo,
                               facecolor=col, alpha=0.22, edgecolor=col, lw=0.8))
        if txt:
            ax.text(max(x0, -0.5) + 0.3, hi, txt, color=col, fontsize=7, va='bottom')
    for p, txt, col, st in levels:
        if not visible(p):
            continue
        ax.axhline(p, color=col, lw=0.9, ls=st, alpha=0.85)
        ax.text(n + 0.5, p, ' ' + txt, color=col, fontsize=7, va='center',
                bbox=dict(facecolor=BG, edgecolor='none', pad=0.5, alpha=0.8))

    w = 0.62
    for i, b in enumerate(c):
        col = UP if b['c'] >= b['o'] else DOWN
        ax.vlines(i, b['l'], b['h'], color=col, lw=0.8)
        body = abs(b['c'] - b['o']) or span * 0.002
        ax.add_patch(Rectangle((i - w / 2, min(b['o'], b['c'])), w, body, color=col, lw=0))

    if signal:
        x0 = n - 1
        e, sl, tp = signal['entry'], signal['sl'], signal['tp']
        ax.add_patch(Rectangle((x0, min(e, sl)), 12, abs(sl - e), color=COL['sl'], alpha=0.22, lw=0))
        ax.add_patch(Rectangle((x0, min(e, tp)), 12, abs(tp - e), color=COL['tp'], alpha=0.22, lw=0))
        for p, txt, col in ((e, 'Kirish %.2f' % e, COL['entry']), (sl, 'SL %.2f' % sl, COL['sl']),
                            (tp, 'TP %.2f' % tp, COL['tp'])):
            ax.hlines(p, x0, x0 + 12, color=col, lw=1.3)
            ax.text(x0 + 12.3, p, txt, color=col, fontsize=8, va='center', fontweight='bold')
        if signal.get('tp2'):
            ax.hlines(signal['tp2'], x0, x0 + 12, color=COL['tp'], lw=1, ls='--')
            ax.text(x0 + 12.3, signal['tp2'], 'TP2 %.2f' % signal['tp2'], color=COL['tp'], fontsize=7, va='center')

    for t, p, txt in marks:
        x = xi(t)
        if x < 0:
            continue
        up = p >= (c[min(int(x), n - 1)]['h'] + c[min(int(x), n - 1)]['l']) / 2
        dy = span * 0.07 * (1 if up else -1)
        ax.annotate(txt, xy=(x, p), xytext=(x, p + dy), color=FG, fontsize=7.5, ha='center',
                    va='bottom' if up else 'top',
                    arrowprops=dict(arrowstyle='->', color=FG, lw=0.8))

    last = c[-1]['c']
    ax.axhline(last, color='#90a4ae', lw=0.6, ls=':')
    ax.text(n + 0.5, last, ' %.2f' % last, color=BG, fontsize=8, va='center',
            bbox=dict(facecolor='#90a4ae', edgecolor='none', pad=1.2))

    step = max(1, n // 10)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([tk(T[i]) for i in ticks])
    ax.set_xlim(-1, right + 6)
    ax.set_ylim(ymin, ymax)
    ax.set_title(title, color=FG, fontsize=11, loc='left')
    foot = 'M15, Toshkent vaqti. %s' % note if note else 'M15, Toshkent vaqti'
    fig.text(0.01, 0.01, foot, color='#8a96a3', fontsize=7.5)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    buf = io.BytesIO()
    fig.savefig(buf, format='png', facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
