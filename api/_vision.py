"""Skrinshot tahlili: foydalanuvchi yuborgan grafik rasmini AI (Claude) ICT/SMC bo'yicha o'qiydi.

Muhit o'zgaruvchilari:
  ANTHROPIC_API_KEY - console.anthropic.com dan olingan kalit (MAJBURIY, bo'lmasa funksiya o'chiq)
  AI_MODEL          - ixtiyoriy, standart: claude-sonnet-5-5
"""
import base64
import os

import requests

API = 'https://api.anthropic.com/v1/messages'
MODEL = os.environ.get('AI_MODEL') or 'claude-sonnet-5-5'

SYSTEM = """Sen tajribali oltin (XAUUSD) va forex treyderisan, ICT va SMC metodlarini chuqur bilasan.
Foydalanuvchi grafik skrinshotini yuboradi. Faqat rasmda HAQIQATAN ko'rinib turgan narsaga tayan:
ko'rinmagan narx yoki daraja to'qib chiqarma. Rasm o'qilmasa yoki grafik bo'lmasa, shuni ochiq ayt.

Javobni O'ZBEK tilida (lotin yozuvida), Telegram uchun qisqa va tartibli yoz (Markdown ishlatma,
** yoki # belgilarisiz; bo'limlarni emoji bilan boshla). Jami 2500 belgidan oshmasin. Tuzilma:

📌 Nima ko'rinyapti: instrument, taymfreym (ko'rinsa), joriy narx.
📈 Struktura: trend, oxirgi BOS/MSS (CHoCH), premium yoki discount.
💧 Likvidlik: qaysi high/low'lar (EQH/EQL, sessiya high/low) olingan yoki hali olinmagan.
🧱 PD arraylar: FVG, Order Block, breaker; ularning taxminiy narxlari.
🎯 Ssenariy: ICT bo'yicha eng ehtimolli harakat. Agar sharoitlar to'liq bo'lsa, aniq
   kirish / SL / TP va nima uchun; bo'lmasa, nima tasdiq kutish kerakligini ayt.
⚠️ Xavf: 1-2 jumla. Hech qachon kafolat berma."""


def enabled():
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def media_type(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    return 'image/jpeg'


def analyze(image, question='', context='', timeout=50):
    """Rasm (bayt) -> tahlil matni. Xato bo'lsa RuntimeError."""
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY o'rnatilmagan")
    text = 'Shu grafikni ICT va SMC bo\'yicha tahlil qil.'
    if question:
        text += '\nFoydalanuvchi savoli: ' + question[:500]
    if context:
        text += ('\n\nMa\'lumot uchun, botning jonli XAUUSD ma\'lumotlari (agar rasm oltin bo\'lsa, '
                 'solishtirish uchun ishlat; boshqa instrument bo\'lsa e\'tiborsiz qoldir):\n' + context)
    body = {
        'model': MODEL, 'max_tokens': 1500, 'system': SYSTEM,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type(image),
                                         'data': base64.b64encode(image).decode('ascii')}},
            {'type': 'text', 'text': text}]}],
    }
    r = requests.post(API, json=body, timeout=timeout,
                      headers={'x-api-key': key, 'anthropic-version': '2023-06-01',
                               'content-type': 'application/json'})
    try:
        js = r.json()
    except ValueError:
        raise RuntimeError('AI javobi o\'qilmadi (HTTP %s)' % r.status_code)
    if r.status_code != 200:
        msg = (js.get('error') or {}).get('message') if isinstance(js, dict) else None
        raise RuntimeError('AI xatosi (HTTP %s): %s' % (r.status_code, msg or js))
    out = ''.join(b.get('text', '') for b in js.get('content', []) if b.get('type') == 'text').strip()
    if not out:
        raise RuntimeError("AI bo'sh javob qaytardi")
    return out.replace('**', '').replace('__', '')
