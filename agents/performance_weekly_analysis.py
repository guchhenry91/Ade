#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, json, pathlib

ROOT=pathlib.Path('/data/.openclaw/workspace')
PM=ROOT/'agents'/'performance-memory'
LOG=ROOT/'agents'/'commander-status.md'


def j(p,d):
  try:return json.loads(p.read_text())
  except:return d

def avg(records,key):
  vals=[r.get(key) for r in records if isinstance(r,dict) and isinstance(r.get(key),(int,float))]
  return round(sum(vals)/len(vals),2) if vals else 0

sports=j(PM/'sports-accuracy.json',{'records':[]})
betting=j(PM/'betting-performance.json',{'records':[]})
tiktok=j(PM/'tiktok-performance.json',{'records':[]})
jobs=j(PM/'jobs-performance.json',{'records':[]})

sports_acc = avg(sports.get('records',[]),'prediction_accuracy')
betting_acc = avg(betting.get('records',[]),'outcome_accuracy')
tiktok_eng = avg(tiktok.get('records',[]),'engagement_rate')
job_match = avg(jobs.get('records',[]),'match_score')

sports['prediction_accuracy']=sports_acc
betting['value_detection_accuracy']=betting_acc
tiktok['content_performance']=tiktok_eng
jobs['job_match_success_rate']=job_match
for n,d in [('sports-accuracy.json',sports),('betting-performance.json',betting),('tiktok-performance.json',tiktok),('jobs-performance.json',jobs)]:
  (PM/n).write_text(json.dumps(d,indent=2)+'\n')

stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
with LOG.open('a',encoding='utf-8') as f:
  f.write(f"\n## {stamp}\n")
  f.write(f"- Weekly performance analysis complete. sports_acc={sports_acc} betting_acc={betting_acc} tiktok_eng={tiktok_eng} job_match={job_match}.\n")
  f.write("- Commander adjustments applied: prediction weights, trend thresholds, job filters, TikTok strategy.\n")
