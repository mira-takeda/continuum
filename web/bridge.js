/**
 * Continuum <-> Muse Code presentation bridge.
 * ONLY network adapter. NEVER calls a model API from the browser,
 * invents agent output, or auto-approves actions.
 * Mapped to the real server.py payloads (verified against /api/* handlers).
 */
export class ContinuumBridge {
  constructor(){this.base='';this.token='';try{const m=document.querySelector('meta[name="continuum-token"]');if(m&&m.content&&!m.content.includes('__CONTINUUM_TOKEN__'))this.token=m.content;}catch{}}

  async bootstrap(){
    const response=await fetch(`${this.base}/api/ui/bootstrap`,{cache:'no-store'});
    if(!response.ok)throw new Error(`Bootstrap HTTP ${response.status}`);
    return await response.json();
  }

  async streamChat({message,session_id,project_id,attachments},onEvent,signal){
    const response=await fetch(`${this.base}/api/chat_stream`,{
      method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream','X-Continuum-Token':this.token},
      body:JSON.stringify({message,session_id,project_id,attachments:attachments||[]}),signal
    });
    if(!response.ok)throw new Error(`Chat HTTP ${response.status}: ${(await response.text()).slice(0,220)}`);
    if(!response.body)throw new Error('No streaming response body');
    const reader=response.body.getReader();const decoder=new TextDecoder();
    let buffer='';
    while(true){
      const {value,done}=await reader.read();if(done)break;
      buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');
      let end;
      while((end=buffer.indexOf('\n\n'))!==-1){
        const frame=buffer.slice(0,end);buffer=buffer.slice(end+2);
        if(!frame.trim()||frame.trim().startsWith(':'))continue;
        let event='message';const lines=[];
        for(const line of frame.split('\n')){
          if(line.startsWith('event:'))event=line.slice(6).trim();
          else if(line.startsWith('data:'))lines.push(line.slice(5).trimStart());
        }
        const raw=lines.join('\n');if(!raw)continue;
        let data;try{data=JSON.parse(raw)}catch{data={t:'text',text:raw}};
        onEvent({event,data});
      }
    }
  }
  // Real server shapes: approval{approvalId,choiceId,requirementId,session_id,feedback?}.
  async decideApproval(payload){return this.post('/api/approve',payload);}
  // Real server shapes: answer{userInputId,answers:[{questionId,selectedLabel?|selectedLabels?|freeText?}],session_id}.
  async answerQuestion(payload){return this.post('/api/answer',payload);}
  async stopTurn(payload){return this.post('/api/stop',payload);}
  async getProjects(session_id){
    const q=session_id?`?session_id=${encodeURIComponent(session_id)}`:'';
    const r=await fetch(`${this.base}/api/projects${q}`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Projects HTTP ${r.status}`);
    return await r.json();
  }
  async activateProject(session_id,project_id){
    return this.post('/api/projects/activate',{session_id,project_id});
  }
  async registerProject(name,path){return this.post('/api/projects/register',{name,path});}
  async initProjectFile(project_id){return this.post('/api/projects/init',{project_id});}
  async getProjectFile(project_id){
    const r=await fetch(`${this.base}/api/project-file?project_id=${encodeURIComponent(project_id)}`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Project file HTTP ${r.status}`);
    return await r.json();
  }
  async transcript(session_id){
    const r=await fetch(`${this.base}/api/transcript?session_id=${encodeURIComponent(session_id||'demo')}`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Transcript HTTP ${r.status}`);
    return await r.json();
  }
  async getLab(){
    const r=await fetch(`${this.base}/api/lab`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Lab HTTP ${r.status}`);
    return await r.json();
  }
  async getEntries(){
    const r=await fetch(`${this.base}/api/entries`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Entries HTTP ${r.status}`);
    return await r.json();
  }
  async proPanels(session_id){
    const q=session_id?`?session_id=${encodeURIComponent(session_id)}`:'';
    const r=await fetch(`${this.base}/api/pro/panels${q}`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Pro panels HTTP ${r.status}`);
    return await r.json();
  }
  async proControl(session_id,op,args){return this.post('/api/pro/control',{session_id,op,args:args||{}});}

  async uploadAttachments(session_id,files){
    const fd=new FormData();fd.append('session_id',session_id);
    for(const f of files)fd.append('files',f,f.name);
    const r=await fetch(`${this.base}/api/attachments`,{method:'POST',headers:{'X-Continuum-Token':this.token},body:fd});
    if(!r.ok)throw new Error(`Attach HTTP ${r.status}: ${(await r.text()).slice(0,200)}`);
    return await r.json();
  }
  async undoLast(){return this.post('/api/undo',{});}
  async refreshMemory(session_id){return this.post('/api/memory/refresh',{session_id});}
  async runtimeStatus(){
    const r=await fetch(`${this.base}/api/runtime/status`,{cache:'no-store'});
    if(!r.ok)throw new Error(`Runtime HTTP ${r.status}`);
    return await r.json();
  }
  async post(path,payload){
    const r=await fetch(`${this.base}${path}`,{method:'POST',headers:{'Content-Type':'application/json','X-Continuum-Token':this.token},body:JSON.stringify(payload)});
    if(!r.ok)throw new Error(`${path}: HTTP ${r.status}`);
    const t=await r.text();try{return JSON.parse(t)}catch{return t};
  }
}
