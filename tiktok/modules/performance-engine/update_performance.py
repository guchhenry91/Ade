#!/usr/bin/env python3
import json, pathlib, datetime as dt, sys
ROOT=pathlib.Path('/data/.openclaw/workspace')
A=ROOT/'tiktok'/'analytics'
A.mkdir(parents=True,exist_ok=True)
P=A/'performance.json'

def load():
  if P.exists(): return json.loads(P.read_text())
  return {'videos':[], 'adaptive': {'hook_style':'curiosity','target_length_sec':20,'caption_style':'mobile-bold-highlight','cta_style':'follow-for-part2'}}

d=load()
video = json.loads(sys.argv[1]) if len(sys.argv)>1 else {}
d['videos'].append(video)
# simple adaptation rules
recent=d['videos'][-10:]
avg_ret=sum(v.get('retention',50) for v in recent)/max(1,len(recent))
if avg_ret < 45:
  d['adaptive']['hook_style']='data-shock'
  d['adaptive']['target_length_sec']=16
elif avg_ret < 55:
  d['adaptive']['hook_style']='contrarian'
  d['adaptive']['target_length_sec']=18
else:
  d['adaptive']['hook_style']='curiosity'
  d['adaptive']['target_length_sec']=20

d['last_updated']=dt.datetime.now(dt.timezone.utc).isoformat()
P.write_text(json.dumps(d,indent=2)+'\n')
print(json.dumps(d['adaptive']))
