"""Server testi: soxta ma'lumot va soxta Telegram bilan. Ishga tushirish: python3 tests/test_bot.py"""
import json, os, sys
from unittest import mock
os.environ.update(BOT_TOKEN='123:ABC', ADMIN_ID='777', WEBHOOK_SECRET='sekret', MIN_SCORE='7')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.dirname(__file__))
for f in ('/tmp/signalbot_store.json',):
    if os.path.exists(f): os.remove(f)
import test_smc as T, _smc as S, _data as D
import index as I

series = T.rw(2600, 1)
P = S.Prep(series, I.CFG)
i_sig = next(i for i in range(900, 2500) if S.find_signal(P, i, I.CFG)[0])
c_sig = series[:i_sig + 1]
sent = []
I.bot.send_message = lambda chat, text, **k: sent.append((chat, text))
now_ts = c_sig[-1]['t'] + 900 + 60
cl = I.app.test_client()
H = {'content-type': 'application/json', 'X-Telegram-Bot-Api-Secret-Token': 'sekret'}
n = [0]
def cmd(text, uid=777):
    n[0] += 1
    r = cl.post('/', data=json.dumps({'update_id': n[0], 'message': {'message_id': n[0], 'date': 0, 'chat': {'id': uid, 'type': 'private'},
              'from': {'id': uid, 'is_bot': False, 'first_name': 'A'}, 'text': text}}), headers=H)
    assert r.status_code == 200
    return sent[-1][1] if sent else ''

with mock.patch.object(D, 'fetch_m15', lambda n_=3000: ([dict(b) for b in c_sig], 'TEST')), mock.patch('time.time', lambda: now_ts):
    assert cl.get('/?scan=1&key=xato').status_code == 403
    assert cl.get('/?ulash=1').status_code == 403
    assert cl.post('/', data='{}', headers={'content-type': 'application/json'}).status_code == 403
    r = cl.get('/?scan=1&key=sekret').get_json()
    assert r['ok'] and r['signal'] and r['sent'], r
    first = [t for c, t in sent if c == 777][-1]
    assert ('SELL' in first or 'BUY' in first) and 'SL:' in first and 'TP:' in first and 'ball' in first
    print(first, '\n')
    k = len(sent)
    r = cl.get('/?scan=1&key=sekret').get_json()
    assert r['signal'] and not r['sent'] and len(sent) == k, 'takroriy signal yuborilmasligi kerak'
    print('OK dedup: ikkinchi skanerda signal qayta yuborilmadi')
    assert 'Hozir signal bor' in cmd('/signal')
    assert 'Kirish yopiq' in cmd('/signal', uid=555)
    z = cmd('/zones'); assert 'SNR zonalar' in z and 'Likvidlik' in z
    t = cmd('/tarix'); assert 'Oxirgi signallar' in t
    assert 'min_score' in cmd('/sozlama')
    print('OK buyruqlar: /signal /zones /tarix /sozlama, begona foydalanuvchi bloklandi')

# signal bo'lmagan payt va yopiq bozor
c_none = series[:600 + 280]
with mock.patch.object(D, 'fetch_m15', lambda n_=3000: ([dict(b) for b in series[:1000]], 'TEST')), mock.patch('time.time', lambda: series[999]['t'] + 960):
    txt = cmd('/signal')
    print(txt.replace('\n', ' | '))
    assert 'Hozir signal' in txt or 'signal bor' in txt
with mock.patch.object(D, 'fetch_m15', lambda n_=3000: ([dict(b) for b in series[:1000]], 'TEST')), mock.patch('time.time', lambda: series[999]['t'] + 960 + 7200):
    r = cl.get('/?scan=1&key=sekret').get_json(); assert 'bozor yopiq' in r['msg'], r
    print('OK bozor yopiq: skaner jim turadi')

big = T.rw(5000, 5)
with mock.patch.object(D, 'fetch_m15', lambda n_=3000: ([dict(b) for b in big], 'TEST')), mock.patch('time.time', lambda: big[-1]['t'] + 960):
    out = cmd('/backtest'); print(out)
    assert 'Backtest' in out

# ma'lumot parserlari
tw = {'status': 'ok', 'values': [{'datetime': '2024-01-02 10:15:00', 'open': '2050.1', 'high': '2051.0', 'low': '2049.5', 'close': '2050.7'},
                                  {'datetime': '2024-01-02 10:00:00', 'open': '2050.0', 'high': '2050.5', 'low': '2049.8', 'close': '2050.1'}]}
p = D.parse_twelve(tw); assert [b['t'] for b in p] == [1704189600, 1704190500] and p[1]['h'] == 2051.0
try:
    D.parse_twelve({'status': 'error', 'message': 'kalit xato'}); assert False
except RuntimeError as e:
    assert 'kalit xato' in str(e)
y = {'chart': {'result': [{'timestamp': [1704189600, 1704190500, 1704191400], 'indicators': {'quote': [{'open': [1, 2, None], 'high': [2, 3, None], 'low': [1, 1, None], 'close': [2, 2, None]}]}}]}}
assert len(D.parse_yahoo(y)) == 2
assert len(D.drop_incomplete([{'t': 0, 'o': 1, 'h': 1, 'l': 1, 'c': 1}, {'t': 900, 'o': 1, 'h': 1, 'l': 1, 'c': 1}], now=1000)) == 1
print('OK ma\'lumot parserlari (Twelve Data, Yahoo)')
print('\nSERVER TESTLARI: HAMMASI O\'TDI')
