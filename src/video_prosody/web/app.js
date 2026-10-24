const $=id=>document.getElementById(id);
const localPlayer=$('player');
let player=localPlayer;
const playbackExtension=window.createReviewPlayback?.({localPlayer,setPlayer(value){player=value;},sync:syncPlayback,status(message){$('playbackStatus').textContent=message;}});
let doc=null, selected=null, active=null, loopEnd=null, loading=false;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=s=>`${Math.floor(s/60)}:${(s%60).toFixed(2).padStart(5,'0')}`;
async function request(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok)throw Error(d.error||'読み込みに失敗しました');return d;}
function dirty(){return [...document.querySelectorAll('.segment')].some(e=>e.dataset.dirty==='true');}
function progress(){const count=doc.segments.filter(s=>s.verified).length;$('progressBar').style.width=`${100*count/doc.segments.length}%`;$('progressText').textContent=`確認済み ${count} / ${doc.segments.length} 区間`;$('historyCount').textContent=doc.history.length;}
function history(){ $('history').innerHTML=doc.history.slice().reverse().map(e=>`<div class="historyItem"><b>${esc(new Date(e.created_at_utc).toLocaleString('ja-JP'))} / ${time(e.start)}</b><br>${esc(e.before||'（発言なし）')} → ${esc(e.after||'（発言なし）')}<br>${e.verified?'確認済み':'未確認'}${e.reason?` / ${esc(e.reason)}`:''}</div>`).join('')||'手動訂正はまだありません。';}
function render(){
 $('segments').innerHTML=doc.segments.map(s=>`<article class="segment ${s.verified?'verified':''}" data-id="${s.id}" ${$('pendingOnly').checked&&s.verified?'hidden':''}>
 <div class="segmentTop"><button class="time" data-seek="${s.id}" aria-label="区間${s.id}を再生">▶ ${time(s.start)} – ${time(s.end)}</button><span class="badge ${s.notes.some(n=>n.requires_audio_review||n.action==='retain_pending_audio_review')?'review':''}">${s.text===''&&s.verified?'発言なし':s.verified?'確認済み':s.notes.some(n=>n.requires_audio_review||n.action==='retain_pending_audio_review')?'要確認':'未確認'}</span></div>
 <div class="raw"><span>認識原文</span>${esc(s.raw_text)}</div>
 <textarea aria-label="区間${s.id}の訂正文">${esc(s.text)}</textarea>
 ${s.notes.length?`<details class="notes"><summary>これまでの補正・確認メモ (${s.notes.length})</summary>${s.notes.map(n=>`<div>${n.before?`${esc(n.before)} → ${esc(n.after)}：`:''}${esc(n.reason)}</div>`).join('')}</details>`:''}
 <div class="actions"><label><input type="checkbox" class="verifiedCheck" ${s.verified?'checked':''}> 音声を確認済み</label><input class="reason" aria-label="区間${s.id}の訂正理由" placeholder="訂正理由・聞き取りメモ（任意）"><button class="save">保存</button><button class="noSpeech" aria-label="区間${s.id}を発言なしにする">発言なし</button><button class="reset">提案に戻す</button></div><div class="saveStatus" role="status"></div>
 </article>`).join('');
 for(const row of document.querySelectorAll('.segment')){
   const id=Number(row.dataset.id), s=doc.segments.find(s=>s.id===id);
   row.querySelector('[data-seek]').onclick=()=>seek(s);
   for(const input of row.querySelectorAll('textarea,input')) input.oninput=()=>{row.dataset.dirty='true';row.querySelector('.saveStatus').textContent='未保存';};
   row.querySelector('.save').onclick=()=>save(row,s);
   row.querySelector('.noSpeech').onclick=()=>{
     row.querySelector('textarea').value='';
     row.querySelector('.verifiedCheck').checked=true;
     const reason=row.querySelector('.reason');
     reason.value=reason.value.trim()?`発言なし（誤認識を削除）：${reason.value.trim()}`:'発言なし（誤認識を削除）';
     row.dataset.dirty='true';
     save(row,s);
   };
   row.querySelector('.reset').onclick=()=>{row.querySelector('textarea').value=s.suggested_text;row.querySelector('.verifiedCheck').checked=false;row.dataset.dirty='true';row.querySelector('.saveStatus').textContent='提案に戻しました。保存で確定します。';};
 }
 progress();history();
}
async function save(row,s){
 const buttons=[...row.querySelectorAll('.save,.noSpeech,.reset')];buttons.forEach(b=>b.disabled=true);
 const value=row.querySelector('textarea').value, verified=row.querySelector('.verifiedCheck').checked, reason=row.querySelector('.reason').value;
 try{const result=await request('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_id:doc.video_id,segment_id:s.id,text:value,verified,reason,expected_revision:doc.revision})});
 doc.revision=result.revision;s.text=value;s.verified=verified;if(result.event)doc.history.push(result.event);
 const stillDirty=row.querySelector('textarea').value!==value||row.querySelector('.verifiedCheck').checked!==verified||row.querySelector('.reason').value!==reason;
 row.dataset.dirty=String(stillDirty);row.classList.toggle('verified',verified);row.querySelector('.badge').textContent=value===''&&verified?'発言なし':verified?'確認済み':'未確認';row.querySelector('.saveStatus').textContent=stillDirty?'保存済み。その後の変更は未保存です。':value===''?'発言なしとして保存済み':'保存済み';progress();history();
 }catch(e){row.querySelector('.saveStatus').textContent=e.message;}finally{buttons.forEach(b=>b.disabled=false);}
}
function seek(s){selected=s.id;loopEnd=s.end+.25;player.currentTime=Math.max(0,s.start-.25);player.play().catch(e=>$('globalStatus').textContent=e.message);highlight(s.id,false);}
function highlight(id,follow){if(active===id)return;active=id;document.querySelectorAll('.segment.active').forEach(e=>e.classList.remove('active'));const row=document.querySelector(`.segment[data-id="${id}"]`);if(row){row.classList.add('active');if(follow&&$('follow').checked&&!document.activeElement.matches('textarea,input'))row.scrollIntoView({block:'nearest',behavior:'smooth'});}}
function syncPlayback(){if(!doc)return;if($('loop').checked&&selected!==null&&loopEnd!==null&&player.currentTime>=loopEnd){const s=doc.segments.find(s=>s.id===selected);player.currentTime=Math.max(0,s.start-.25);}const s=doc.segments.find(s=>player.currentTime>=s.start&&player.currentTime<s.end);if(s)highlight(s.id,true);}
localPlayer.addEventListener('timeupdate',()=>{if(player===localPlayer)syncPlayback();});
localPlayer.addEventListener('error',()=>{$('playbackStatus').textContent='ローカルメディアを再生できません。ファイルの状態を確認してください。';});
async function setPlayback(reset=false){
 const position=reset?0:player.currentTime;
 player.pause();
 if(playbackExtension)return playbackExtension.load(doc,{position});
 player=localPlayer;
 player.src=doc.media_url||doc.audio_url;
 player.classList.toggle('audioOnly',!doc.media_url&&!!doc.audio_url);
 player.currentTime=position;
 player.playbackRate=Number($('speed').value);
 $('playbackStatus').textContent='ローカルのメディアを再生します。';
}
async function load(id){if(loading)return;if(dirty()&&!confirm('未保存の訂正があります。破棄して動画を切り替えますか？'))return;loading=true;
try{document.querySelectorAll('a[href^="/prosody"],a[href^="/moras"]').forEach(a=>a.href=a.pathname+'?id='+id);player.pause();doc=await request(`/api/video?id=${id}`);selected=null;active=null;loopEnd=null;$('title').textContent=doc.title;$('source').hidden=!doc.source_url;$('source').href=doc.source_url||'';document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.id===id));render();$('globalStatus').textContent='原文保持 · 訂正履歴をローカル保存';await setPlayback(true);}catch(e){$('globalStatus').textContent=e.message;}finally{loading=false;}}
$('back').onclick=()=>player.currentTime=Math.max(0,player.currentTime-2);
$('forward').onclick=()=>player.currentTime=Math.min(player.duration||Infinity,player.currentTime+2);
$('play').onclick=()=>{if(player.paused)player.play().catch(e=>$('playbackStatus').textContent=e.message);else player.pause();};
$('speed').onchange=()=>player.playbackRate=Number($('speed').value);
$('pendingOnly').onchange=()=>{if(dirty()){$('pendingOnly').checked=!$('pendingOnly').checked;alert('先に訂正を保存してください。');return;}render();};
$('export').onclick=async()=>{if(!doc)return;try{if(dirty())throw Error('書き出す前に訂正を保存してください。');const data=await request(`/api/export?id=${doc.video_id}`);const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`${doc.video_id}-reviewed.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){$('globalStatus').textContent=e.message;}};
window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
request('/api/catalog').then(videos=>{ $('videos').innerHTML=videos.map(v=>`<button data-id="${v.video_id}">${esc(v.title)}</button>`).join('');document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>load(b.dataset.id));if(videos.length)return load(new URLSearchParams(location.search).get('id')||videos[0].video_id);}).catch(e=>$('globalStatus').textContent=e.message);
