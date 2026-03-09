#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, json, os, pathlib, urllib.parse, urllib.request

ROOT=pathlib.Path('/data/.openclaw/workspace'); AG=ROOT/'agents'; ST=AG/'status'; AP=AG/'approvals'
ROUTES=AG/'telegram-routes.json'; BIND=AG/'agent-topic-bindings.json'; LOG=AG/'commander-status.md'; RCFG=AG/'reporting-config.json'
STATE=ST/'notification-state.json'; TAG='@HenryAgentsbot'

LOG_FILES={
 'Sports Prediction Agent':'sports-worker.log',
 'Betting Intelligence Agent':'betting-worker.log',
 'TikTok Content Agent':'tiktok-worker.log',
 'Job Research Agent':'jobs-worker.log',
 'Commander Agent':'commander-worker.log',
}


def j(p,d):
  try:return json.loads(p.read_text())
  except:return d

def get_reporting_token():
  cfg=j(RCFG,{})
  key=cfg.get('reporting_bot',{}).get('env_token_key','TELEGRAM_REPORTING_BOT_TOKEN')
  return os.getenv(key) or cfg.get('reporting_bot',{}).get('token') or os.getenv('TELEGRAM_REPORTING_BOT_TOKEN')

def send(topic,msg):
  routes=j(ROUTES,{})
  r=routes.get(topic,{})
  token=get_reporting_token()
  if not token or not r.get('chat_id') or not (r.get('message_thread_id') or r.get('thread_id')):
    return False
  tid=r.get('message_thread_id') or r.get('thread_id')
  q=urllib.parse.urlencode({'chat_id':r['chat_id'],'message_thread_id':tid,'text':msg})
  try:
    with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage?{q}',timeout=20) as resp:
      return bool(json.loads(resp.read().decode()).get('ok'))
  except:
    return False

def log(line):
  with LOG.open('a',encoding='utf-8') as f:f.write(f"\n- {line}")

def main():
  ST.mkdir(parents=True,exist_ok=True)
  state=j(STATE,{'logs':{},'approvals':[]})
  binds=j(BIND,{})

  for agent,lf in LOG_FILES.items():
    p=ST/lf
    if not p.exists():
      continue
    mt=p.stat().st_mtime
    prev=state['logs'].get(lf,0)
    if mt>prev:
      topic=binds.get(agent,'commander-control')
      msg=f"{TAG} Task completed by {agent}"
      if send(topic,msg):
        log(f"Notification sent: {agent} -> {topic} | completion")
      state['logs'][lf]=mt

  seen=set(state.get('approvals',[]))
  for p in sorted(AP.glob('tiktok-*.md')):
    if p.name in seen: continue
    topic=binds.get('TikTok Content Agent','tiktok-production')
    txt=p.read_text(encoding='utf-8',errors='ignore')
    task='Publish TikTok video?'
    for line in txt.splitlines():
      if line.lower().startswith('task description:'): task=line.split(':',1)[1].strip(); break
    msg=f"{TAG} Approval required: {task}"
    if send(topic,msg):
      log(f"Notification sent: TikTok approval -> {topic} | {p.name}")
    seen.add(p.name)
  state['approvals']=sorted(seen)

  STATE.write_text(json.dumps(state,indent=2)+'\n')

if __name__=='__main__':
  main()
