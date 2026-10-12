"""ICT, grafik rasm va skrinshot tahlili buyruqlari testi (soxta Telegram va soxta AI bilan).
Ishga tushirish: python3 tests/test_bot_ict.py   (grafiklar tests/_out/ ga saqlanadi)"""
import json
import os
import sys
from unittest import mock

os.environ.update(BOT_TOKEN='123:ABC', ADMIN_ID='777', WEBHOOK_SECRET='sekret')
os.environ.pop('ANTHROPIC_API_KEY', None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
sys.path.insert(0, os.path.dirname(__file__))
import _data as D  # noqa: E402
import _ict as ICT  # noqa: E402
import _smc as S  # noqa: E402
import _vision as V  # noqa: E402
import index as B  # noqa: E402
import test_smc as T  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), '_out')
os.makedirs(OUT, exist_ok=True)

series = T.rw(3000, 1)
P = S.Prep(series, B.ICT_CFG)
i_sig = next(i for i in range(900, 2900) if ICT.find_signal(P, i, B.ICT_CFG)[0])
c_sig = series[:i_sig + 1]

sent, photos = [], []
B.bot.send_message = lambda chat, text, **k: sent.append((chat, text))


def fake_photo(chat, f, caption=None, **k):
    data = f.read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    photos.append((chat, data, caption))


B.bot.send_photo = fake_photo
cl = B.app.test_client()
H = {'content-type': 'application/json', 'X-Telegram-Bot-Api-Secret-Token': 'sekret'}
n = [0]


def post(msg, uid=777):
    n[0] += 1
    msg = {'message_id': n[0], 'date': 0, 'chat': {'id': uid, 'type': 'private'},
           'from': {'id': uid, 'is_bot': False, 'first_name': 'A'}, **msg}
    r = cl.post('/', data=json.dumps({'update_id': n[0], 'message': msg}), headers=H)
    assert r.status_code == 200


def feed(c):
    return mock.patch.object(D, 'fetch_m15', lambda n_=3000: ([dict(b) for b in c], 'TEST'))


def at(c):
    return mock.patch('time.time', lambda: c[-1]['t'] + 960)


with feed(c_sig), at(c_sig):
    post({'text': 'ict'})
    txt = [t for _, t in sent][-1]
    print(txt, '\n')
    assert 'ICT tahlil' in txt and 'ICT SIGNAL BOR' in txt and 'Kirish' in txt and 'Killzone' in txt and 'FVG' in txt
    assert photos and photos[-1][0] == 777
    open(os.path.join(OUT, 'ict_signal.png'), 'wb').write(photos[-1][1])
    k = len(photos)
    post({'text': '/ict'})
    assert len(photos) == k + 1
    post({'text': 'rasm'})
    assert len(photos) == k + 2 and 'XAUUSD' in photos[-1][2]
    open(os.path.join(OUT, 'smc_chart.png'), 'wb').write(photos[-1][1])
    post({'text': 'ict'}, uid=555)
    assert 'Kirish yopiq' in sent[-1][1]
    print('OK ict / rasm buyruqlari, grafik PNG, begona foydalanuvchi bloklandi')

c_none = series[:1500]
with feed(c_none), at(c_none):
    post({'text': 'ICT'})
    txt = [t for _, t in sent][-1]
    print(txt, '\n')
    assert "ICT signal: hozircha yo'q" in txt and 'SELL' in txt and 'BUY' in txt and 'Midnight Open' in txt
    open(os.path.join(OUT, 'ict_nosignal.png'), 'wb').write(photos[-1][1])
    print('OK signal yo\'q holat')

with feed(series[:5000]), at(series):
    post({'text': '/ictbacktest'})
    assert 'ICT backtest' in sent[-1][1], sent[-1][1]
    print('OK /ictbacktest')

# skrinshot: kalit yo'q bo'lsa tushuntiradi
photo = {'photo': [{'file_id': 'a', 'file_unique_id': 'a', 'width': 90, 'height': 90},
                   {'file_id': 'BIG', 'file_unique_id': 'b', 'width': 1280, 'height': 720}], 'caption': 'sotaymi?'}
post(photo)
assert 'ANTHROPIC_API_KEY' in sent[-1][1]

# kalit bor: rasm yuklanadi va AI ga yuboriladi
got = {}


def fake_analyze(img, question='', context='', timeout=50):
    got.update(img=img, q=question, ctx=context)
    return '📌 XAUUSD M15\n🎯 Ssenariy: **Asian high** sweep, <FVG> kutish'


with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'x'}), feed(c_sig), at(c_sig), \
        mock.patch.object(B.bot, 'get_file', lambda fid: type('F', (), {'file_path': 'p/' + fid})()), \
        mock.patch.object(B.bot, 'download_file', lambda p: b'\x89PNG\r\n\x1a\n' + p.encode()), \
        mock.patch.object(V, 'analyze', fake_analyze):
    post(photo)
    assert got['img'].endswith(b'p/BIG') and got['q'] == 'sotaymi?' and 'XAUUSD hozir' in got['ctx'], got
    assert 'Skrinshot tahlili' in sent[-1][1] and '&lt;FVG&gt;' in sent[-1][1]
    post({'document': {'file_id': 'DOC', 'file_unique_id': 'd', 'mime_type': 'image/png', 'file_size': 1000}})
    assert got['img'].endswith(b'p/DOC')
    post({'document': {'file_id': 'PDF', 'file_unique_id': 'p', 'mime_type': 'application/pdf', 'file_size': 1000}})
    assert 'Faqat rasm' in sent[-1][1]
print('OK skrinshot tahlili (photo, rasm-fayl, kalitsiz holat)')

# AI so'rovi formati
with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k'}):
    calls = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {'content': [{'type': 'text', 'text': 'tahlil **matni**'}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.update(url=url, body=json, headers=headers)
        return R()
    with mock.patch.object(V.requests, 'post', fake_post):
        out = V.analyze(b'\xff\xd8\xff\xe0jpeg', 'savol', 'ctx')
    assert out == 'tahlil matni'
    blk = calls['body']['messages'][0]['content']
    assert blk[0]['source']['media_type'] == 'image/jpeg' and calls['headers']['x-api-key'] == 'k'
    assert 'savol' in blk[1]['text'] and 'ctx' in blk[1]['text']
print('OK AI so\'rovi formati')
print('\nICT/RASM TESTLARI: HAMMASI O\'TDI')
