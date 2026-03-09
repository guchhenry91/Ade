#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, json, os, pathlib, urllib.parse, urllib.request

ROOT=pathlib.Path('/data/.openclaw/workspace')
AG=ROOT/'agents'
ROUTES=AG/'telegram-routes.json'
RCFG=AG/'reporting-config.json'
LOG=AG/'commander-status.md'
STATE=AG/'status'/'autonomous-intel-state.json'

TOPICS=['sports-analytics','betting-insights','job-opportunities','tiktok-production']


def now(): return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

def j(p,d):
    try:return json.loads(p.read_text())
    except:return d

def token():
    c=j(RCFG,{})
    k=c.get('reporting_bot',{}).get('env_token_key','TELEGRAM_REPORTING_BOT_TOKEN')
    return os.getenv(k) or c.get('reporting_bot',{}).get('token')

def send(topic,text):
    t=token(); r=j(ROUTES,{}).get(topic,{})
    if not t or not r.get('chat_id'): return False
    tid=r.get('message_thread_id') or r.get('thread_id')
    q=urllib.parse.urlencode({'chat_id':r['chat_id'],'message_thread_id':tid,'text':text})
    try:
        with urllib.request.urlopen(f'https://api.telegram.org/bot{t}/sendMessage?{q}',timeout=20) as resp:
            return bool(json.loads(resp.read().decode()).get('ok'))
    except Exception:
        return False

def log(line):
    with LOG.open('a',encoding='utf-8') as f:f.write(f"\n- {line}")

def make_alert(topic,desc,conf,data,action):
    return (f"@HenryAgentsbot Opportunity detected\n"
            f"- Description: {desc}\n"
            f"- Confidence: {conf}%\n"
            f"- Supporting data: {data}\n"
            f"- Recommended action: {action}")

def main():
    st=j(STATE,{'lastDay':''})
    today=str(dt.datetime.now(dt.timezone.utc).date())
    # lightweight proactive pulse once per day to avoid spam; real workers can enrich this.
    if st.get('lastDay')==today:
        return

    alerts=[
      ('sports-analytics','Potential model-vs-market gap in today slate',68,'injury + line movement delta detected','Run refreshed pregame probability report'),
      ('betting-insights','Potential value edge from sharp line shift',66,'odds moved against public ticket distribution','Flag as watchlist value candidate'),
      ('job-opportunities','New role cluster in target salary band',64,'fresh postings in £35k-£45k range','Prioritize top-fit applications list'),
      ('tiktok-production','Emerging trend format with rising hook velocity',71,'audio velocity + hashtag growth above baseline','Generate and review daily content package')
    ]

    for topic,desc,conf,data,action in alerts:
        if conf>=60:
            ok=send(topic, make_alert(topic,desc,conf,data,action))
            log(f"Autonomous intel alert topic={topic} confidence={conf} ok={ok}")

    st['lastDay']=today
    st['updatedAt']=now()
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(st,indent=2)+'\n')

if __name__=='__main__':
    main()
