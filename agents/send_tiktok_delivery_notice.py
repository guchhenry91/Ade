#!/usr/bin/env python3
from __future__ import annotations
import json, os, pathlib, urllib.parse, urllib.request, sys

ROOT=pathlib.Path('/data/.openclaw/workspace')
RCFG=ROOT/'agents'/'reporting-config.json'
ROUTES=ROOT/'agents'/'telegram-routes.json'

caption=sys.argv[1] if len(sys.argv)>1 else ''
hashtags=sys.argv[2] if len(sys.argv)>2 else ''
link=sys.argv[3] if len(sys.argv)>3 else ''

cfg=json.loads(RCFG.read_text()) if RCFG.exists() else {}
routes=json.loads(ROUTES.read_text())
r=routes.get('tiktok-production',{})
key=cfg.get('reporting_bot',{}).get('env_token_key','TELEGRAM_REPORTING_BOT_TOKEN')
token=os.getenv(key) or cfg.get('reporting_bot',{}).get('token') or os.getenv('TELEGRAM_BOT_TOKEN')
if not token:
    raise SystemExit(1)

text=("@HenryAgentsbot TikTok video ready\n"
      f"Caption: {caption}\n"
      f"Hashtags: {hashtags}\n"
      f"Download: {link}")

q=urllib.parse.urlencode({'chat_id':r.get('chat_id'),'message_thread_id':r.get('message_thread_id') or r.get('thread_id'),'text':text})
urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage?{q}',timeout=20).read()
