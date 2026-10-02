"""Signallarni saqlash va takrorlanishdan himoya (dedup).
Upstash Redis (Vercel Marketplace) bo'lsa o'shani, bo'lmasa /tmp ni ishlatadi.
"""
import json
import logging
import os
import time

import requests

log = logging.getLogger('signalbot')
URL = (os.environ.get('UPSTASH_REDIS_REST_URL') or os.environ.get('KV_REST_API_URL') or '').rstrip('/')
TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN') or os.environ.get('KV_REST_API_TOKEN') or ''
TMP = '/tmp/signalbot_store.json'


def persistent():
    return bool(URL)


def _redis(*cmd):
    r = requests.post(URL, headers={'Authorization': 'Bearer %s' % TOKEN}, json=list(cmd), timeout=5)
    r.raise_for_status()
    return r.json().get('result')


def _load():
    try:
        with open(TMP) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(d):
    with open(TMP, 'w') as f:
        json.dump(d, f)


def claim(key):
    """True qaytarsa, bu signal birinchi marta ko'rilyapti (yuborish kerak)."""
    try:
        if URL:
            return int(_redis('HSETNX', 'sent', key, int(time.time()))) == 1
        d = _load()
        sent = d.setdefault('sent', {})
        if key in sent:
            return False
        sent[key] = int(time.time())
        _save(d)
        return True
    except Exception:  # noqa: BLE001
        log.exception('claim')
        return True


def save_signal(sig):
    rec = json.dumps({**sig, 'saved': int(time.time())}, ensure_ascii=False)
    try:
        if URL:
            _redis('HSET', 'signals', sig['key'], rec)
        else:
            d = _load()
            d.setdefault('signals', {})[sig['key']] = rec
            _save(d)
    except Exception:  # noqa: BLE001
        log.exception('save_signal')


def last_signals(n=10):
    try:
        if URL:
            flat = _redis('HGETALL', 'signals') or []
            vals = [flat[i + 1] for i in range(0, len(flat), 2)]
        else:
            vals = list(_load().get('signals', {}).values())
        recs = [json.loads(v) for v in vals]
        recs.sort(key=lambda r: r.get('saved', 0), reverse=True)
        return recs[:n]
    except Exception:  # noqa: BLE001
        log.exception('last_signals')
        return []
