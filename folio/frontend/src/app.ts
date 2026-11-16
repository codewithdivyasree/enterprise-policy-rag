import {Component, OnInit, inject, ChangeDetectorRef, HostListener} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {HttpClient, HttpErrorResponse} from '@angular/common/http';
import {firstValueFrom} from 'rxjs';
import {marked} from 'marked';
import DOMPurify from 'dompurify';

interface User {id:string;name:string;email:string}
interface Doc {id:string; source:string; uploaded_at:string; units:number; characters:number}
interface Unit {location:string;text:string}
interface Report {source:string;status:string;reason?:string;units?:number}
interface Match {document_id:string;source:string;location:string;excerpt:string;citation_id?:string;distance?:number}
interface Message {role:'user'|'assistant';text:string;matches?:Match[];provider?:string;providerLabel?:string;latency?:number;tokens?:number;html?:string}
interface RagStatus {providers:{id:string;name:string;model:string;configured:boolean}[];chunks:number;index_available:boolean;embedding_model:string;default_provider:string;general_chat_enabled:boolean}

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
 resetWorkspace(){this.user=null;this.docs=[];this.messages=[];this.selected.clear();this.reports=[];this.preview=null;this.showReport=false;this.confirmClear=false;this.question='';this.filter='';this.libraryPage=0;this.profileOpen=false;this.error='';this.notice='';this.view='workspace';this.ragStatus=null;this.provider='sources';this.providerChosen=false;this.clearConnectionState();}
 async logout(){
  if(this.uploading||this.indexing||this.searching||this.authBusy||this.settingsBusy)return;
  this.authBusy=true;
  try{await firstValueFrom(this.http.post('/auth/logout',{}));this.resetWorkspace();this.authPassword='';this.authMode='login';}
  catch(e){if((e as HttpErrorResponse).status===401)this.resetWorkspace();else this.fail(e);}
  finally{this.authBusy=false;this.cd.detectChanges();}
 }
 docs:Doc[]=[]; selected=new Set<string>(); reports:Report[]=[]; messages:Message[]=[];
 filter=''; question=''; connected=false; checking=false; uploading=false; searching=false;
 libraryPage=0; mobilePane:'documents'|'chat'='chat'; showReport=false;
 drag=false; notice=''; error=''; view:'workspace'|'guide'|'connections'='workspace';
 preview:{source:string;content:Unit[]}|null=null; confirmClear=false;
 motion=localStorage.getItem('folio-motion')||(matchMedia('(prefers-reduced-motion: reduce)').matches?'off':'on');
 theme=localStorage.getItem('folio-theme')||'light';
 pendingName=''; model='Semantic sources'; provider='sources'; indexing=false;
 ragStatus:RagStatus|null=null; providerChosen=false;


 apiConnections:Connection[]=[];providerOptions:ProviderOption[]=[];connectionsLoading=false;connectionSaving=false;connectionAction='';connectionsGeneral=true;savedGeneral=true;
 connectionFormOpen=false;editingConnection:Connection|null=null;deleteConnectionTarget:Connection|null=null;connectionFormError='';connectionMessage='';showConnectionKey=false;modelOptions:string[]=[];
 connectionDraft:ConnectionDraft={name:'',provider:'groq',custom_name:'',api_key:'',model_id:'openai/gpt-oss-20b',base_url:'',is_default:false};
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
 get canAsk(){return this.provider==='sources'?this.docs.length>0:(this.ragStatus?.general_chat_enabled??true)||this.docs.length>0;}
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
 get pageCount(){return Math.max(1,Math.ceil(this.filtered.length/2));}
 get currentPage(){return Math.min(this.libraryPage,this.pageCount-1);}
 get pagedDocuments(){return this.filtered.slice(this.currentPage*2,this.currentPage*2+2);}
 get totalChars(){return this.docs.reduce((sum,d)=>sum+d.characters,0);}
 @HostListener('document:keydown.escape') closeModals(){this.preview=null;this.confirmClear=false;this.showReport=false;this.profileOpen=false;this.closeConnectionForm();this.deleteConnectionTarget=null;}
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
    this.pendingName=file.name;
    if(file.size>10*1024*1024){this.reports.push({source:file.name,status:'failed',reason:'Upload exceeds 10 MiB'});continue;}
    const data=new FormData();data.append('file',file);
    try{
     const result=await firstValueFrom(this.http.post<{files:Report[];document_ids:string[]}>('/upload',data));
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
 async clear(){try{await firstValueFrom(this.http.delete('/documents'));this.docs=[];this.selected.clear();this.preview=null;this.messages=[];this.reports=[];this.confirmClear=false;this.notice='All documents removed.';}catch(e){this.fail(e);}this.cd.detectChanges();}
 cleanAnswer(text:string){return text.replace(/\[S\d+\]/g,'').replace(/[ \t]+([.,;:!?])/g,'$1').trim();}
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
  this.question='';this.error='';this.searching=true;this.messages.push({role:'user',text});
  try{
   const result=await firstValueFrom(this.http.post<{answer:string;sources:Match[];provider:string;mode:string;provider_label?:string;latency_ms:number;usage:{input_tokens?:number;output_tokens?:number;html?:string}}>('/ask',{question:text,document_ids:[...this.selected],provider:selectedProvider,history}));
   const citedIds=new Set(Array.from(result.answer.matchAll(/\[(S\d+)\]/g),match=>match[1]));
   const displayedSources=result.mode==='rag_answer'
     ? result.sources.filter(source=>!!source.citation_id&&citedIds.has(source.citation_id))
     : result.provider==='sources'?result.sources:[];
   this.messages.push({role:'assistant',text:this.cleanAnswer(result.answer),html:this.renderMarkdown(result.answer),matches:displayedSources,provider:result.provider,providerLabel:result.provider_label,latency:result.latency_ms,tokens:(result.usage.input_tokens||0)+(result.usage.output_tokens||0)});
   await this.loadRag();
  }catch(e){this.fail(e);this.messages.push({role:'assistant',text:'The request could not be completed. Check the message above, then retry. Your documents are still saved.'});}
  finally{this.searching=false;this.cd.detectChanges();setTimeout(()=>document.getElementById('conversation-end')?.scrollIntoView({behavior:'smooth',block:'nearest'}),20);}
 }
 newChat(){this.messages=[];this.question='';this.notice='Started a new conversation.';}
 sample(text:string){this.question=text;document.getElementById('question')?.focus();}
 download(){
  const content=this.messages.map(m=>m.role.toUpperCase()+': '+m.text+(m.matches||[]).map(x=>'\n['+x.source+' • '+x.location+']\n'+x.excerpt).join('\n')).join('\n\n');
  const url=URL.createObjectURL(new Blob([content],{type:'text/plain'}));const a=document.createElement('a');a.href=url;a.download='folio-conversation.txt';a.click();URL.revokeObjectURL(url);
 }
 fileName(source:string){return source.split('/').pop()||source;}
 extension(source:string){return source.split('.').pop()?.toUpperCase()||'FILE';}
 async citation(match:Match){const doc=this.docs.find(d=>d.id===match.document_id);if(doc)await this.open(doc);else this.error='This source has been deleted.';}
}
