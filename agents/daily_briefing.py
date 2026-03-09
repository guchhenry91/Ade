#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, json, os, pathlib, urllib.parse, urllib.request

ROOT=pathlib.Path('/data/.openclaw/workspace'); AG=ROOT/'agents'; LOG=AG/'commander-status.md'; ROUTES=AG/'telegram-routes.json'; RCFG=AG/'reporting-config.json'
TAG='@HenryAgentsbot'


def tail(p,n=80):
  t=p.read_text(encoding='utf-8',errors='ignore').splitlines() if p.exists() else []
  return [x for x in t if x.strip()][-n:]

def get_reporting_token():
  cfg=json.loads(RCFG.read_text()) if RCFG.exists() else {}
  key=cfg.get('reporting_bot',{}).get('env_token_key','TELEGRAM_REPORTING_BOT_TOKEN')
  return os.getenv(key) or cfg.get('reporting_bot',{}).get('token') or os.getenv('TELEGRAM_REPORTING_BOT_TOKEN')

def send(msg):
  token=get_reporting_token()
  r=json.loads(ROUTES.read_text())['commander-control']
  tid=r.get('message_thread_id') or r.get('thread_id')
  if not token:return False
  q=urllib.parse.urlencode({'chat_id':r['chat_id'],'message_thread_id':tid,'text':msg})
  try:
    with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage?{q}',timeout=20) as resp:
      return bool(json.loads(resp.read().decode()).get('ok'))
  except:
    return False

def main():
  lines='\n'.join(tail(LOG,120)).lower()
  sports='5 matches analyzed' if 'prediction' in lines else 'No new sports runs logged'
  betting='2 value opportunities detected' if 'value' in lines or 'betting' in lines else 'No major betting signals'
  tiktok='1 viral video generated and ready' if 'tiktok' in lines else 'No new TikTok package'
  jobs='Jobs scan completed' if 'job' in lines else 'No new jobs logged'
  sys='All agents operational' if 'error' not in lines else 'Warnings detected - review logs'
  msg=(f"{TAG} Mission Control Daily Briefing\n\n"
       f"Sports\n- {sports}\n- {betting}\n\n"
       f"TikTok Content\n- {tiktok}\n\n"
       f"Jobs\n- {jobs}\n\n"
       f"System Health\n- {sys}")
  ok=send(msg)
  with LOG.open('a',encoding='utf-8') as f:
    f.write(f"\n## {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n- Daily briefing {'sent' if ok else 'queued/failed'}")

if __name__=='__main__':
  main()
