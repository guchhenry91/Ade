const OP=8385872564;const AG=['Commander Agent','Sports Prediction Agent','Betting Intelligence Agent','TikTok Content Agent','Job Research Agent'];
const q=s=>document.querySelector(s),qq=s=>document.querySelectorAll(s);const row=(k,v)=>`<div><b>${k}</b>: ${v}</div>`;
const INTEL_KEY='mission_control_intel_v1';
async function j(u,o){const r=await fetch(u,o);return r.json();}

function loadIntel(){try{return JSON.parse(localStorage.getItem(INTEL_KEY)||'[]')}catch{return[]}}
function saveIntel(items){localStorage.setItem(INTEL_KEY,JSON.stringify(items))}
function addIntel(){
  const title=q('#intelTitle')?.value?.trim(),summary=q('#intelSummary')?.value?.trim(),source=q('#intelSource')?.value?.trim();
  const category=q('#intelCategory')?.value||'AI News',importance=q('#intelImportance')?.value||'📌 reference';
  if(!title||!summary)return;
  const items=loadIntel();
  items.unshift({title,summary,source,category,importance,dateAdded:new Date().toISOString()});
  saveIntel(items);
  q('#intelTitle').value=''; q('#intelSummary').value=''; q('#intelSource').value='';
  renderIntel();
}
function esc(s){return String(s||'').replace(/</g,'&lt;')}
function renderIntel(){
  const items=loadIntel();
  const c=q('#intelFilterCategory')?.value||'All',i=q('#intelFilterImportance')?.value||'All';
  const filtered=items.filter(x=>(c==='All'||x.category===c)&&(i==='All'||x.importance===i));
  const brief=items.slice().sort((a,b)=>new Date(b.dateAdded)-new Date(a.dateAdded)).slice(0,5);
  q('#intelBrief').innerHTML=brief.length?brief.map(x=>`<div class='card'><b>${esc(x.title)}</b> <span class='muted'>${esc(x.importance)}</span>${row('Category',esc(x.category))}${row('Date',new Date(x.dateAdded).toLocaleString())}<div>${esc(x.summary)}</div>${x.source?`<div><a href='${esc(x.source)}' target='_blank'>Source</a></div>`:''}</div>`).join(''):'No intel items yet.';
  q('#intelFeed').innerHTML=filtered.length?filtered.map(x=>`<div class='card'><b>${esc(x.title)}</b> <span class='muted'>${esc(x.importance)}</span>${row('Category',esc(x.category))}${row('Date',new Date(x.dateAdded).toLocaleString())}<div>${esc(x.summary)}</div>${x.source?`<div><a href='${esc(x.source)}' target='_blank'>Source</a></div>`:''}</div>`).join(''):'No matching intel items.';
}

function nav(){qq('.nav').forEach(b=>b.onclick=()=>{qq('.nav').forEach(x=>x.classList.remove('active'));b.classList.add('active');qq('.view').forEach(v=>v.classList.remove('active'));q('#view-'+b.dataset.view).classList.add('active');});}
async function refresh(){
 const [s,a,f,ap,pf,tm,da,n,g]=await Promise.all([j('/api/status'),j('/api/agents'),j('/api/feed'),j('/api/approvals'),j('/api/performance-intelligence'),j('/api/system-telemetry'),j('/api/dynamic-agents'),j('/api/notifications'),j('/api/goals')]);
 q('#activeAgents').textContent=(a||[]).filter(x=>x.status==='running').length; q('#uptime').textContent=s.container_uptime||'-';
 q('#statusDot').className='dot '+((s.gateway_status||'').toLowerCase().includes('ok')?'healthy':'warn');
 q('#systemHealth').innerHTML=[row('CPU',s.cpu_usage),row('Memory',s.memory_usage),row('Container',tm.container_health),row('Gateway',s.gateway_status)].join('');
 const counts={running:0,idle:0,error:0};(a||[]).forEach(x=>counts[x.status]= (counts[x.status]||0)+1);
 q('#agentOverview').innerHTML=[row('Active',counts.running||0),row('Idle',counts.idle||0),row('Failed',counts.error||0)].join('');
 q('#liveFeed').textContent=(f||[]).slice(-20).join('\n'); q('#activityFull').textContent=(f||[]).join('\n');
 q('#timelineBar').innerHTML=(f||[]).slice(-12).map(x=>`<div class='tick'>${x.replace(/</g,'&lt;')}</div>`).join('');
 const cards=(a||[]).map(x=>`<article class='card agent state-${String(x.status||'idle').replace(' ','-')}' data-agent='${x.name}'><h3>${x.name}</h3><div>${row('Task',x.current_task)}${row('Status',x.status)}${row('Progress',(x.progress||0)+'%')}${row('Last activity',x.last_execution_time||'unknown')}</div><div style='margin-top:8px'><button onclick="ctrl('${x.name}','restart')">Restart</button><button onclick="ctrl('${x.name}','pause')">Pause</button></div></article>`).join('');
 q('#agentCards').innerHTML=cards; qq('.agent').forEach(c=>c.onclick=()=>agent(c.dataset.agent));
 const appr=(ap||[]).map(x=>`<div class='card state-waiting-approval'><b>${x.agent||'unknown'}</b>${row('Task',x.task_description||'')}${row('Risk',x.risk_level||'n/a')}${row('Confidence',x.confidence_score||'n/a')}${row('Recommended',x.recommended_action||'review')}<button onclick="dec('${x._file}','approve')">Approve</button><button onclick="dec('${x._file}','reject')">Reject</button></div>`).join('')||'No pending approvals';
 q('#approvalQueue').innerHTML=appr; q('#approvalsFull').innerHTML=appr;
 const perf=[row('Prediction accuracy',pf.sports_prediction_accuracy+'%'),row('Betting accuracy',pf.betting_value_detection_accuracy+'%'),row('TikTok performance',pf.tiktok_content_performance+'%'),row('Job success',pf.job_match_success_rate+'%')].join('');
 q('#performanceInsights').innerHTML=perf; q('#performanceFull').innerHTML=perf;
 const dyn=(da&&da.length?da:[{name:'None',task_purpose:'-',creation_time:'-',termination_time:'-'}]).map(x=>`<div class='card'><b>${x.name}</b>${row('Purpose',x.task_purpose)}${row('Created',x.creation_time)}${row('Terminated',x.termination_time)}<button onclick="spawn('${x.name}')">Assign task</button><button onclick="terminate('${x.name}')">Terminate</button></div>`).join('');
 q('#dynagents').innerHTML=dyn; q('#dynamicFull').innerHTML=dyn;
 const tele=[row('CPU',tm.cpu_usage),row('Memory',tm.memory_usage),row('Container',tm.container_health)].join('')+Object.entries(tm.agent_uptime||{}).map(([k,v])=>row(k,v)).join('');
 q('#telemetry').innerHTML=tele; q('#telemetryFull').innerHTML=tele;
 q('#notificationsFull').innerHTML=(n||[]).map(x=>`<div class='sev-${x.severity}'>• ${x.message}</div>`).join('');
 q('#goalsFull').innerHTML=Object.entries((g.metrics)||{}).map(([k,v])=>row(k,`${v.current}/${v.target}`)).join('');
 q('#controlsFull').innerHTML=`<button onclick='safe(true)'>Enable SAFE MODE</button><button onclick='safe(false)'>Disable SAFE MODE</button><button onclick='ctrl("Commander Agent","restart")'>Restart all agents</button><button onclick='backup()'>Trigger Backup</button>`;
 renderIntel();
}
async function agent(name){const d=await j('/api/agent-detail?name='+encodeURIComponent(name));q('#agentDetail').innerHTML=`<h3>${d.agent}</h3>${row('Task',d.task_description)}${row('Reasoning',d.reasoning_summary)}<pre>${(d.latest_logs||[]).join('\n')}</pre>`;}
async function dec(file,w){await j(w==='approve'?'/api/approve':'/api/reject',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({file,operator_id:OP})});refresh();}
async function ctrl(agent,action){await j('/api/control',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({agent,action,operator_id:OP})});refresh();}
async function safe(v){await j('/api/safe-mode',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({enabled:v,operator_id:OP})});refresh();}
function spawn(name){ctrl(name||'Commander Agent','spawn-temporary-agent')} function terminate(name){ctrl(name||'Commander Agent','terminate')}
function backup(){ctrl('Commander Agent','trigger-backup')}

q('#darkToggle').onclick=()=>document.body.classList.toggle('dark');
q('#quickCmd').onclick=()=>ctrl('Commander Agent','quick-command');
q('#globalSearch').onchange=async e=>{const r=await j('/api/search?q='+encodeURIComponent(e.target.value)); alert(r.slice(0,10).map(x=>x.file).join('\n')||'No match');};
q('#intelAddBtn') && (q('#intelAddBtn').onclick=addIntel);
q('#intelFilterCategory') && (q('#intelFilterCategory').onchange=renderIntel);
q('#intelFilterImportance') && (q('#intelFilterImportance').onchange=renderIntel);

nav(); refresh(); setInterval(refresh,5000);
