#!/usr/bin/env python3
"""Lightweight Telegram topic detector and route updater.

Requires TELEGRAM_BOT_TOKEN in env.
Polls getUpdates, detects forum topic thread ids in target supergroup,
and updates agents/telegram-routes.json.
"""
from __future__ import annotations
import json
import os
import pathlib
import urllib.request

WORKSPACE = pathlib.Path('/data/.openclaw/workspace')
ROUTES_PATH = WORKSPACE / 'agents' / 'telegram-routes.json'
STATUS_LOG = WORKSPACE / 'agents' / 'commander-status.md'
TARGET_CHAT_ID = -1003659714036
TOPICS = [
    'sports-analytics',
    'tiktok-production',
    'job-opportunities',
    'betting-insights',
    'commander-control',
]


def load_routes():
    if ROUTES_PATH.exists():
        return json.loads(ROUTES_PATH.read_text())
    return {t: {'chat_id': TARGET_CHAT_ID, 'thread_id': None} for t in TOPICS}


def save_routes(routes):
    ROUTES_PATH.write_text(json.dumps(routes, indent=2) + '\n')


def tg_get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


def detect():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        return {'ok': False, 'error': 'TELEGRAM_BOT_TOKEN missing', 'updates': 0, 'changes': 0}

    updates_url = f'https://api.telegram.org/bot{token}/getUpdates?limit=100'
    data = tg_get(updates_url)
    updates = data.get('result', [])
    routes = load_routes()
    changes = 0

    for u in updates:
        msg = u.get('message') or u.get('channel_post') or {}
        if msg.get('chat', {}).get('id') != TARGET_CHAT_ID:
            continue
        thread_id = msg.get('message_thread_id')
        if not thread_id:
            continue
        # Determine topic label from topic creation name or first text token.
        topic_name = None
        if isinstance(msg.get('forum_topic_created'), dict):
            topic_name = msg['forum_topic_created'].get('name')
        if not topic_name and isinstance(msg.get('text'), str):
            t = msg['text'].strip().lower()
            if t in TOPICS:
                topic_name = t
        if topic_name in routes and routes[topic_name].get('thread_id') != thread_id:
            routes[topic_name]['thread_id'] = thread_id
            changes += 1

    save_routes(routes)
    return {'ok': True, 'updates': len(updates), 'changes': changes}


if __name__ == '__main__':
    result = detect()
    STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with STATUS_LOG.open('a', encoding='utf-8') as f:
        f.write(f"\n- Telegram topic listener run: {result}\n")
    print(result)
