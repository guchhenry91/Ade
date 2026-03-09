#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt, json, os, pathlib, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=pathlib.Path('/data/.openclaw/workspace'); UI=ROOT/'mission-control'/'ui'; PUB=ROOT/'mission-control'/'public'; AG=ROOT/'agents'; ST=AG/'status'; AP=AG/'approvals'
LOG=AG/'commander-status.md'; OPLOG=ROOT/'mission-control'/'operations-log.md'; ROUTES=AG/'telegram-routes.json'
STATE=ROOT/'mission-control'/'system-state.json'; GOALS=ROOT/'mission-control'/'goals.json'; LEARN=ROOT/'mission-control'/'learning-data.json'
PMEM=AG/'performance-memory'
PORT=8787; OPERATOR_ID=8385872564
AGENTS=['Commander Agent','Sports Prediction Agent','Betting Intelligence Agent','TikTok Content Agent','Job Research Agent']
TOPIC={'Commander Agent':'commander-control','Sports Prediction Agent':'sports-analytics','Betting Intelligence Agent':'betting-insights','TikTok Content Agent':'tiktok-production','Job Research Agent':'job-opportunities'}

jload=lambda p,d: json.loads(p.read_text()) if p.exists() else d

def parse_md_kv(p:pathlib.Path):
  txt=p.read_text(encoding='utf-8',errors='ignore')
  item={}
  for line in txt.splitlines():
    if ':' in line and not line.strip().startswith('#'):
      k,v=line.split(':',1); item[k.strip()]=v.strip()
  return item

def run(c):
  try:return subprocess.check_output(['bash','-lc',c],text=True,stderr=subprocess.DEVNULL).strip()
  except:return 'unknown'

def tail(p,n=50):
  t=p.read_text(encoding='utf-8',errors='ignore').splitlines() if p.exists() else []
  return [x for x in t if x.strip()][-n:]

def now(): return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

def health():
  uptime=run("uptime -p")
  cpu=run("top -bn1 | awk '/Cpu\\(s\\)/{printf \"%.1f\",100-$8; exit}'")
  mem=run("free | awk '/Mem:/{printf \"%.1f\",($3/$2)*100}'")
  disk=run("df -h /data | awk 'NR==2{print $5}'")
  return {'container_uptime':uptime,'gateway_status':run('openclaw gateway status 2>/dev/null || echo unknown'),'telegram_connection':'active' if ROUTES.exists() else 'unknown','cpu_usage':f'{cpu}%','memory_usage':f'{mem}%','disk_usage':disk,'updated_at':now()}

def agent_cards():
  routes=jload(ROUTES,{})
  out=[]
  for a in AGENTS:
    f=ST/(a.replace(' ','-')+'.json'); d=jload(f,{})
    s='idle' if not d else ('waiting approval' if d.get('status')=='waiting approval' else d.get('status','running'))
    out.append({'name':a,'status':s,'current_task':d.get('task','n/a'),'progress':d.get('progress',0),'last_action':d.get('last_action','n/a'),'last_execution_time':d.get('last_update','unknown'),'next_scheduled_run':d.get('next_action','scheduled'),'assigned_topic':TOPIC[a],'thread_id':routes.get(TOPIC[a],{}).get('thread_id')})
  for f in ST.glob('temp-agent-*.json'):
    d=jload(f,{}) ; d.setdefault('name',f.stem); d.setdefault('assigned_topic','commander-control'); out.append(d)
  return out

def approvals():
  AP.mkdir(parents=True,exist_ok=True)
  arr=[]
  for p in sorted(AP.glob('*.json')):
    d=jload(p,{}) ; d['_file']=p.name
    if str(d.get('decision','')).lower() in {'approved','rejected'}:
      continue
    d.setdefault('risk_level','n/a'); d.setdefault('confidence_score','n/a'); d.setdefault('recommended_action','review')
    arr.append(d)
  for p in sorted(AP.glob('*.md')):
    item=parse_md_kv(p); item['_file']=p.name
    if str(item.get('decision','')).lower() in {'approved','rejected'}:
      continue
    item.setdefault('risk_level','n/a'); item.setdefault('confidence_score','n/a'); item.setdefault('recommended_action','review')
    arr.append(item)
  return arr

def op_log(line):
  OPLOG.parent.mkdir(parents=True,exist_ok=True)
  with OPLOG.open('a',encoding='utf-8') as f:f.write(f"\n- {now()} | {line}")
  with LOG.open('a',encoding='utf-8') as f:f.write(f"\n- [Mission Control] {line}")

def set_decision(file,decision):
  p=AP/file
  if not p.exists(): return False
  agent_name=''
  if p.suffix=='.json':
    d=jload(p,{})
    agent_name=d.get('agent','')
    d['decision']=decision; d['decided_at']=now()
    p.write_text(json.dumps(d,indent=2)+'\n')
  else:
    item=parse_md_kv(p)
    agent_name=item.get('agent','')
    item['decision']=decision; item['decided_at']=now()
    lines=[f"# Approval Request - {p.stem}"]
    for k,v in item.items():
      lines.append(f"{k}: {v}")
    p.write_text("\n".join(lines)+"\n",encoding='utf-8')

  # Sync agent status so UI no longer shows waiting approval after decision
  if 'tiktok' in file.lower() or 'tiktok' in agent_name.lower():
    sf=ST/'TikTok-Content-Agent.json'
    s=jload(sf,{})
    s['status']='running' if decision=='approved' else 'idle'
    s['last_action']=f"approval {decision}"
    s['next_action']='publish package' if decision=='approved' else 'generate revised package'
    s['last_update']=now()
    sf.write_text(json.dumps(s,indent=2)+'\n')

  op_log(f'approval {decision}: {file}') ; return True

def performance_intelligence():
  PMEM.mkdir(parents=True,exist_ok=True)
  sports=jload(PMEM/'sports-accuracy.json',{'prediction_accuracy':0})
  betting=jload(PMEM/'betting-performance.json',{'value_detection_accuracy':0})
  tiktok=jload(PMEM/'tiktok-performance.json',{'content_performance':0})
  jobs=jload(PMEM/'jobs-performance.json',{'job_match_success_rate':0})
  return {
    'sports_prediction_accuracy': sports.get('prediction_accuracy',0),
    'betting_value_detection_accuracy': betting.get('value_detection_accuracy',0),
    'tiktok_content_performance': tiktok.get('content_performance',0),
    'job_match_success_rate': jobs.get('job_match_success_rate',0)
  }

def dynamic_agents_panel():
  out=[]
  for f in sorted(ST.glob('temp-agent-*.json')):
    d=jload(f,{})
    out.append({
      'name': d.get('name',f.stem),
      'task_purpose': d.get('task','n/a'),
      'creation_time': d.get('created_at','unknown'),
      'termination_time': d.get('terminated_at','active')
    })
  return out

def system_telemetry():
  h=health()
  uptime={}
  for a in AGENTS:
    f=ST/(a.replace(' ','-')+'.json')
    if f.exists():
      mins=(dt.datetime.now().timestamp()-f.stat().st_mtime)/60
      uptime[a]=f"{mins:.0f}m since last heartbeat"
    else:
      uptime[a]='unknown'
  return {
    'cpu_usage': h.get('cpu_usage'),
    'memory_usage': h.get('memory_usage'),
    'container_health': 'healthy' if os.path.exists('/.dockerenv') else 'unknown',
    'agent_uptime': uptime
  }

def search(q):
  q=q.lower().strip(); res=[]
  if not q:return res
  for p in ROOT.rglob('*'):
    if p.is_file() and p.suffix in {'.md','.json','.txt','.log'}:
      try:t=p.read_text(encoding='utf-8',errors='ignore')
      except:continue
      if q in t.lower(): res.append({'file':str(p).replace(str(ROOT)+'/','')})
      if len(res)>=50: break
  return res

class H(BaseHTTPRequestHandler):
  def _j(self,d,c=200):
    b=json.dumps(d).encode(); self.send_response(c); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
  def _ctype(self,p):
    if p.suffix=='.html': return 'text/html'
    if p.suffix=='.js': return 'application/javascript'
    if p.suffix=='.css': return 'text/css'
    if p.suffix=='.mp4': return 'video/mp4'
    if p.suffix=='.mov': return 'video/quicktime'
    if p.suffix=='.png': return 'image/png'
    if p.suffix in {'.jpg','.jpeg'}: return 'image/jpeg'
    return 'application/octet-stream'
  def _f(self,p,head=False):
    if not p.exists(): return self.send_error(404)
    c=p.read_bytes()
    ct=self._ctype(p)
    self.send_response(200); self.send_header('Content-Type',ct); self.send_header('Content-Length',str(len(c))); self.end_headers()
    if not head: self.wfile.write(c)
  def do_GET(self):
    from urllib.parse import urlparse,parse_qs
    u=urlparse(self.path)
    if u.path in ['/','/index.html']: return self._f(UI/'index.html')
    if u.path=='/app.js': return self._f(UI/'app.js')
    if u.path=='/styles.css': return self._f(UI/'styles.css')
    if u.path.startswith('/videos/'):
      rel=u.path[len('/videos/'):]
      p=(PUB/'videos'/rel).resolve()
      if not str(p).startswith(str((PUB/'videos').resolve())):
        return self.send_error(403)
      return self._f(p)
    if u.path=='/api/status': return self._j(health())
    if u.path=='/api/agents': return self._j(agent_cards())
    if u.path=='/api/feed': return self._j(tail(LOG,50))
    if u.path=='/api/logs': return self._j(tail(LOG,50))
    if u.path=='/api/approvals': return self._j(approvals())
    if u.path=='/api/notifications':
      items=[{'severity':'critical' if 'error' in x.lower() or 'failure' in x.lower() else ('warning' if 'warn' in x.lower() or 'repair' in x.lower() else 'info'),'message':x} for x in tail(LOG,50)]
      return self._j(items[-20:])
    if u.path=='/api/goals': return self._j(jload(GOALS,{}))
    if u.path=='/api/state': return self._j(jload(STATE,{}))
    if u.path=='/api/performance-intelligence': return self._j(performance_intelligence())
    if u.path=='/api/dynamic-agents': return self._j(dynamic_agents_panel())
    if u.path=='/api/system-telemetry': return self._j(system_telemetry())
    if u.path=='/api/replay': return self._j({'steps':tail(LOG,20),'inputs':['workspace status files','commander log'],'decisions':tail(OPLOG,10),'outputs':tail(LOG,10)})
    if u.path=='/api/agent-detail':
      q=parse_qs(u.query).get('name',[''])[0]; f=ST/(q.replace(' ','-')+'.json'); d=jload(f,{})
      return self._j({'agent':q,'task_description':d.get('task','n/a'),'reasoning_summary':d.get('reasoning_summary','n/a'),'recent_outputs':tail(LOG,8),'latest_logs':tail(LOG,12),'commander_decisions':tail(OPLOG,8)})
    if u.path=='/api/search': return self._j(search(parse_qs(u.query).get('q',[''])[0]))
    return self.send_error(404)
  def do_HEAD(self):
    from urllib.parse import urlparse
    u=urlparse(self.path)
    if u.path in ['/','/index.html']:
      p=UI/'index.html'
    elif u.path=='/app.js':
      p=UI/'app.js'
    elif u.path=='/styles.css':
      p=UI/'styles.css'
    elif u.path.startswith('/videos/'):
      rel=u.path[len('/videos/'):]
      p=(PUB/'videos'/rel).resolve()
      if not str(p).startswith(str((PUB/'videos').resolve())):
        return self.send_error(403)
    else:
      return self.send_error(404)
    if not p.exists() or not p.is_file():
      return self.send_error(404)
    self.send_response(200)
    self.send_header('Content-Type', self._ctype(p))
    self.send_header('Content-Length', str(p.stat().st_size))
    self.end_headers()

  def do_POST(self):
    l=int(self.headers.get('Content-Length','0') or 0); data=json.loads((self.rfile.read(l).decode() if l else '{}') or '{}')
    if int(data.get('operator_id',0))!=OPERATOR_ID: return self._j({'ok':False,'error':'unauthorized'},403)
    if self.path=='/api/approve': return self._j({'ok':set_decision(data.get('file',''),'approved')})
    if self.path=='/api/reject': return self._j({'ok':set_decision(data.get('file',''),'rejected')})
    if self.path=='/api/control': op_log(f"control {data.get('action')} on {data.get('agent')}"); return self._j({'ok':True})
    if self.path=='/api/safe-mode':
      s=jload(STATE,{"safe_mode":True}); on=bool(data.get('enabled',True)); s.update({'safe_mode':on,'betting_automation_paused':on,'job_auto_apply_enabled':False if on else s.get('job_auto_apply_enabled',False),'tiktok_publish_requires_approval':True,'last_updated':now()}); STATE.write_text(json.dumps(s,indent=2)+'\n'); op_log(f'safe_mode={on}'); return self._j({'ok':True,'state':s})
    return self.send_error(404)

if __name__=='__main__':
  UI.mkdir(parents=True,exist_ok=True); AP.mkdir(parents=True,exist_ok=True); ST.mkdir(parents=True,exist_ok=True)
  ThreadingHTTPServer(('0.0.0.0',PORT),H).serve_forever()
