import {Component, OnInit, inject, ChangeDetectorRef, HostListener} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {HttpClient, HttpErrorResponse, HttpEventType} from '@angular/common/http';
import {firstValueFrom} from 'rxjs';
import {marked} from 'marked';
import DOMPurify from 'dompurify';
import {cleanAnswer} from './answer-format';

interface User {id:string;name:string;email:string}
interface Doc {id:string; source:string; uploaded_at:string; units:number; characters:number}
interface Unit {location:string;text:string}
interface Report {source:string;status:string;reason?:string;units?:number}
interface Match {document_id:string;source:string;location:string;excerpt:string;citation_id?:string;distance?:number}
interface Message {role:'user'|'assistant';text:string;matches?:Match[];provider?:string;providerLabel?:string;latency?:number;tokens?:number;html?:string}
interface RagStatus {providers:{id:string;name:string;model:string;configured:boolean}[];chunks:number;index_available:boolean;embedding_model:string;default_provider:string;general_chat_enabled:boolean}

interface EvalDraftCase {question:string;expected_answer:string;expected_document_ids:string[]}
interface EvalResult {answer?:string;error?:string;mode?:string;latency_ms:number;timings_ms?:Record<string,number>;reference_f1:number|null;hit_at_k:number|null;document_recall_at_k:number|null;reciprocal_rank:number|null;retrieved_document_ids:string[];retrieved_passages:number;usage?:{input_tokens?:number;output_tokens?:number}}
interface EvalCase extends EvalDraftCase {id:string;position:number;state:string;result:EvalResult|null;verdict:string;note:string}
interface EvalRun {id:string;title:string;provider:string;answer_mode:string;created_at:string;snapshot:{name:string;model_id:string;documents:Record<string,string>};cases:EvalCase[];metrics:Record<string,number|null>}
interface Connection {id:string;name:string;provider:string;provider_name:string;custom_name:string;model_id:string;base_url:string;is_default:boolean;key_configured:boolean;created_at:string;updated_at:string;last_tested_at:string|null;last_test_success:boolean|null;last_test_message:string|null}
interface ProviderOption {id:string;name:string;protocol:string;model_hint:string}
interface ConnectionConfiguration {connections:Connection[];default_id:string;general_chat_enabled:boolean;provider_options:ProviderOption[]}
interface ConnectionDraft {name:string;provider:string;custom_name:string;api_key:string;model_id:string;base_url:string;is_default:boolean}

@Component({
 selector:'app-root',standalone:true,imports:[CommonModule,FormsModule],
 templateUrl:'./app.html'
})
export class AppComponent implements OnInit {
 private http=inject(HttpClient);
 private cd=inject(ChangeDetectorRef);
 sidebarCollapsed=localStorage.getItem('folio-sidebar-collapsed')==='true';
 toggleSidebar(){this.sidebarCollapsed=!this.sidebarCollapsed;localStorage.setItem('folio-sidebar-collapsed',String(this.sidebarCollapsed));if(this.sidebarCollapsed)setTimeout(()=>document.getElementById('sidebar-toggle')?.focus(),0);}
 user:User|null=null; authLoading=true; authMode:'login'|'register'='login';
 authName=''; authEmail=''; authPassword=''; authConfirm=''; authError=''; authBusy=false; showPassword=false; profileOpen=false;
 get initials(){return (this.user?.name||'').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase();}
 async initSession(){
  try{this.user=await firstValueFrom(this.http.get<User>('/auth/me'));await this.refresh();}
  catch(e){if((e as HttpErrorResponse).status!==401)this.authError='Cannot reach the backend. Start the server and try again.';}
  finally{this.authLoading=false;this.cd.detectChanges();}
 }
 switchAuth(mode:'login'|'register'){this.authMode=mode;this.authError='';this.authPassword='';this.authConfirm='';this.showPassword=false;}
 async authenticate(){
  if(this.authBusy)return;
  this.authError='';
  if(this.authMode==='register'&&this.authPassword!==this.authConfirm){this.authError='Passwords do not match.';return;}
  this.authBusy=true;
  try{
   this.user=await firstValueFrom(this.http.post<User>('/auth/'+(this.authMode==='register'?'register':'login'),{name:this.authName.trim(),email:this.authEmail.trim(),password:this.authPassword}));
   this.authPassword='';this.authConfirm='';this.authError='';await this.refresh();
  }catch(e){const h=e as HttpErrorResponse;this.authError=typeof h.error?.detail==='string'?h.error.detail:(h.status===422?'Check all fields and try again.':'Cannot connect. Check that the backend is running.');}
  finally{this.authBusy=false;this.cd.detectChanges();}
 }
 resetWorkspace(){this.user=null;this.documentToDelete=null;this.scopeFilter='';this.evalRun=null;this.evalRuns=[];this.evalCases=[{question:'',expected_answer:'',expected_document_ids:[]}];this.chatId='';this.chatTitle='';this.chats=[];this.historyOpen=false;this.lastFailedQuestion='';this.answerMode='auto';this.docs=[];this.messages=[];this.selected.clear();this.reports=[];this.preview=null;this.showReport=false;this.confirmClear=false;this.question='';this.filter='';this.libraryPage=0;this.profileOpen=false;this.error='';this.notice='';this.view='workspace';this.ragStatus=null;this.provider='sources';this.providerChosen=false;this.clearConnectionState();}
 async logout(){
  if(this.uploading||this.indexing||this.searching||this.authBusy||this.settingsBusy||this.evalRunning)return;
  this.authBusy=true;
  try{await firstValueFrom(this.http.post('/auth/logout',{}));this.resetWorkspace();this.authPassword='';this.authMode='login';}
  catch(e){if((e as HttpErrorResponse).status===401)this.resetWorkspace();else this.fail(e);}
  finally{this.authBusy=false;this.cd.detectChanges();}
 }
 docs:Doc[]=[]; selected=new Set<string>(); reports:Report[]=[]; messages:Message[]=[];
 documentToDelete:Doc|null=null;scopeFilter='';
 get scopeDocuments(){return this.docs.filter(d=>d.source.toLowerCase().includes(this.scopeFilter.toLowerCase()));}
 async deleteDocumentConfirmed(){const doc=this.documentToDelete;this.documentToDelete=null;if(doc)await this.remove(doc);}
 filter=''; question=''; connected=false; checking=false; uploading=false; searching=false;
 libraryPage=0; mobilePane:'documents'|'chat'='chat'; showReport=false;
 drag=false; notice=''; error=''; view:'workspace'|'guide'|'connections'|'evaluation'|'documents'='workspace';
 preview:{source:string;content:Unit[]}|null=null; confirmClear=false;
 motion=localStorage.getItem('folio-motion')||(matchMedia('(prefers-reduced-motion: reduce)').matches?'off':'on');
 theme=localStorage.getItem('folio-theme')||'light';
 uploadStage=''; uploadPercent=0; pendingName=''; model='Semantic sources'; provider='sources'; indexing=false;
 ragStatus:RagStatus|null=null; providerChosen=false;
 answerMode:'auto'|'documents'|'general'='auto';
 chats:{id:string;title:string;updated_at:string}[]=[];chatId='';chatTitle='';historyOpen=false;chatFilter='';chatBusy=false;deleteChatId='';lastFailedQuestion='';
 get filteredChats(){return this.chats.filter(c=>c.title.toLowerCase().includes(this.chatFilter.toLowerCase()));}
 async loadChats(){try{this.chats=await firstValueFrom(this.http.get<typeof this.chats>('/conversations'));}catch(e){this.fail(e);}}
 async openHistory(){this.historyOpen=true;await this.loadChats();}
 async openChat(id:string){if(this.searching||this.chatBusy)return;this.chatBusy=true;try{const c=await firstValueFrom(this.http.get<{id:string;title:string;messages:Message[]}>('/conversations/'+id));this.chatId=c.id;this.chatTitle=c.title;this.messages=c.messages.map(m=>({...m,html:m.role==='assistant'?this.renderMarkdown(m.text):undefined}));this.historyOpen=false;this.view='workspace';this.lastFailedQuestion='';}catch(e){this.fail(e);}finally{this.chatBusy=false;this.cd.detectChanges();}}
 async saveChat(){if(!this.user||!this.messages.length)return;const payload={title:this.chatTitle||this.messages.find(m=>m.role==='user')?.text.slice(0,100)||'Conversation',messages:this.messages.slice(-100).map(({html,...m})=>m)};try{if(this.chatId)await firstValueFrom(this.http.put('/conversations/'+this.chatId,payload));else{const c=await firstValueFrom(this.http.post<{id:string}>('/conversations',payload));this.chatId=c.id;}this.chatTitle=payload.title;}catch{this.error='Your reply is available, but conversation history could not be saved.';}}
 async deleteChat(){if(!this.deleteChatId||this.chatBusy)return;this.chatBusy=true;try{await firstValueFrom(this.http.delete('/conversations/'+this.deleteChatId));if(this.chatId===this.deleteChatId)this.newChat();this.deleteChatId='';await this.loadChats();}catch(e){this.fail(e);}finally{this.chatBusy=false;this.cd.detectChanges();}}
 async renameChat(){const title=this.chatTitle.trim();if(!this.chatId||!title)return;this.chatTitle=title.slice(0,100);await this.saveChat();await this.loadChats();}
 async copyAnswer(text:string){try{await navigator.clipboard.writeText(text);this.notice='Answer copied.';}catch{this.error='Clipboard access was blocked. Select and copy the answer manually.';}this.cd.detectChanges();}
 retry(){if(this.lastFailedQuestion&&!this.searching){this.question=this.lastFailedQuestion;if(this.messages.at(-1)?.text==='The request could not be completed. Check the message above, then retry. Your documents are still saved.')this.messages.splice(-2);void this.send();}}



 apiConnections:Connection[]=[];providerOptions:ProviderOption[]=[];connectionsLoading=false;connectionSaving=false;connectionAction='';connectionsGeneral=true;savedGeneral=true;
 connectionFormOpen=false;editingConnection:Connection|null=null;deleteConnectionTarget:Connection|null=null;connectionFormError='';connectionMessage='';showConnectionKey=false;modelOptions:string[]=[];
 connectionDraft:ConnectionDraft={name:'',provider:'groq',custom_name:'',api_key:'',model_id:'openai/gpt-oss-20b',base_url:'',is_default:false};
 evalRuns:{id:string;title:string;created_at:string;provider:string;answer_mode:string}[]=[];evalRun:EvalRun|null=null;
 evalTitle='My RAG evaluation';evalProvider='sources';evalMode:'documents'|'auto'|'general'='documents';evalCases:EvalDraftCase[]=[{question:'',expected_answer:'',expected_document_ids:[]}];
 evalBusy=false;evalRunning=false;evalPause=false;evalCreating=false;evalError='';evalShowForm=true;evalImportError='';
 get evalCanCreate(){return !!this.evalTitle.trim()&&this.evalCases.length>0&&this.evalCases.every(c=>!!c.question.trim())&&!this.evalBusy&&!this.evalRunning;}
 evalPercent(value:number|null|undefined){return value==null?'—':(value*100).toFixed(1)+'%';}
 evalNumber(value:number|null|undefined){return value==null?'—':value.toFixed(3);}
 evalTime(value:number|null|undefined){return value==null?'—':(value/1000).toFixed(2)+' s';}
 evalSourceName(id:string){return this.evalRun?.snapshot.documents[id]||id;}
 async openEvaluation(){this.view='evaluation';this.evalError='';this.evalBusy=true;try{await this.loadRag();this.evalProvider=this.ragStatus?.default_provider||'sources';this.evalRuns=await firstValueFrom(this.http.get<typeof this.evalRuns>('/evaluations'));}catch(e){this.evalError=this.connectionErrorText(e);}finally{this.evalBusy=false;this.cd.detectChanges();}}
 addEvalCase(){if(this.evalCases.length<30)this.evalCases.push({question:'',expected_answer:'',expected_document_ids:[]});}
 async loadEvalRun(id:string){try{this.evalRun=await firstValueFrom(this.http.get<EvalRun>('/evaluations/'+id));this.evalShowForm=false;}catch(e){this.evalError=this.connectionErrorText(e);}this.cd.detectChanges();}
 async createEvalRun(){if(!this.evalCanCreate)return;this.evalBusy=true;this.evalError='';try{const run=await firstValueFrom(this.http.post<{id:string}>('/evaluations',{title:this.evalTitle.trim(),provider:this.evalProvider,answer_mode:this.evalMode,cases:this.evalCases}));await this.loadEvalRun(run.id);this.evalRuns=await firstValueFrom(this.http.get<typeof this.evalRuns>('/evaluations'));this.notice='Evaluation created. Click Run pending cases to start.';}catch(e){this.evalError=this.connectionErrorText(e);}finally{this.evalBusy=false;this.cd.detectChanges();}}
 async runEvaluation(){if(!this.evalRun||this.evalRunning)return;this.evalRunning=true;this.evalPause=false;this.evalError='';const runId=this.evalRun.id;try{for(const c of this.evalRun.cases.filter(c=>c.state==='pending')){if(this.evalPause)break;c.state='running';this.cd.detectChanges();await firstValueFrom(this.http.post('/evaluations/'+runId+'/cases/'+c.id+'/execute',{}));await this.loadEvalRun(runId);if(this.evalRun?.cases.find(x=>x.id===c.id)?.state==='failed'){this.evalPause=true;this.evalError='Run paused after a failed case. Check its error before resuming pending cases.';break;}}}catch(e){this.evalError=this.connectionErrorText(e);await this.loadEvalRun(runId);}finally{this.evalRunning=false;this.cd.detectChanges();}}
 async reviewEvalCase(c:EvalCase){if(!this.evalRun)return;try{await firstValueFrom(this.http.put('/evaluations/'+this.evalRun.id+'/cases/'+c.id+'/review',{verdict:c.verdict,note:c.note}));await this.loadEvalRun(this.evalRun.id);}catch(e){this.evalError=this.connectionErrorText(e);}this.cd.detectChanges();}
 exportEvaluation(){if(!this.evalRun)return;this.downloadJson('folio-evaluation-'+this.evalRun.id+'.json',this.evalRun);}
 exportEvalDataset(){this.downloadJson('folio-evaluation-dataset.json',{title:this.evalTitle,answer_mode:this.evalMode,cases:this.evalCases});}
 downloadJson(name:string,value:unknown){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url);}
 async importEvalDataset(event:Event){const input=event.target as HTMLInputElement;const f=input.files?.[0];if(!f)return;this.evalImportError='';try{if(f.size>512000)throw Error('Dataset must be smaller than 500 KB.');const d=JSON.parse(await f.text());if(!Array.isArray(d.cases)||!d.cases.length||d.cases.length>30)throw Error('Provide 1–30 test cases.');const allowed=new Set(this.docs.map(d=>d.id));this.evalCases=d.cases.map((c:EvalDraftCase)=>{if(typeof c.question!=='string'||!c.question.trim()||c.question.length>2000||typeof c.expected_answer!=='string'||c.expected_answer.length>8000||!Array.isArray(c.expected_document_ids)||c.expected_document_ids.some(id=>!allowed.has(id)))throw Error('Check question, reference answer and source IDs. Source IDs must belong to documents in this account.');return {question:c.question,expected_answer:c.expected_answer,expected_document_ids:c.expected_document_ids};});if(typeof d.title==='string')this.evalTitle=d.title.slice(0,100);if(['auto','documents','general'].includes(d.answer_mode))this.evalMode=d.answer_mode;}catch(e){this.evalImportError=(e as Error).message;}finally{input.value='';this.cd.detectChanges();}}
 get settingsBusy(){return this.connectionsLoading||this.connectionSaving||!!this.connectionAction;}
 get connectionDestinationChanged(){return !!this.editingConnection&&(this.editingConnection.provider!==this.connectionDraft.provider||this.editingConnection.base_url!==this.connectionDraft.base_url.trim().replace(/\/+$/,''));}
 get selectedProviderOption(){return this.providerOptions.find(p=>p.id===this.connectionDraft.provider);}
 configurationChanged(value:ConnectionConfiguration){this.apiConnections=value.connections;this.providerOptions=value.provider_options;this.connectionsGeneral=value.general_chat_enabled;this.savedGeneral=value.general_chat_enabled;}
 async loadConnections(){
  this.connectionsLoading=true;
  try{this.configurationChanged(await firstValueFrom(this.http.get<ConnectionConfiguration>('/connections')));return true;}
  catch(e){this.error=this.connectionErrorText(e);return false;}
  finally{this.connectionsLoading=false;this.cd.detectChanges();}
 }
 connectionErrorText(e:unknown){const h=e as HttpErrorResponse;if(h.status===401){this.fail(e);return 'Please sign in again.';}return typeof h.error?.detail==='string'?h.error.detail:'The request failed. Check the backend connection and try again.';}
 async openConnections(){this.view='connections';this.profileOpen=false;this.connectionMessage='';await this.loadConnections();}
 openSettings(){void this.openConnections();}
 clearConnectionState(){this.closeConnectionForm();this.apiConnections=[];this.providerOptions=[];this.deleteConnectionTarget=null;this.connectionMessage='';}
 closeConnectionForm(){this.connectionFormOpen=false;this.connectionDraft.api_key='';this.showConnectionKey=false;this.connectionFormError='';this.modelOptions=[];this.editingConnection=null;}
 async openConnectionForm(record?:Connection){
  if(this.settingsBusy)return;
  this.profileOpen=false;this.connectionFormError='';this.connectionMessage='';
  if(!(await this.loadConnections()))return;
  this.editingConnection=record||null;
  this.connectionDraft=record?{name:record.name,provider:record.provider,custom_name:record.custom_name,api_key:'',model_id:record.model_id,base_url:record.base_url,is_default:record.is_default}
   :{name:'',provider:'groq',custom_name:'',api_key:'',model_id:'openai/gpt-oss-20b',base_url:'',is_default:!this.apiConnections.length};
  this.showConnectionKey=false;this.modelOptions=[];this.connectionFormOpen=true;this.cd.detectChanges();
 }
 changeConnectionProvider(){this.connectionDraft.api_key='';this.connectionDraft.model_id=this.connectionDraft.provider==='groq'?'openai/gpt-oss-20b':'';this.connectionDraft.base_url='';this.connectionDraft.custom_name='';this.showConnectionKey=false;this.modelOptions=[];}
 async saveConnection(){
  if(this.settingsBusy)return;
  this.connectionSaving=true;this.connectionFormError='';
  const payload={...this.connectionDraft};
  if(!payload.name.trim())payload.name=(payload.provider==='custom'?payload.custom_name:this.selectedProviderOption?.name||payload.provider)+' · '+payload.model_id;payload.name=payload.name.slice(0,80);
  try{
   const record=await firstValueFrom(this.editingConnection
    ?this.http.put<Connection>('/connections/'+this.editingConnection.id,payload)
    :this.http.post<Connection>('/connections',payload));
   this.closeConnectionForm();await this.loadConnections();this.provider=record.id;this.providerChosen=true;await this.loadRag();
   this.connectionMessage='Connection saved. It is available in chat immediately.';
  }catch(e){this.connectionFormError=this.connectionErrorText(e);}
  finally{this.connectionDraft.api_key='';this.showConnectionKey=false;this.connectionSaving=false;this.cd.detectChanges();}
 }
 async testConnection(record:Connection){
  if(this.settingsBusy)return;
  this.connectionAction=record.id;this.connectionMessage='';this.error='';
  try{const result=await firstValueFrom(this.http.post<{success:boolean;message:string}>('/connections/'+record.id+'/test',{}));this.connectionMessage=record.name+': '+result.message;}
  catch(e){this.error=this.connectionErrorText(e);}
  finally{await this.loadConnections();this.connectionAction='';this.cd.detectChanges();}
 }
 async loadConnectionModels(){
  const record=this.editingConnection;if(!record||this.connectionDestinationChanged||this.settingsBusy)return;
  this.connectionAction=record.id+':models';this.connectionFormError='';
  try{const result=await firstValueFrom(this.http.post<{models:string[]}>('/connections/'+record.id+'/models',{}));this.modelOptions=result.models;}
  catch(e){this.connectionFormError=this.connectionErrorText(e);}
  finally{this.connectionAction='';this.cd.detectChanges();}
 }
 async makeDefault(record:Connection){
  if(this.settingsBusy)return;this.connectionAction=record.id+':default';
  try{this.configurationChanged(await firstValueFrom(this.http.post<ConnectionConfiguration>('/connections/'+record.id+'/default',{})));this.providerChosen=false;await this.loadRag();}
  catch(e){this.error=this.connectionErrorText(e);}
  finally{this.connectionAction='';this.cd.detectChanges();}
 }
 async deleteConnection(){
  const record=this.deleteConnectionTarget;if(!record||this.settingsBusy)return;
  this.connectionAction=record.id+':delete';
  try{await firstValueFrom(this.http.delete('/connections/'+record.id));this.deleteConnectionTarget=null;if(this.provider===record.id){this.provider='sources';this.providerChosen=false;}await this.loadConnections();await this.loadRag();this.connectionMessage='Connection deleted. Your documents are unchanged.';}
  catch(e){this.error=this.connectionErrorText(e);}
  finally{this.connectionAction='';this.cd.detectChanges();}
 }
 async saveGeneralPreference(){
  if(this.settingsBusy)return;this.connectionSaving=true;
  try{this.configurationChanged(await firstValueFrom(this.http.post<ConnectionConfiguration>('/connections/preferences',{general_chat_enabled:this.connectionsGeneral})));await this.loadRag();}
  catch(e){this.connectionsGeneral=this.savedGeneral;this.error=this.connectionErrorText(e);}
  finally{this.connectionSaving=false;this.cd.detectChanges();}
 }
 get canAsk(){if(this.answerMode==='general')return this.provider!=='sources'&&(this.ragStatus?.general_chat_enabled??true);if(this.answerMode==='documents')return this.docs.length>0;return this.provider==='sources'?this.docs.length>0:(this.ragStatus?.general_chat_enabled??true)||this.docs.length>0;}
 get activeProvider(){return this.ragStatus?.providers.find(p=>p.id===this.provider);}
 async loadRag(){
  try{this.ragStatus=await firstValueFrom(this.http.get<RagStatus>('/rag/status'));
   if(this.providerChosen&&this.provider!=='sources'&&!this.ragStatus.providers.some(p=>p.id===this.provider))this.providerChosen=false;
   if(!this.providerChosen){const configured=this.ragStatus.providers.find(p=>p.id===this.ragStatus?.default_provider&&p.configured)||this.ragStatus.providers.find(p=>p.configured);this.provider=configured?.id||'sources';}
  }catch(e){this.fail(e);}
 }
 async prepare(){
  if(this.indexing||this.uploading||!this.docs.length)return;
  this.indexing=true;this.error='';this.notice='Preparing semantic search. The first run downloads the embedding model; it may take a few minutes.';
  this.cd.detectChanges();
  try{const result=await firstValueFrom(this.http.post<{chunks:number;added:number}>('/rag/index',{}));this.notice='Your documents are ready for questions.';await this.loadRag();}
  catch(e){this.notice='';this.fail(e);}
  finally{this.indexing=false;this.cd.detectChanges();}
 }
 get filtered(){return this.docs.filter(d=>d.source.toLowerCase().includes(this.filter.toLowerCase()));}
 get pageCount(){return Math.max(1,Math.ceil(this.filtered.length/10));}
 get currentPage(){return Math.min(this.libraryPage,this.pageCount-1);}
 get pagedDocuments(){return this.filtered.slice(this.currentPage*10,this.currentPage*10+10);}
 get totalChars(){return this.docs.reduce((sum,d)=>sum+d.characters,0);}
 @HostListener('document:keydown.escape') closeModals(){this.documentToDelete=null;this.preview=null;this.confirmClear=false;this.showReport=false;this.profileOpen=false;this.closeConnectionForm();this.deleteConnectionTarget=null;}
 ngOnInit(){this.applyMotion();this.applyTheme();void this.initSession();}
 applyMotion(){document.documentElement.setAttribute("data-motion",this.motion);}
 toggleMotion(){this.motion=this.motion==="on"?"off":"on";localStorage.setItem("folio-motion",this.motion);this.applyMotion();}
 applyTheme(){document.documentElement.setAttribute('data-theme',this.theme);}
 toggleTheme(){this.theme=this.theme==='light'?'dark':'light';localStorage.setItem('folio-theme',this.theme);this.applyTheme();}
 async refresh(){
  this.checking=true;this.error='';
  try{await firstValueFrom(this.http.get('/health'));this.connected=true;this.docs=await firstValueFrom(this.http.get<Doc[]>('/documents'));const ids=new Set(this.docs.map(d=>d.id));this.selected=new Set([...this.selected].filter(x=>ids.has(x)));await this.loadRag();}
  catch(e){this.connected=false;this.fail(e);}
  finally{this.checking=false;this.cd.detectChanges();}
 }
 fail(e:unknown){const h=e as HttpErrorResponse;if(h.status===401){this.resetWorkspace();this.authError='Your session has expired. Please sign in again.';return;}this.error=typeof h.error?.detail==='string'?h.error.detail:'Cannot reach the backend. Start FastAPI, then click Reconnect.';}
 choose(event:Event){const input=event.target as HTMLInputElement;if(input.files)void this.upload(Array.from(input.files));input.value='';}
 drop(event:DragEvent){event.preventDefault();this.drag=false;if(event.dataTransfer)void this.upload(Array.from(event.dataTransfer.files));}
 async upload(files:File[]){
  if(this.uploading||this.indexing)return;
  this.uploading=true;this.error='';this.notice='';this.reports=[];
  try{
   for(const file of files){
    this.pendingName=file.name;this.uploadStage="Uploading";this.uploadPercent=0;this.cd.detectChanges();
    if(file.size>10*1024*1024){this.reports.push({source:file.name,status:'failed',reason:'Upload exceeds 10 MiB'});continue;}
    const data=new FormData();data.append('file',file);
    try{
     const result=await new Promise<{files:Report[];document_ids:string[]}>((resolve,reject)=>{
      this.http.post<{files:Report[];document_ids:string[]}>('/upload',data,{observe:'events',reportProgress:true}).subscribe({next:event=>{
       if(event.type===HttpEventType.UploadProgress){this.uploadPercent=Math.round(100*event.loaded/(event.total||file.size||1));if(this.uploadPercent>=100)this.uploadStage='Extracting files';this.cd.detectChanges();}
       if(event.type===HttpEventType.Response&&event.body)resolve(event.body);
      },error:reject});
     });
     this.reports.push(...result.files);result.document_ids.forEach(id=>this.selected.add(id));
    }catch(e){const h=e as HttpErrorResponse;this.reports.push({source:file.name,status:'failed',reason:typeof h.error?.detail==='string'?h.error.detail:'Upload failed. Check the backend connection.'});}
   }
   await this.refresh();
   this.notice=this.reports.filter(x=>x.status==='processed').length+' file(s) uploaded. Click Prepare documents to get them ready for questions.';
  }finally{this.pendingName='';this.uploading=false;this.cd.detectChanges();}
 }
 toggle(id:string){this.selected.has(id)?this.selected.delete(id):this.selected.add(id);}
 selectAll(){this.selected=this.selected.size===this.docs.length?new Set():new Set(this.docs.map(d=>d.id));}
 async open(doc:Doc){this.error='';try{this.preview=await firstValueFrom(this.http.get<{source:string;content:Unit[]}>('/documents/'+doc.id));}catch(e){this.fail(e);}this.cd.detectChanges();}
 async remove(doc:Doc){this.error='';try{await firstValueFrom(this.http.delete('/documents/'+doc.id));this.selected.delete(doc.id);if(this.preview?.source===doc.source)this.preview=null;this.notice='Document removed.';await this.refresh();}catch(e){this.fail(e);}this.cd.detectChanges();}
 async clear(){try{await firstValueFrom(this.http.delete('/documents'));this.docs=[];this.selected.clear();this.preview=null;this.reports=[];this.confirmClear=false;this.notice='All documents removed. Saved conversations are unchanged.';}catch(e){this.fail(e);}this.cd.detectChanges();}
 cleanAnswer(text:string){return cleanAnswer(text);}
 renderMarkdown(text:string){
  return DOMPurify.sanitize(marked.parse(this.cleanAnswer(text),{async:false,gfm:true,breaks:false}),{
   ALLOWED_TAGS:['p','br','strong','em','ul','ol','li','pre','code','blockquote','h2','h3','h4','table','thead','tbody','tr','th','td','hr'],
   ALLOWED_ATTR:[],ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false
  });
 }
 async send(){
  const text=this.question.trim();if(!text||this.searching||this.indexing||!this.canAsk)return;
  const history=this.messages.slice(-6).map(m=>({role:m.role,text:m.text.slice(0,4000)}));
  const selectedProvider=this.provider;
  this.question='';this.error='';this.lastFailedQuestion='';this.searching=true;this.messages.push({role:'user',text});
  try{
   const result=await firstValueFrom(this.http.post<{answer:string;sources:Match[];provider:string;mode:string;provider_label?:string;latency_ms:number;usage:{input_tokens?:number;output_tokens?:number;html?:string}}>('/ask',{question:text,document_ids:[...this.selected],provider:selectedProvider,history,answer_mode:this.answerMode}));
   const citedIds=new Set(Array.from(result.answer.matchAll(/\[(S\d+)\]/g),match=>match[1]));
   const displayedSources=result.mode==='rag_answer'
     ? result.sources.filter(source=>!!source.citation_id&&citedIds.has(source.citation_id))
     : result.provider==='sources'?result.sources:[];
   this.messages.push({role:'assistant',text:this.cleanAnswer(result.answer),html:this.renderMarkdown(result.answer),matches:displayedSources,provider:result.provider,providerLabel:result.provider_label,latency:result.latency_ms,tokens:(result.usage.input_tokens||0)+(result.usage.output_tokens||0)});
  }catch(e){this.lastFailedQuestion=text;this.fail(e);this.messages.push({role:'assistant',text:'The request could not be completed. Check the message above, then retry. Your documents are still saved.'});}
  finally{if(this.user)await this.saveChat();this.searching=false;this.cd.detectChanges();setTimeout(()=>document.getElementById('conversation-end')?.scrollIntoView({behavior:'smooth',block:'nearest'}),20);}
 }
 newChat(){this.chatId='';this.chatTitle='';this.lastFailedQuestion='';this.messages=[];this.question='';this.notice='Started a new conversation.';}
 sample(text:string){this.question=text;document.getElementById('question')?.focus();}
 download(){
  const content=this.messages.map(m=>m.role.toUpperCase()+': '+m.text+(m.matches||[]).map(x=>'\n['+x.source+' • '+x.location+']\n'+x.excerpt).join('\n')).join('\n\n');
  const url=URL.createObjectURL(new Blob([content],{type:'text/plain'}));const a=document.createElement('a');a.href=url;a.download='folio-conversation.txt';a.click();URL.revokeObjectURL(url);
 }
 fileName(source:string){return source.split('/').pop()||source;}
 extension(source:string){return source.split('.').pop()?.toUpperCase()||'FILE';}
 async citation(match:Match){const doc=this.docs.find(d=>d.id===match.document_id);if(doc)await this.open(doc);else this.error='This source has been deleted.';}
}
