import {ContinuumBridge} from './bridge.js';
import {t,getLang,useLang} from './i18n.js';
const $=s=>document.querySelector(s);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n};
const bridge=new ContinuumBridge();
const state={view:'chat',connected:false,working:false,sessionId:'demo',projectId:null,projects:[],history:[],memory:[],lab:{},core:{},abort:null,assistant:null,runtime:null,lastTurn:{opened:[],paths:[],writes:[]},pro:{live:{},panels:null},followups:new Set(),pendingQueue:[],pendingAtts:[],turnOpen:false,threadCache:{},proMode:(()=>{try{return localStorage.getItem('continuumProMode')==='1'}catch{return false}})(),lang:getLang(),statusKey:null,statusNative:'',sessionStarted:false};
function proCapture(kind,d){state.pro.live[kind]=d;if(state.proMode)renderProPanels()}
async function refreshProPanels(){try{state.pro.panels=await bridge.proPanels(state.sessionId)}catch(e){state.pro.panels={error:String(e&&e.message||e).slice(0,200)}}renderProPanels();fillModes()}
function proRow(parent,k,v){const r=el('div','rail-mini-row');r.append(el('span','',String(k)));r.append(el('strong','',String(v).slice(0,120)));parent.append(r)}
function proRaw(parent,obj){const det=document.createElement('details');det.className='raw-wrap';const sum=document.createElement('summary');sum.textContent='raw';det.append(sum);const pre=el('pre','raw-data');try{pre.textContent=JSON.stringify(obj||{},null,1).slice(0,3000)}catch{pre.textContent=String(obj)}det.append(pre);parent.append(det)}
function renderProPanels(){
const host=$('#proPanelsBody');if(!host)return;host.replaceChildren();
const live=state.pro.live,pan=state.pro.panels||{};
if(pan.error)host.append(el('p','rail-info',t('proPanelsErr')+pan.error));
const sec=t=>{const s=el('div','pro-sec');s.append(el('h4','',t));host.append(s);return s};
const sess=(pan.session&&pan.session.session)||{};
let s=sec(t('proSess'));
proRow(s,'msp_session',pan.msp_session||'(ninguna)');
proRow(s,'status',sess.status||(live.status||{}).status||'—');
proRow(s,'model',sess.modelId||(live.usage||{}).modelId||'—');
proRow(s,t('proTurns'),sess.turnCount==null?'—':sess.turnCount);
proRow(s,'workspace',sess.workspaceRoot||'—');
proRow(s,'approvalMode',JSON.stringify(sess.approvalMode!=null?sess.approvalMode:'—'));
proRaw(s,pan.session);
s=sec(t('proUsage'));
const u=live.usage||{};proRow(s,'promptTokens',u.promptTokens==null?'—':u.promptTokens);proRow(s,'totalTokens',u.totalTokens==null?'—':u.totalTokens);
const sub=(pan.usage||{}).usage||live['usage-changed']||{};
proRow(s,'tier',sub.tier||'—');proRow(s,'window',JSON.stringify(sub.window||'—'));proRow(s,'weekly',JSON.stringify(sub.weekly||'—'));
proRaw(s,{turn:u,subscription:pan.usage||null});
s=sec(t('proCtx'));const c=live.context||{};
proRow(s,'windowTokens',c.windowTokens==null?'—':c.windowTokens);proRow(s,'usedTokens',c.usedTokens==null?'—':c.usedTokens);proRow(s,'pressure',c.pressure==null?'—':c.pressure);
proRaw(s,c);
s=sec(t('proTasks'));const tasks=(pan.tasks||{}).tasks||[];
if(!tasks.length)s.append(el('p','rail-info',t('proNoTasks')));
for(const t of tasks.slice(0,20))proRow(s,t.tool||t.kind||'task',(t.taskId||'').slice(0,8)+' · '+(t.status||''));
proRaw(s,pan.tasks);
s=sec(t('proTodos'));const td=live.todos||{};const items=td.items||[];
if(!items.length)s.append(el('p','rail-info',t('proEmptyList')));
for(const it of items.slice(0,30))proRow(s,it.status||'?',(it.text||'').slice(0,80));
proRaw(s,td);
s=sec(t('proPend'));const pp=pan.pending||{};const pend=Array.isArray(pp)?pp:(pp.approvals||pp.pending||pp.requests||pp.items||[]);
if(!pend.length)s.append(el('p','rail-info',t('proNone')));
for(const p2 of pend.slice(0,20))proRow(s,String(p2.approvalId||p2.id||'?').slice(0,12),p2.toolName||p2.method||'');
proRaw(s,pan.pending);
}
const CLOSED_TIERS=["none","minimal","low","medium","high","xhigh","max","ultra"];
async function proOp(op,args){
const box=$('#ctlResult');
try{const r=await bridge.proControl(state.sessionId,op,args||{});
if(box){box.replaceChildren();const pre=el('pre','raw-data');pre.textContent=op+': '+JSON.stringify(r,null,1).slice(0,2000);box.append(pre)}
if(op!=='listModels'&&op!=='listSkills')refreshProPanels();
return r}catch(e){if(box){box.replaceChildren();box.append(el('p','rail-info',t('errPrefix')+String(e&&e.message||e).slice(0,300)+(String(e&&e.message||e).includes('not loaded on this host')?t('proSessionHint'):'')))}throw e}}
let ctlCatalog=[];
async function refreshProControls(){
const ms=$('#ctlModel');if(!ms)return;
try{const cat=await bridge.proControl(state.sessionId,'listModels',{});
ctlCatalog=((cat&&cat.result&&cat.result.models)||[]);
ms.replaceChildren();
for(const m of ctlCatalog){const o=document.createElement('option');o.value=m.modelId;o.textContent=(m.displayLabel||m.modelId)+(m.isActive?' ●':'');if(m.isActive)o.selected=true;ms.append(o)}
if(!ctlCatalog.length){const o=document.createElement('option');o.textContent=t('proNoModels');ms.append(o)}
const active=ctlCatalog.find(m=>m.isActive)||{};
fillEffort(active.variants||active.reasoningEffortVariants||null);
}catch(e){ms.replaceChildren();const o=document.createElement('option');o.textContent=t('proNoModels');ms.append(o);fillEffort(null)}
const sk=$('#ctlSkills');if(!sk)return;
try{const r=await bridge.proControl(state.sessionId,'listSkills',{});
const rows=((r&&r.result&&r.result.skills)||[]);
sk.replaceChildren();
if(!rows.length)sk.append(el('p','rail-info',t('proNoSkills')));
for(const s of rows.slice(0,40)){
const row=el('div','pro-row skill-row');
row.append(el('span','skill-name',(s.displayName||s.selector||'?')));
const scope=document.createElement('select');scope.className='skill-scope';scope.setAttribute('aria-label',t('ariaScope'));
for(const v of ['user','project','bundled','plugin']){const o=document.createElement('option');o.value=v;o.textContent=v;scope.append(o)}
const act=document.createElement('select');act.className='skill-act';act.setAttribute('aria-label',t('ariaActivation'));
for(const v of ['on','userInvocableOnly','off']){const o=document.createElement('option');o.value=v;o.textContent=v;act.append(o)}
const go=el('button','',t('ctlSet'));
go.addEventListener('click',()=>proOp('skillActivation',{selector:s.selector,scope:scope.value,activation:act.value}));
row.append(scope);row.append(act);row.append(go);sk.append(row);
}}catch(e){sk.replaceChildren();sk.append(el('p','rail-info',t('proSkillsErr')+String(e&&e.message||e).slice(0,150)))}
}
function fillEffort(variants){
const es=$('#ctlEffort');if(!es)return;es.replaceChildren();
const list=(Array.isArray(variants)&&variants.length?variants:CLOSED_TIERS);
for(const v of list){const t=(v&&v.tier)||v;const o=document.createElement('option');o.value=t;o.textContent=t;es.append(o)}
}
function selectedModel(){const cur=$('#ctlModel').value;const m=ctlCatalog.find(x=>x.modelId===cur)||{};const a={modelId:m.modelId||cur};if(m.providerId)a.providerId=m.providerId;if(m.profileId!=null)a.profileId=m.profileId;if(m.displayLabel)a.displayLabel=m.displayLabel;return a}
const CLOSED_MODES=["allowAll","promptUnmatched","onRequest","denyUnmatched"];
function fillModes(){
const ms=$('#ctlMode');if(!ms)return;
const sess=((state.pro.panels||{}).session||{}).session||{};
const am=sess.approvalMode;const cur=(typeof am==='string')?am:((am||{}).mode||(am||{}).effective||'');
ms.replaceChildren();
for(const m of CLOSED_MODES){const o=document.createElement('option');o.value=m;o.textContent=m+(m===cur?' ●':'');if(m===cur)o.selected=true;ms.append(o)}
}
async function refreshPendingControls(){
const box=$('#ctlPending');if(!box)return;
try{const r=await bridge.proControl(state.sessionId,'listPending',{});
const res=(r&&r.result)||{};const pend=Array.isArray(res)?res:(res.approvals||res.pending||res.requests||res.items||[]);
box.replaceChildren();
if(!pend.length)box.append(el('p','rail-info',t('proNone')));
for(const p2 of pend.slice(0,20)){proRow(box,String(p2.approvalId||p2.id||'?').slice(0,16),(p2.toolName||p2.method||'')+' · '+String((p2.choices||[]).length)+t('proOptSuffix'))}
proRaw(box,res);
}catch(e){box.replaceChildren();box.append(el('p','rail-info',t('proPendErr')+String(e&&e.message||e).slice(0,150)))}
}
let ctlStreams=[];
async function refreshExpControls(){
const ps=$('#ctlProfile');
if(ps){try{const r=await bridge.proControl(state.sessionId,'expProfiles',{});
const rows=((r&&r.result&&r.result.profiles)||[]);
ps.replaceChildren();
for(const p of rows){const o=document.createElement('option');o.value=p.profileId;o.textContent=(p.label||p.profileId)+(p.available===false?t('proNotAvail'):'');o.disabled=(p.available===false);ps.append(o)}
if(!rows.length){const o=document.createElement('option');o.textContent=t('proNoProfiles');ps.append(o)}
}catch(e){ps.replaceChildren();const o=document.createElement('option');o.textContent=t('proProfilesErr');ps.append(o)}}
const ss=$('#ctlStream');
if(ss){try{const r=await bridge.proControl(state.sessionId,'expLogStreams',{});
ctlStreams=((r&&r.result&&r.result.streams)||[]);
ss.replaceChildren();
for(let i=0;i<ctlStreams.length;i++){const st=(ctlStreams[i]||{}).stream||{};const o=document.createElement('option');o.value=String(i);o.textContent=((st.kind||'?')+':'+String(st.id||'?').slice(0,12));ss.append(o)}
if(!ctlStreams.length){const o=document.createElement('option');o.textContent=t('proNoStreams');ss.append(o)}
}catch(e){ss.replaceChildren();const o=document.createElement('option');o.textContent=t('proStreamsErr');ss.append(o)}}
}
let ctlLogCursor=null;
async function expLogPage(next){
const box=$('#ctlLogOut');const ss=$('#ctlStream');
const st=ctlStreams[Number((ss&&ss.value)||0)]||{};
const stream=st.stream||{};
const args={stream:stream,limit:20};
if(next&&ctlLogCursor)args.from=ctlLogCursor;
try{const r=await bridge.proControl(state.sessionId,'expLogPage',args);
const res=(r&&r.result)||{};
ctlLogCursor=(res.nextCursor||{}).after||null;
if(box){box.replaceChildren();
const recs=res.records||[];
box.append(el('p','rail-info',t('proRecords')+recs.length+(ctlLogCursor?t('proMore'):t('proEnd'))));
proRaw(box,res)}
}catch(e){if(box){box.replaceChildren();box.append(el('p','rail-info',t('errPrefix')+String(e&&e.message||e).slice(0,200)))}}
}
function applyProMode(){document.body.classList.toggle('pro',state.proMode);document.querySelectorAll('details.tech-details').forEach(d=>{d.open=state.proMode});renderProPanels();if(state.proMode){refreshProPanels();refreshProControls();refreshPendingControls();refreshExpControls();}const b=$('#modeToggle');if(b){b.textContent=state.proMode?t('modePro'):t('modeNormal');b.classList.toggle('on',state.proMode);b.title=state.proMode?t('modeProTitle'):t('modeNormalTitle')}try{localStorage.setItem('continuumProMode',state.proMode?'1':'0')}catch{}}
const titleKeys={chat:'navChat',projects:'navProjects',history:'navHistory',memory:'navMemory',lab:'navLab'};
function viewTitle(v){return titleKeys[v]?t(titleKeys[v]):v}
let toastTimer;
function toast(message){const t=$('#toast');t.textContent=message;t.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('visible'),3900)}
function changeView(view){state.view=view;$('#crumb').textContent=viewTitle(view);document.querySelectorAll('.nav-link').forEach(b=>b.classList.toggle('active',b.dataset.view===view));closeSidebar();renderView()}
function closeSidebar(){$('#sidebar').classList.remove('open');$('#mobileBackdrop').classList.remove('show')}
async function renderView(){
  const page=$('#page');
  saveThreadCache();if(state.view==='chat'){page.replaceChildren(buildChat());renderAttChips();await loadThreadIntoChat(state.sessionId);return}
  const node=el('section','alt-view');
  node.append(el('div','alt-kicker','CONTINUUM / '+viewTitle(state.view).toUpperCase()));
  node.append(el('h1','',viewTitle(state.view)));
  const intros={projects:t('introProjects'),history:t('introHistory'),memory:t('introMemory'),lab:t('introLab')};
  node.append(el('p','alt-intro',intros[state.view]));
  page.replaceChildren(node);
  if(state.view==='projects')await renderProjectsView(node);
  else if(state.view==='history')await renderHistoryView(node);
  else if(state.view==='memory')await renderMemoryView(node);
  else if(state.view==='lab')await renderLabView(node);
}
function emptyBlock(node,msg){const d=el('div','empty-block');d.textContent=msg;node.append(d)}
// ---------- projects view (real folders) ----------
async function renderProjectsView(node){
  let data;
  try{data=await bridge.getProjects(state.sessionId)}catch(e){emptyBlock(node,t('projErr')+e.message);return}
  state.projects=data.projects||[];
  const ws=el('p','alt-intro',t('projWs')+(data.workspace||t('projWsNone')));
  node.append(ws);
  if(!state.projects.length)emptyBlock(node,t('projEmpty'));
  const grid=el('div','alt-grid');
  for(const p of state.projects){
    const card=el('article','alt-card');
    card.append(el('div','card-icon','▦'));
    card.append(el('h3','',(p.active?'● ':'')+(p.title||p.name||p.id)));
    card.append(el('p','',p.path||''));
    card.append(el('p','',(p.has_file?t('mdPresent'):t('mdMissing'))));
    const open=el('button','project-mini',t('projOpen'));
    open.addEventListener('click',async()=>{await openProject(p.id||p.name);changeView('chat')});
    card.append(open);
    const view=el('button','project-mini',t('projView'));
    const pre=el('pre','raw-data');pre.style.display='none';
    view.addEventListener('click',async()=>{
      if(pre.style.display==='none'){try{const f=await bridge.getProjectFile(p.id||p.name);pre.textContent=f.content||t('emptyFile')}catch(e){pre.textContent=t('errPrefix')+e.message}pre.style.display='block'}else pre.style.display='none'});
    card.append(view);
    if(!p.has_file){const init=el('button','project-mini',t('projCreate'));
      init.addEventListener('click',async()=>{try{await bridge.initProjectFile(p.id||p.name);toast(t('fileCreated'));renderView()}catch(e){toast(t('errPrefix')+e.message)}});
      card.append(init)}
    grid.append(card);
  }
  node.append(grid);
  // Manual registration (fallback path; folder drops cannot supply real paths).
  const form=el('div','register-form');
  form.append(el('h3','',t('regTitle')));
  const nameI=el('input');nameI.placeholder=t('regNamePh');
  const pathI=el('input');pathI.placeholder=t('regPathPh');pathI.id='registerPath';
  const btn=el('button','project-mini',t('regBtn'));
  btn.addEventListener('click',async()=>{try{await bridge.registerProject(nameI.value.trim(),pathI.value.trim());toast(t('projRegged'));refreshProjects()}catch(e){toast(t('errPrefix')+e.message)}});
  form.append(nameI,pathI,btn);
  const dz=el('div','drop-hint',t('dropHint'));
  dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('on')});
  dz.addEventListener('dragleave',()=>dz.classList.remove('on'));
  dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('on');
    const names=[...e.dataTransfer.files].map(f=>f.name);
    window.ContinuumDrop&&window.ContinuumDrop.onProjectFolder&&window.ContinuumDrop.onProjectFolder(names);
    toast(names.length?t('folderGot',{names:names.slice(0,3).join(', ')}):t('dropEmpty'));
    pathI.focus()});
  form.append(dz);
  node.append(form);
}
async function refreshProjects(){
  try{const d=await bridge.getProjects(state.sessionId);state.projects=d.projects||[];
    const act=(d.projects||[]).find(p=>p.active);state.projectId=act?(act.id||act.name):state.projectId;
    renderSidebarProjects();updateChip();if(state.view==='projects')renderView()}catch{}
}
function updateChip(){
  const chip=$('#projectChip');if(!chip)return;
  if(state.projectId){chip.classList.remove('hidden');$('#projectChipName').textContent=state.projectId}
  else chip.classList.add('hidden');
}
// ---------- history view (real sessions) ----------
function fmtDay(ts){const d=new Date(ts);if(isNaN(d))return '';const now=new Date();
  const same=(a,b)=>a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth()&&a.getDate()===b.getDate();
  const hm=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
  if(same(d,now))return t('histToday')+' '+hm;
  const y=new Date(now);y.setDate(now.getDate()-1);
  if(same(d,y))return t('histYesterday')+' '+hm;
  try{return d.toLocaleDateString(state.lang==='es'?'es-ES':'en-US',{day:'numeric',month:'short'})+' '+hm}catch{return d.toISOString().slice(0,10)}}
async function renderHistoryView(node){
  try{const d=await bridge.bootstrap();if(d&&Array.isArray(d.history))state.history=d.history}catch{}
  if(!state.history.length){emptyBlock(node,state.connected?t('histEmpty'):t('histDesign'));return}
  const grid=el('div','alt-grid');
  for(const h of state.history){
    const card=el('article','alt-card');
    card.append(el('div','card-icon','◫'));
    card.append(el('h3','',h.title||t('newChat')));
    const bits=[];
    if(h.project)bits.push(h.project);
    if(h.updated)bits.push(fmtDay(h.updated));
    bits.push(t('histTurns',{n:h.turns??'?'}));
    card.append(el('p','',bits.join(' · ')));
    if(h.preview)card.append(el('p','hist-preview','“'+h.preview+'”'));
    const det=document.createElement('details');det.className='tech-details';
    const sum=document.createElement('summary');sum.textContent=t('apTech');det.append(sum);
    det.append(el('p','','bid: '+(h.id||'?')+' · muse: '+(h.muse_session||'—')));
    card.append(det);
    const b=el('button','project-mini',t('histResume'));
    b.addEventListener('click',()=>{if(state.working){toast(t('stillWorkingSwitch'));return}state.sessionId=h.id||null;state.sessionStarted=true;$('#sessionName').textContent=String(state.sessionId||'—').slice(0,12);changeView('chat');toast(t('resumeToast'))});
    card.append(b);grid.append(card);
  }
  if(state.proMode)grid.querySelectorAll('details').forEach(d=>d.open=true);
  node.append(grid);
}
// ---------- memory view (real encyclopedia) ----------
async function renderMemoryView(node){
  let lab,entries;
  try{[lab,entries]=await Promise.all([bridge.getLab(),bridge.getEntries()])}
  catch(e){emptyBlock(node,t('memErr')+e.message);return}
  state.memory=(entries&&entries.entries)||[];
  const core=(lab&&lab.core)||{};
  node.append(el('p','alt-intro',t('memCoreLine',{ids:(core.entry_ids||[]).join(', ')||t('emptyFile'),tokens:core.tokens||0,n:state.memory.length})));
  if(!state.memory.length){emptyBlock(node,t('memEmpty'));return}
  const grid=el('div','alt-grid');
  for(const m of state.memory.slice(0,60)){
    const card=el('article','alt-card');
    card.append(el('div','card-icon','✳'));
    card.append(el('h3','',m.title||m.id));
    card.append(el('p','',`${(m.types||[]).join(',')} · ${m.status||''} · ${m.id||''}`));
    card.append(el('p','',(m.gist||m.why||'').slice(0,200)));
    grid.append(card);
  }
  node.append(grid);
}
// ---------- lab view (real traces) ----------
async function renderLabView(node){
  let lab;
  try{lab=await bridge.getLab()}catch(e){emptyBlock(node,t('labErr')+e.message);return}
  const u=(lab.usage_totals)||{};
  node.append(el('p','alt-intro',t('labIntro',{c:u.calls||0,p:u.prompt_tokens||0,tot:u.total_tokens||0})));
  const lt=state.lastTurn;
  if(lt.opened&&lt.opened.length){
    node.append(el('h3','',t('labLast')));
    const ul=el('div','alt-grid');
    for(const o of lt.opened){const c=el('article','alt-card');c.append(el('div','card-icon','✳'));c.append(el('h3','',o.title||o.id));c.append(el('p','',`${o.status||''}/${o.state||''} · ${o.score??''} · ${o.path||''}`));ul.append(c)}
    node.append(ul);
  }
  node.append(el('h3','',t('labRecent')));
  const undo=el('button','project-mini',t('labUndo'));
  undo.addEventListener('click',async()=>{try{await bridge.undoLast();toast(t('undoToast'));renderView()}catch(e){toast(t('errPrefix')+e.message)}});
  node.append(undo);
  const refr=el('button','project-mini',t('memRefresh'));
  refr.addEventListener('click',async()=>{
    if(state.working){toast(t('memBusy'));return}
    refr.disabled=true;refr.textContent=t('memRefreshing');
    try{
      const r=await bridge.refreshMemory(state.sessionId);
      toast(t('memRefreshed',{n:r.items??0,t:r.tokens_est??0}));
      renderView();
    }catch(e){toast(t('errPrefix')+e.message)}
    finally{refr.disabled=false;refr.textContent=t('memRefresh')}
  });
  node.append(refr);
  const evs=(lab.log)||[];
  if(!evs.length)emptyBlock(node,t('labNoChanges'));
  const grid=el('div','alt-grid');
  for(const w of evs.slice().reverse().slice(0,20)){
    const card=el('article','alt-card');
    card.append(el('div','card-icon','⌘'));
    card.append(el('h3','',`${w.kind||''} · ${w.entry_id||''}`));
    card.append(el('p','',(w.reason||'')+(w.ts?` · ${w.ts}`:'')));
    grid.append(card);
  }
  node.append(grid);
}
function saveThreadCache(){
  const thread=$('#chatThread');if(!thread||!state.sessionId)return;
  if(thread.dataset.restored&&thread.dataset.restored!==state.sessionId)return;
  const rows=[...thread.querySelectorAll('.message-row')];
  if(!rows.length){delete state.threadCache[state.sessionId];return}
  state.threadCache[state.sessionId]=rows.map(r=>{let atts=[];try{atts=JSON.parse(r.querySelector('.att-chips')?.dataset.atts||'[]')}catch{}return{role:r.classList.contains('user')?'user':'assistant',text:(r.querySelector('.message-text')?.textContent||''),atts}});
}
async function loadThreadIntoChat(sid){
  const thread=$('#chatThread');if(!thread||!sid)return;
  if(thread.dataset.restored===sid||thread.dataset.restoring===sid)return;
  thread.dataset.restoring=sid;
  let entries=state.threadCache[sid];
  if(!entries){try{const d=await bridge.transcript(sid);const turns=(d&&d.turns)||[];entries=[];for(const t of turns){entries.push({role:'user',text:String(t.user||''),atts:t.attachments||[]});entries.push({role:'assistant',text:String(t.assistant||'')})}}catch{entries=[]}}
  delete thread.dataset.restoring;
  if(!thread.isConnected||thread.dataset.restored===sid)return;
  thread.dataset.restored=sid;
  if(!entries.length)return;
  if(!thread.querySelector('.message-row')){for(const m of entries)addMessage(m.role,m.text,m.atts)}
  else{const first=thread.querySelector('.message-row');for(let i=entries.length-1;i>=0;i--){const b=buildRow(entries[i].role,entries[i].text,entries[i].atts);thread.insertBefore(b.row,first)}scrollThread()}
  if(state.working&&state.assistant){const nodes=thread.querySelectorAll('.message-row.assistant .message-text');if(nodes.length)state.assistant=nodes[nodes.length-1]}
}
const MAX_ATTS_UI=5,MAX_ATT_UI=10*1024*1024;
function fmtSize(n){n=+n||0;if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(1)+' MB'}
function addAttFiles(list){
  const arr=[...list];if(!arr.length)return;
  if(state.pendingAtts.length+arr.length>MAX_ATTS_UI){toast(t('attTooMany',{m:MAX_ATTS_UI}));return}
  for(const f of arr){if(f.size>MAX_ATT_UI){toast(t('attTooBig',{n:f.name}));return}}
  state.pendingAtts.push(...arr);renderAttChips();
}
function removeAtt(i){state.pendingAtts.splice(i,1);renderAttChips()}
function renderAttChips(){
  const box=$('#attChips');if(!box)return;
  box.replaceChildren();
  box.classList.toggle('hidden',!state.pendingAtts.length);
  state.pendingAtts.forEach((f,i)=>{
    const c=el('span','att-chip','📎 '+f.name+' ('+fmtSize(f.size)+')');
    const x=el('button','','✕');x.title=t('attRemove');x.setAttribute('aria-label',t('attRemove')+' '+f.name);
    x.addEventListener('click',()=>removeAtt(i));c.append(x);box.append(c)});
}
// ---------- chat ----------
function buildChat(){const template=document.createElement('div');template.innerHTML=`<div class="chat-layout"><div class="chat-thread" id="chatThread"></div><div class="composer-wrap"><div class="att-chips hidden" id="attChips"></div><div class="composer"><label class="sr-only" for="prompt">${t('promptLabel')}</label><textarea id="prompt" rows="2" placeholder="${t('promptPh')}" spellcheck="true"></textarea><div class="turn-status-row hidden" id="turnStatusRow"><span class="status-dot"></span><span id="turnStatus"></span><span id="turnElapsed" class="turn-elapsed hidden"></span></div><div class="composer-actions"><div class="composer-hint"><span class="cmd-glyph">⌘</span> Continuum <span class="hint-divider">/</span> <span id="composerState">${state.connected?t('composerOn'):t('composerOff')}</span></div><div class="composer-buttons"><span id="queueBadge" class="queue-badge hidden"></span><button class="stop-btn ${state.working?'':'hidden'}" id="stop" title="${t('stopTitle')}">${t('stopBtn')}</button><button class="icon-btn attach-btn" id="attach" title="${t('attachBtn')}" aria-label="${t('attachBtn')}">📎</button><input type="file" id="fileInput" class="hidden" multiple/><button class="send-btn" id="send" aria-label="${t('sendLabel')}">↑</button></div></div></div><p class="composer-note">${t('composerNote')}</p><p class="composer-disclaimer">${t('aiDisclaimer')}</p></div></div>`;
  const root=template.firstElementChild;
  root.querySelector('#chatThread').append(buildWelcome());root.querySelector('#send').addEventListener('click',()=>send());
  root.querySelector('#stop').addEventListener('click',stop);
  root.querySelector('#attach').addEventListener('click',()=>root.querySelector('#fileInput').click());
  root.querySelector('#fileInput').addEventListener('change',e=>{addAttFiles(e.target.files);e.target.value=''});
  root.querySelector('#prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});
  return root}
function buildWelcome(){const section=el('div','welcome');section.id='welcome';section.innerHTML=`<div class="welcome-orb" aria-hidden="true"><div class="orb-halo halo-one"></div><div class="orb-halo halo-two"></div><div class="orb-core">✳</div><span class="orb-tick orb-tick-a"></span><span class="orb-tick orb-tick-b"></span></div><div class="eyebrow"><span class="eyebrow-line"></span> ${t('wEyebrow')}</div><h1>${t('wTitle1')}<br/><em>${t('wTitle2')}</em></h1><p class="welcome-copy">${t('wCopy')}</p><div class="suggestions"><button class="suggestion" data-prompt="${t('sug1p')}"><span class="suggestion-icon">↗</span><strong>${t('sug1t')}</strong><small>${t('sug1s')}</small></button><button class="suggestion" data-prompt="${t('sug2p')}"><span class="suggestion-icon">✳</span><strong>${t('sug2t')}</strong><small>${t('sug2s')}</small></button><button class="suggestion" data-prompt="${t('sug3p')}"><span class="suggestion-icon">⌘</span><strong>${t('sug3t')}</strong><small>${t('sug3s')}</small></button></div>`;
  section.querySelectorAll('[data-prompt]').forEach(b=>b.addEventListener('click',()=>{$('#prompt').value=b.dataset.prompt;$('#prompt').focus()}));return section}
function buildRow(role,text,atts){const row=el('div','message-row '+role);if(role==='assistant'){row.append(el('div','message-avatar','✳'))}const body=el('div','message-content');if(role==='assistant')body.append(el('div','message-label','MUSE CODE'));if(atts&&atts.length){const chips=el('div','att-chips');try{chips.dataset.atts=JSON.stringify(atts)}catch{}for(const a of atts)chips.append(el('span','att-chip','📎 '+(a.name||'file')));body.append(chips)}body.append(el('div','message-text',text));row.append(body);return{row:row,text:body.querySelector('.message-text')}}
function addMessage(role,text,atts){const thread=$('#chatThread');if(!thread)return null;$('#welcome')?.remove();const b=buildRow(role,text,atts);thread.append(b.row);scrollThread();return b.text}
function scrollThread(){const t=$('#chatThread');if(t)t.scrollTop=t.scrollHeight}
function activity(title,detail='',raw=''){
  const feed=$('#activityFeed');if(feed.querySelector('.feed-empty'))feed.replaceChildren();
  const row=el('div','activity-item collapsible');
  row.append(el('div','',String(title)));
  if(detail)row.append(el('small','',String(detail).slice(0,350)));
  if(raw){const pre=el('pre','raw-data');pre.style.display='none';pre.textContent=String(raw).slice(0,1500);row.append(pre);
    row.addEventListener('click',()=>{pre.style.display=pre.style.display==='none'?'block':'none'})}
  feed.prepend(row);while(feed.children.length>40)feed.lastChild.remove()}
function setStatus(connected){state.connected=connected;$('#runtimeChip').classList.toggle('connected',connected);$('#runtimeText').textContent=connected?t('rtOnline'):t('rtOff');$('#agentStatus').textContent=connected?t('online'):t('offline');$('#agentStatus').classList.toggle('on',connected);const hint=$('#composerState');if(hint)hint.textContent=connected?t('composerOn'):t('composerOff')}
function setWorking(value){state.working=value;$('#stop')?.classList.toggle('hidden',!value)}
function newSessionId(){try{if(window.crypto&&crypto.randomUUID)return crypto.randomUUID().slice(0,8)}catch{}return 's'+Date.now().toString(36)}
function setTurnStatus(key,native,force){
const row=$('#turnStatusRow'),el=$('#turnStatus');
if(!row||!el)return;
if(key){state.statusKey=key;state.statusNative=native||''}else{state.statusKey=null;state.statusNative=''}
const k=key?key+'|'+(state.proMode&&native||''):null;
if(!force&&setTurnStatus._k===k)return;
setTurnStatus._k=k;
if(!key){row.classList.add('hidden');stopElapsed();return}
row.classList.remove('hidden');
const label=t(key);el.textContent=(state.proMode&&native)?(label+' · '+native):label;
}
let elapsedTimer=null,turnStartMs=0;
function startElapsed(){turnStartMs=Date.now();stopElapsed();tickElapsed();elapsedTimer=setInterval(tickElapsed,500)}
function stopElapsed(){if(elapsedTimer){clearInterval(elapsedTimer);elapsedTimer=null}const e=$('#turnElapsed');if(e){e.classList.add('hidden');e.textContent=''}}
function tickElapsed(){const e=$('#turnElapsed');if(!e||!turnStartMs)return;const s=Math.floor((Date.now()-turnStartMs)/1000);e.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0');e.classList.remove('hidden')}
async function followupSend(msg,opts){
const input=$('#prompt');
if(!(opts&&opts.skipBubble))addMessage('user',msg);
const ctl=new AbortController();state.followups.add(ctl);
activity(t('fupSent'),t('fupDetail'));
const front=!!(opts&&opts.front);let sawTurn=false;
try{await bridge.streamChat({message:msg,session_id:state.sessionId,project_id:state.projectId},ev=>{const ft=((ev||{}).data||{}).t||'';if(ft==='text'||ft==='tool'||ft==='started'||ft==='approval'||ft==='questions')sawTurn=true;if(ft==='done'&&sawTurn&&!state.working){state.turnOpen=false;setTurnStatus(null)}followupEvent(ev,msg,front)},ctl.signal)}
catch(e){if(e.name!=='AbortError'){if(input&&!input.value)input.value=msg;toast(t('sendFailT')+(e.message||e))}}
finally{state.followups.delete(ctl);if(!state.working)flushQueue()}
}
function followupEvent({data},msg,front){
const d=data||{},et=d.t||'';
if(et==='steered'){activity(t('steeredAct'));return}
if(et==='queued'){const e={msg:msg,bubbled:true};if(front)state.pendingQueue.unshift(e);else state.pendingQueue.push(e);updateQueueUI();activity(t('queuedN',{n:state.pendingQueue.length}),t('queuedDetail'));return}
if(et==='done'||et==='started'){return}
if(et==='error'){activity(t('sendErrT'),d.text||'');toast(d.text||t('sendErrT'));return}
handleEvent({data});
}
function flushQueue(){
// Deterministic delivery: retry steer on turn start, deliver the rest at
// turn end. A follow-up settle only flushes when idle, so a refused steer
// waits instead of busy-retrying. One follow-up in flight at a time.
if(!state.pendingQueue.length||!state.connected||state.followups.size)return;
const e=state.pendingQueue.shift();updateQueueUI();
const text=(e&&typeof e==='object')?e.msg:String(e);
const shown=!((e&&typeof e==='object')&&e.bubbled===false);
if(state.working)followupSend(text,{skipBubble:true,front:true});
else send(text,{skipBubble:shown});
}
function updateQueueUI(){
const b=$('#queueBadge');if(!b)return;
const n=state.pendingQueue.length;
b.classList.toggle('hidden',!n);
b.textContent=n?t('queuedN',{n}):'';
}
async function send(forced,opts){
const input=$('#prompt');
const msg=String(forced!=null?forced:(input?.value||'')).trim();
if(!msg)return;
  if(!state.connected){toast(t('noConnToast'));return}
  if(state.working){if(state.pendingAtts.length){toast(t('attBusy'));return}if(forced==null&&input)input.value='';followupSend(msg);return}
  if(state.pendingQueue.length&&forced==null){state.pendingQueue.push({msg:msg,bubbled:false});if(input)input.value='';updateQueueUI();flushQueue();return}
  if(forced==null&&input)input.value='';
  let attIds=[],attMeta=[];
  if(state.pendingAtts.length){try{const up=await bridge.uploadAttachments(state.sessionId,state.pendingAtts);attIds=(up.attachments||[]).map(a=>a.id);attMeta=(up.attachments||[]).map(a=>({name:a.name,size:a.size,type:a.type}));state.pendingAtts=[];renderAttChips()}catch(e){toast(t('attUploadFail')+(e.message||e));return}}
  if(!(opts&&opts.skipBubble))addMessage('user',msg,attMeta);state.assistant=addMessage('assistant','');state.abort=new AbortController();setWorking(true);state.turnOpen=true;setTurnStatus('stThinking');startElapsed();
  state.lastTurn={opened:[],paths:[],writes:[]};
  try{await bridge.streamChat({message:msg,session_id:state.sessionId,project_id:state.projectId,attachments:attIds},handleEvent,state.abort.signal)}
  catch(error){if(error.name!=='AbortError'){if(forced!=null){state.pendingQueue.unshift({msg:msg,bubbled:true});updateQueueUI()}else if(input&&!input.value)input.value=msg;activity(t('connErrT'),error.message);toast(t('errPrefix')+error.message);state.turnOpen=false;setTurnStatus('stError');if(state.assistant&&!state.assistant.textContent)state.assistant.textContent=t('noResponse')}}
  finally{setWorking(false);state.abort=null;state.assistant=null;if(state.view==='lab')renderView();if(state.turnOpen){state.turnOpen=false;setTurnStatus(null)}flushQueue()}}
function handleEvent({data}){
  // Real server.py frame shapes: data.t in started/text/activity/tool/approval/
  // approval-resolved/questions/questions-settled/usage/context/status/error/writes/done/event.
  const d=data||{};const et=d.t||'';
  if(et==='started'){
    if(d.session_id){state.sessionStarted=true;$('#sessionName').textContent=String(d.session_id).slice(0,12)}
    state.lastTurn.opened=d.opened||[];state.lastTurn.paths=d.paths||[];setTurnStatus('stThinking');
    $('#modelName').textContent=d.model||$('#modelName').textContent;
    activity(t('turnStartedT',{id:String(d.turn_id||'').slice(0,8),prov:d.provider||''}),
      t('turnOpenedD',{list:(d.opened||[]).map(o=>o.title||t('untitled')).join(', ')||t('noneF'),deep:d.deep}),JSON.stringify({turno:(d.turn_id||'').slice(0,8),abiertas:(d.opened||[]).map(o=>o.id)}));
    flushQueue();return}
  if(et==='text'){if(!state.assistant)state.assistant=addMessage('assistant','');state.assistant.textContent+=d.text||'';scrollThread();setTurnStatus('stResponding');return}
  if(et==='activity'){activity(d.kind||t('actFallback'),d.text||'');return}
  if(et==='tool'){const bits=[d.kind||'tool',d.status||'',d.toolName||d.name||'',d.title||'',d.command||'',d.path||'',d.url||''].filter(Boolean).join(' ');
    setTurnStatus('stTool',(d.toolName||d.name||d.kind||''));activity('▸ '+bits,(d.text||d.args||'').slice(0,300),JSON.stringify(d));return}
  if(et==='approval'){renderApproval(d);setTurnStatus('stApproval',(d.toolName||''));activity(t('permReq'),d.toolName||'');return}
  if(et==='approval-resolved'){activity(t('permRes'),JSON.stringify(d.params||{}).slice(0,300));return}
  if(et==='questions'){renderQuestion(d);setTurnStatus('stQuestions');return}
  if(et==='questions-settled'){activity(t('qSettled'));return}
  if(et==='usage'){proCapture('usage',d);activity(t('usageActT'),t('usageLine',{p:d.promptTokens,total:d.totalTokens,model:d.modelId||''}));return}
  if(et==='context'||et==='status'){proCapture(et,d);return}
  if(et==='todos'||et==='usage-changed'){proCapture(et,d);return}
  if(et==='memory-ops'){activity(t('memUpd'),'',d.block||'');return}
  if(et==='project-ops'){activity(t('projUpd'),'',d.block||'');return}
  if(et==='error'){activity(t('agentErrT'),d.text||'');toast(d.text||t('agentErrT'));state.turnOpen=false;setTurnStatus('stError');if(state.assistant&&!state.assistant.textContent)state.assistant.textContent=String(d.text||t('agentErrT')).slice(0,500);
    if(d.need_auth||d.need_install)showSetup();return}
  if(et==='writes'){state.lastTurn.writes=d.writes||[];
    for(const w of state.lastTurn.writes||[]){toast(`${w.kind}: ${w.title||w.name||t('memWord')}`);
      if(w.activated&&w.name){state.projectId=w.name;updateChip();renderSidebarProjects()}}
    if(d.cap_warning)toast(t('capWarn'));return}
  if(et==='done'){state.turnOpen=false;setTurnStatus(null);activity(t('turnDone'),d.terminal||'');if(d.terminal==='cancelled'&&state.assistant&&!state.assistant.textContent)state.assistant.textContent=t('stStopped');return}
  activity(t('genericEv')+(et||'message'),JSON.stringify(d).slice(0,300),JSON.stringify(d));
}
function renderApproval(f){
  const card=el('div','approval-card');
  card.append(el('h3','',t('apTitle')));
  card.append(el('p','',t('apTool')+(f.toolName||'—')));
  const ex=f.explanation;
  if(ex){
    const box=el('div','approval-expl'+(ex.fallback?' fallback':''));
    const wants=el('div','expl-wants');wants.append(el('strong','',t('apWants')));wants.append(document.createTextNode(ex.title||''));box.append(wants);
    for(const o of ex.objects||[])box.append(el('div','expl-objects',o));
    for(const l of ex.lines||[])box.append(el('div','expl-lines',l));
    for(const e of ex.effects||[])box.append(el('div','expl-effects',e));
    card.append(box);
  }
  const det=document.createElement('details');det.className='tech-details';
  const sum=document.createElement('summary');sum.textContent=t('apTech');det.append(sum);
  const pre=el('pre','raw-data');pre.textContent=JSON.stringify(f.subject||{},null,1)+(f.rawArgs?`\nargs: ${f.rawArgs}`:'');
  if(state.proMode)det.open=true;
  det.append(pre);card.append(det);
  const fb=el('input');fb.placeholder=t('apFeedback');fb.style.display='none';card.append(fb);
  const box=el('div','approval-actions');
  let done=false;
  for(const c of f.choices||[]){
    const b=el('button',(c.decision||'').toLowerCase().includes('abort')||(c.decision||'').toLowerCase().includes('deny')?'reject':'',c.label||c.choiceId);
    if(c.acceptsFeedback)fb.style.display='block';
    b.addEventListener('click',async()=>{
      if(done)return;done=true;box.querySelectorAll('button').forEach(x=>x.disabled=true);
      try{await bridge.decideApproval({approvalId:f.approvalId,choiceId:c.choiceId,requirementId:f.requirementId,session_id:state.sessionId,feedback:fb.value||undefined});
        activity(t('permDecided'),c.label||c.choiceId);card.append(el('p','',t('permSent')+(c.label||c.choiceId)))}
      catch(e){done=false;box.querySelectorAll('button').forEach(x=>x.disabled=false);toast(t('sendFailT')+e.message)}});
    box.append(b)}
  if(!(f.choices||[]).length)card.append(el('p','',t('apNoChoices')));
  card.append(box);$('#chatThread')?.append(card);scrollThread()}
function renderQuestion(f){
  const card=el('div','question-card');
  card.append(el('h3','',t('qTitle')));
  const answers=[];
  for(const q of f.questions||[]){
    card.append(el('p','',((q.header?q.header+': ':'')+(q.question||''))));
    const multi=(q.selection&&q.selection.mode)==='multiple';
    const picked=new Set();
    const box=el('div','approval-actions');
    for(const o of q.options||[]){
      const b=el('button','',o.label);if(o.description)b.title=o.description;
      b.addEventListener('click',()=>{
        if(multi){picked.has(o.label)?picked.delete(o.label):picked.add(o.label);b.classList.toggle('on')}
        else{box.querySelectorAll('button').forEach(x=>x.disabled=true);sendAnswers()}});
      box.append(b)}
    const free=el('input');free.placeholder=t('qFree');box.append(free);
    if(multi){const s=el('button','',t('qSend'));s.addEventListener('click',sendAnswers);box.append(s)}
    card.append(box);
    answers.push({q,box,free,picked,multi,sent:false});
    function sendAnswers(){
      const a=answers.find(x=>x.q===q);if(!a||a.sent)return;a.sent=true;
      const one={questionId:q.id};
      if(a.free.value.trim())one.freeText=a.free.value.trim().slice(0,500);
      else if(a.multi)one.selectedLabels=[...a.picked];
      else{const lbl=[...a.box.querySelectorAll('button')].find(x=>x.disabled&&x.textContent!==t('qSend'));one.selectedLabel=(lbl?lbl.textContent:'').slice(0,120)}
      a.box.querySelectorAll('button').forEach(x=>x.disabled=true);
      bridge.answerQuestion({userInputId:f.userInputId,answers:[one],session_id:state.sessionId})
        .then(()=>{activity(t('answerSent'));card.append(el('p','',t('answeredTo')+(q.header||q.id)))})
        .catch(e=>{a.sent=false;a.box.querySelectorAll('button').forEach(x=>x.disabled=false);toast(e.message)})}
  }
  $('#chatThread')?.append(card);scrollThread()}
async function stop(){try{await bridge.stopTurn({session_id:state.sessionId});activity(t('stopReq'))}catch(e){toast(t('stopFail')+e.message)}state.abort?.abort();state.turnOpen=false;setTurnStatus('stStopped');state.followups.forEach(c=>{try{c.abort()}catch{}});state.followups.clear();if(state.pendingQueue.length){state.pendingQueue=[];updateQueueUI();activity(t('queueDropped'))}setWorking(false)}
async function bootstrap(){
setTurnStatus(null);
  try{const d=await bridge.bootstrap();
    state.runtime=(d&&d.runtime)||null;
    if(d&&d.connected===false)throw new Error('backend offline');
    state.projects=Array.isArray(d.projects)?d.projects:[];
    state.history=Array.isArray(d.history)?d.history:[];
    state.core=(d.memory&&d.memory.core_ids)||[];
    state.lab=(d.lab)||{};state.runtime=d.runtime||null;
    $('#modelName').textContent=d.model||'Muse Spark';
    renderSidebarProjects();setStatus(true);updateChip();renderView();hideSetup();
  }catch(e){setStatus(false);showSetup()}}
function renderSidebarProjects(){
  const pane=$('#sidebarProjects');pane.replaceChildren();
  let list=state.projects.slice();
  try{const order=JSON.parse(localStorage.getItem('continuumProjectOrder')||'[]');
    if(order.length)list.sort((a,b)=>{const ia=order.indexOf(a.id||a.name),ib=order.indexOf(b.id||b.name);
      return (ia<0?999:ia)-(ib<0?999:ib)})}catch{}
  if(!list.length){pane.append(el('p','sidebar-empty',t('sideEmpty')));return}
  list.forEach(p=>{
    const b=el('button','project-mini');b.draggable=true;b.dataset.pid=p.id||p.name||'';
    if((p.id||p.name)===state.projectId)b.classList.add('on');
    b.append(el('i','','▦'));b.append(el('span','',p.title||p.name||p.id));
    b.addEventListener('click',()=>openProject(p.id||p.name));
    b.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/project-id',b.dataset.pid)});
    b.addEventListener('dragover',e=>e.preventDefault());
    b.addEventListener('drop',e=>{e.preventDefault();
      const src=e.dataTransfer.getData('text/project-id');const dst=b.dataset.pid;
      if(!src||src===dst)return;
      const ids=list.map(x=>x.id||x.name).filter(Boolean);
      const from=ids.indexOf(src);ids.splice(from,1);ids.splice(ids.indexOf(dst),0,src);
      try{localStorage.setItem('continuumProjectOrder',JSON.stringify(ids))}catch{}
      renderSidebarProjects();toast(t('orderToast'))});
    pane.append(b)});
}
async function openProject(id){
  try{await bridge.activateProject(state.sessionId,id);state.projectId=id;updateChip();renderSidebarProjects();toast(t('projActiveT')+id)}
  catch(e){toast(t('openFailT')+e.message)}
}
function showSetup(){
  const bar=$('#setupBanner');if(!bar)return;bar.classList.remove('hidden');
  const r=state.runtime||{};
  let msg=t('setupOff'),act=null,href=null;
  if(r&&r.installed===false){msg=t('setupNoInstall',{sh:r.install_cmd||r.install_sh||'—'});act=t('setupDocs');href=r.docs||null}
  else if(r&&r.provider!=='echo'&&!r.logged_in){msg=t('setupLogin');act=t('setupConnect');href=r.setup||null}
  else if(r&&!r.allowance){msg=t('setupNoSub',{auth:r.auth_text||'—'});act=t('setupConnect');href=r.setup||null}
  bar.querySelector('span').textContent=msg;
  const a=$('#setupAction');
  if(a){if(href){a.textContent=act;a.href=href;a.classList.remove('hidden')}else{a.classList.add('hidden');a.removeAttribute('href')}}
}
function hideSetup(){const bar=$('#setupBanner');if(bar)bar.classList.add('hidden')}
window.ContinuumDrop={onFiles:null,onProjectFolder:null};
function initDrop(){
  const ov=$('#dropOverlay');
  addEventListener('dragenter',e=>{if(e.dataTransfer&&[...e.dataTransfer.types].includes('Files'))ov.classList.remove('hidden')});
  ov.addEventListener('dragleave',()=>ov.classList.add('hidden'));
  ov.addEventListener('dragover',e=>e.preventDefault());
  ov.addEventListener('drop',e=>{e.preventDefault();ov.classList.add('hidden');
    addAttFiles(e.dataTransfer.files);
    const files=[...e.dataTransfer.files].map(f=>`${f.name} (${f.size}B)`);
    activity(t('filesGot'),files.slice(0,8).join(', ')||t('dropEmptyFeed'));
    toast(files.length?t('filesToastN',{n:files.length}):t('dropEmpty'));
    if(window.ContinuumDrop.onFiles)window.ContinuumDrop.onFiles(files)});
}
function resumeElapsed(){if(!turnStartMs||elapsedTimer)return;tickElapsed();elapsedTimer=setInterval(tickElapsed,500)}
function updateLangControl(){document.querySelectorAll('#langSwitch [data-lang]').forEach(b=>{const on=b.dataset.lang===state.lang;b.classList.toggle('on',on);b.setAttribute('aria-pressed',on?'true':'false')})}
function applyStaticI18n(){
document.documentElement.lang=state.lang;document.title=t('docTitle');
const md=document.querySelector('meta[name="description"]');if(md)md.content=t('metaDesc');
document.querySelectorAll('[data-i18n]').forEach(n=>{n.textContent=t(n.dataset.i18n)});
document.querySelectorAll('[data-i18n-ph]').forEach(n=>{n.placeholder=t(n.dataset.i18nPh)});
document.querySelectorAll('[data-i18n-aria]').forEach(n=>{n.setAttribute('aria-label',t(n.dataset.i18nAria))});
document.querySelectorAll('[data-i18n-title]').forEach(n=>{n.title=t(n.dataset.i18nTitle)});
const ls=$('#langSwitch');if(ls)ls.setAttribute('aria-label',t('langLabel'));
updateLangControl();
const feed=$('#activityFeed');const fe=feed&&feed.querySelector('.feed-empty');
if(fe)fe.innerHTML=t('feedEmpty1')+'<br/>'+t('feedEmpty2');
if(!state.sessionStarted){const sn=$('#sessionName');if(sn)sn.textContent=t('sessNone')}
const crumb=$('#crumb');if(crumb)crumb.textContent=viewTitle(state.view);
}
function setLang(l){
if(l!=='en'&&l!=='es')return;
state.lang=useLang(l);
applyStaticI18n();setStatus(state.connected);
const bar=$('#setupBanner');if(bar&&!bar.classList.contains('hidden'))showSetup();
const _draft=document.querySelector('#prompt')?.value;const _focus=document.activeElement&&document.activeElement.id==='prompt';renderSidebarProjects();updateChip();applyProMode();renderView();if(_draft!=null){const _p=document.querySelector('#prompt');if(_p){_p.value=_draft;if(_focus)_p.focus()}}
setTurnStatus._k=null;
if(state.statusKey){setTurnStatus(state.statusKey,state.statusNative);resumeElapsed()}
else setTurnStatus(null);
}
function init(){
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>changeView(b.dataset.view)));
  $('#newChat').addEventListener('click',()=>{if(state.working){toast(t('stillWorkingNew'));return}
    saveThreadCache();
    const draft=$('#prompt')?$('#prompt').value:'';
    state.pendingQueue.length=0;updateQueueUI();
    state.turnOpen=false;state.assistant=null;state.lastTurn={opened:[],paths:[],writes:[]};
    turnStartMs=0;setTurnStatus(null);
    state.sessionId=newSessionId();state.sessionStarted=true;$('#sessionName').textContent=String(state.sessionId).slice(0,12);changeView('chat');
    if(draft){const p=$('#prompt');if(p)p.value=draft}
    activity(t('newChatAct'),t('newChatDetail'));toast(t('newChatToast'))});
  const chipClear=$('#projectChipClear');
  if(chipClear)chipClear.addEventListener('click',async()=>{try{await bridge.activateProject(state.sessionId,null);state.projectId=null;updateChip();renderSidebarProjects();toast(t('projOff'))}catch(e){toast(e.message)}});
  const recheck=$('#setupRecheck');
  if(recheck)recheck.addEventListener('click',bootstrap);
  $('#openSidebar').addEventListener('click',()=>{$('#sidebar').classList.add('open');$('#mobileBackdrop').classList.add('show')});
  $('#closeSidebar').addEventListener('click',closeSidebar);
  $('#mobileBackdrop').addEventListener('click',closeSidebar);
  $('#modeToggle').addEventListener('click',()=>{state.proMode=!state.proMode;applyProMode()});
  document.querySelectorAll('#langSwitch [data-lang]').forEach(b=>b.addEventListener('click',()=>setLang(b.dataset.lang)));
  $('#proRefresh').addEventListener('click',refreshProPanels);
  $('#ctlModelApply').addEventListener('click',()=>proOp('setModel',selectedModel()));
  $('#ctlEffortApply').addEventListener('click',()=>proOp('setReasoningEffort',{effort:$('#ctlEffort').value}));
  $('#ctlRename').addEventListener('click',()=>proOp('rename',{name:$('#ctlName').value}));
  $('#ctlFork').addEventListener('click',()=>proOp('fork',{}));
  $('#ctlSide').addEventListener('click',()=>proOp('sideChat',{}));
  $('#ctlCompact').addEventListener('click',()=>{if(confirm(t('cfCompact')))proOp('compact',{})});
  $('#ctlAttachGo').addEventListener('click',()=>proOp('attach',{sessionId:$('#ctlAttach').value}));
  $('#ctlGoalSet').addEventListener('click',()=>proOp('goalSet',{objective:$('#ctlGoal').value}));
  $('#ctlGoalEdit').addEventListener('click',()=>proOp('goalEdit',{objective:$('#ctlGoal').value}));
  $('#ctlGoalPause').addEventListener('click',()=>proOp('goalPause',{}));
  $('#ctlGoalResume').addEventListener('click',()=>proOp('goalResume',{}));
  $('#ctlGoalClear').addEventListener('click',()=>proOp('goalClear',{}));
  $('#ctlModeApply').addEventListener('click',()=>{const m=$('#ctlMode').value;if(m==='allowAll'){if(!confirm(t('cfAllowAll')))return}proOp('setApprovalMode',{mode:m})});
  $('#ctlPendingRefresh').addEventListener('click',refreshPendingControls);
  $('#ctlProfileApply').addEventListener('click',()=>{const s=$('#ctlProfile');const txt=s.options[s.selectedIndex]?s.options[s.selectedIndex].text:s.value;if(!confirm(t('cfProfile',{t:txt})))return;proOp('expSetProfile',{profileId:s.value})});
  $('#ctlLogPage').addEventListener('click',()=>expLogPage(false));
  $('#ctlLogNext').addEventListener('click',()=>expLogPage(true));
  applyProMode();
  $('#railClose').addEventListener('click',()=>$('#contextRail').classList.remove('show'));
  $('#railToggleNarrow').addEventListener('click',()=>$('#contextRail').classList.toggle('show'));
  document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#newChat').click()}});
  applyStaticI18n();initDrop();renderView();bootstrap();
}
init();
