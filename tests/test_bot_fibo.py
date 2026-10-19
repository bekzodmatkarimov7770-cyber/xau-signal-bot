"""`fibo` buyrug'i testi (soxta ma'lumot va soxta Telegram bilan).
Ishga tushirish: python3 tests/test_bot_fibo.py   (grafiklar tests/_out/ ga saqlanadi)"""
import json
import os
import random
import sys
from unittest import mock

os.environ.update(BOT_TOKEN='123:ABC', ADMIN_ID='777', WEBHOOK_SECRET='sekret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.dirname(__file__))
if os.path.exists('/tmp/signalbot_store.json'):
    os.remove('/tmp/signalbot_store.json')
import _data as D  # noqa: E402
import index as B  # noqa: E402
import test_fibo as TF  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), '_out')
os.makedirs(OUT, exist_ok=True)


def prefix(n=1000, seed=3, end=2008.0):
    """Ssenariydan oldingi tarix (H4 SNR va ATR uchun): 1975-2008 oralig'ida tebranadi."""
    rnd, out, p = random.Random(seed), [], 1990.0
    for k in range(n, 0, -1):
        o = p
        c = o + rnd.gauss(0, 0.8) + (1990.0 - o) * 0.02
        out.append({'t': TF.T0 - 900 * k, 'o': o, 'c': c, 'h': max(o, c) + 0.3, 'l': min(o, c) - 0.3})
        p = c
    out[-1]['c'] = end
    out[-1]['h'] = max(out[-1]['h'], end)
    return out


sent, photos = [], []
B.bot.send_message = lambda chat, text, **k: sent.append((chat, text))
B.bot.send_photo = lambda chat, f, caption=None, **k: photos.append((chat, f.read(), caption))
cl = B.app.test_client()
H = {'content-type': 'application/json', 'X-Telegram-Bot-Api-Secret-Token': 'sekret'}
n = [0]


def cmd(text, uid=777):
    n[0] += 1
    msg = {'message_id': n[0], 'date': 0, 'chat': {'id': uid, 'type': 'private'},
           'from': {'id': uid, 'is_bot': False, 'first_name': 'A'}, 'text': text}
    assert cl.post('/', data=json.dumps({'update_id': n[0], 'message': msg}), headers=H).status_code == 200
    return sent[-1][1]


def feed(c1):
    c15 = prefix() + TF.resample(c1, 900)
    c5 = TF.resample(c1, 300)
    tf = {1: c1, 5: c5, 15: c15}
    now = c1[-1]['t'] + 61
    return (mock.patch.object(D, 'fetch_m15', lambda n_=3000: ([dict(b) for b in c15[-n_:]], 'TEST')),
            mock.patch.object(D, 'fetch_tf', lambda m, n_=3000, timeout=15: ([dict(b) for b in tf[m][-n_:]], 'TEST')),
            mock.patch('time.time', lambda: now))


# 1) signal bor holat
c1 = TF.m1_bars(TF.path(TF.BOUNCE))
a, b, t = feed(c1)
with a, b, t:
    k = len(photos)
    txt = cmd('fibo')
    print(txt, '\n')
    assert 'Auto Fibo' in txt and 'M15' in txt and 'M5' in txt and 'M1' in txt and 'Golden zone' in txt
    assert 'FIBO SIGNAL BOR' in txt and 'BUY' in txt and 'TP2' in txt and 'SNR' in txt, txt
    assert len(photos) == k + 2 and all(p[1][:8] == b'\x89PNG\r\n\x1a\n' for p in photos[-2:])
    open(os.path.join(OUT, 'fibo_m15.png'), 'wb').write(photos[-2][1])
    open(os.path.join(OUT, 'fibo_m5.png'), 'wb').write(photos[-1][1])
    assert 'FIBO' in cmd('/fibo')
    assert 'Kirish yopiq' in cmd('fibo', uid=555)
    assert 'BUY' in cmd('/tarix')
print('OK fibo buyrug\'i: matn, 2 ta grafik, /tarix, begona foydalanuvchi bloklandi')

# 2) M1 tasdiq yo'q: nima kutilayotgani aniq yoziladi
a, b, t = feed(TF.m1_bars(TF.path(((1506, 2016.6),))))
with a, b, t:
    txt = cmd('Fibo')
    print(txt, '\n')
    assert "Fibo signal: hozircha yo'q" in txt and 'M1 da yuqoriga BOS kutiladi' in txt, txt
print('OK signal yo\'q holat: M1 tasdiq kutilmoqda')

# 3) bozor yopiq
a, b, _ = feed(c1)
with a, b, mock.patch('time.time', lambda: c1[-1]['t'] + 3 * 3600):
    assert 'bozori hozir yopiq' in cmd('fibo')
print('OK bozor yopiq')
print('\nFIBO BOT TESTLARI: HAMMASI O\'TDI')
