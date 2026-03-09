#!/usr/bin/env python3
from __future__ import annotations
import json, os, pathlib, sys, urllib.parse, urllib.request

ROOT=pathlib.Path('/data/.openclaw/workspace')
RCFG=ROOT/'agents'/'reporting-config.json'
ROUTES=ROOT/'agents'/'telegram-routes.json'

msg=' '.join(sys.argv[1:]).strip()
if not msg:
    raise SystemExit(0)
cfg=json.loads(RCFG.read_text()) if RCFG.exists() else {}
routes=json.loads(ROUTES.read_text())
rt=routes['commander-control']
key=cfg.get('reporting_bot',{}).get('env_token_key','TELEGRAM_REPORTING_BOT_TOKEN')
token=os.getenv(key) or cfg.get('reporting_bot',{}).get('token')
if not token:
    raise SystemExit(0)
q=urllib.parse.urlencode({'chat_id':rt['chat_id'],'message_thread_id':rt.get('message_thread_id') or rt.get('thread_id'),'text':msg})
urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage?{q}', timeout=20).read()
