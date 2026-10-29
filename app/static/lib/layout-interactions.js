/* Direct manipulation is committed once on release; cancelled gestures change nothing. */
export function groupedItems(items, sections) {
  const ids=new Set(sections.map(s=>s.id));
  return [...sections.flatMap(s=>items.filter(i=>i.section_id===s.id)),...items.filter(i=>!ids.has(i.section_id))];
}

export function movePreviewItem(items, sections, sourceId, targetId, before=true) {
  if(sourceId===targetId)return items;
  const source=items.find(i=>i.id===sourceId),target=items.find(i=>i.id===targetId);
  if(!source||!target)return items;
  const result=items.filter(i=>i.id!==sourceId),index=result.findIndex(i=>i.id===targetId)+(before?0:1);
  result.splice(index,0,{...source,section_id:target.section_id||null});
  return groupedItems(result,sections).map((item,n)=>({...item,order_no:n}));
}

export function resizedSpace(initial, deltaPixels, pixelsPerMm) {
  if(!Number.isFinite(pixelsPerMm)||pixelsPerMm<=0)return initial;
  return Math.max(0,Math.min(200,Math.round((initial+deltaPixels/pixelsPerMm)/5)*5));
}

export function bindLayoutInteractions(canvas, {select, move, resize, announce}) {
  let gesture=null,frame=0;
  const pages=()=>canvas.querySelector('[data-pages]');
  function clear() {
    cancelAnimationFrame(frame);frame=0;
    canvas.querySelectorAll('.drop-before,.drop-after,.paper-moving').forEach(el=>el.classList.remove('drop-before','drop-after','paper-moving'));
    canvas.classList.remove('layout-manipulating');
    if(gesture?.handle.hasPointerCapture?.(gesture.pointerId))gesture.handle.releasePointerCapture(gesture.pointerId);
  }
  function pointTarget(x,y) {
    const box=canvas.getBoundingClientRect();
    if(x<box.left||x>box.right||y<box.top-35||y>box.bottom+35)return null;
    const candidates=[...pages().querySelectorAll('[data-paper-item]')].filter(el=>el.dataset.paperItem!==gesture.id);
    const nearest=candidates.map(el=>({el,box:el.getBoundingClientRect()}))
      .filter(t=>t.box.bottom>=box.top&&t.box.top<=box.bottom)
      .sort((a,b)=>Math.abs(y-(a.box.top+a.box.bottom)/2)-Math.abs(y-(b.box.top+b.box.bottom)/2))[0];
    return nearest?{id:nearest.el.dataset.paperItem,before:y<(nearest.box.top+nearest.box.bottom)/2,el:nearest.el}:null;
  }
  function drawMove() {
    if(!gesture||gesture.kind!=='move')return;
    const g=gesture,box=canvas.getBoundingClientRect();
    if(g.active){
      const dy=g.y<box.top+48?-12:g.y>box.bottom-48?12:0;
      if(dy)canvas.scrollTop+=dy;
      const target=pointTarget(g.x,g.y);
      canvas.querySelectorAll('.drop-before,.drop-after').forEach(el=>el.classList.remove('drop-before','drop-after'));
      target?.el.classList.add(target.before?'drop-before':'drop-after');g.target=target;
    }
    frame=requestAnimationFrame(drawMove);
  }
  function start(event) {
    if(event.button!==0||gesture)return;
    const handle=event.target.closest('[data-paper-drag],[data-space-resize]');
    if(!handle||!canvas.contains(handle))return;
    event.preventDefault();
    const item=handle.closest('[data-paper-item]'),space=handle.closest('.paper-space');
    select(item.dataset.paperItem);
    gesture={kind:space?'resize':'move',handle,pointerId:event.pointerId,id:item.dataset.paperItem,
      x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,active:false,target:null};
    if(space){
      gesture.space=space;gesture.initial=Number(handle.getAttribute('aria-valuenow'));
      gesture.originalStyle=space.style.height;
      gesture.pixelsPerMm=(space.getBoundingClientRect().width/space.offsetWidth)*(96/25.4);
    }else frame=requestAnimationFrame(drawMove);
    handle.setPointerCapture(event.pointerId);canvas.classList.add('layout-manipulating');
    handle.focus({preventScroll:true});
  }
  function pointerMove(event) {
    if(!gesture||event.pointerId!==gesture.pointerId)return;
    const g=gesture;g.x=event.clientX;g.y=event.clientY;
    if(Math.hypot(g.x-g.startX,g.y-g.startY)>4)g.active=true;
    if(!g.active)return;
    if(g.kind==='resize'){
      g.value=resizedSpace(g.initial,event.clientY-g.startY,g.pixelsPerMm);
      g.space.style.height=`${g.value}mm`;g.handle.setAttribute('aria-valuenow',String(g.value));
      g.handle.setAttribute('aria-valuetext',`${g.value}毫米`);
      g.space.querySelector('[data-space-label]').textContent=`作答区 · ${g.value} mm`;
      announce(`留白 ${g.value} mm · 松开后重新分页 · Esc 取消`);
    }else {g.handle.closest('[data-paper-item]').classList.add('paper-moving');announce('拖到目标题目的上方或下方；靠近预览边缘可滚动 · Esc 取消');}
  }
  function finish(event,cancel=false) {
    if(!gesture||(event?.pointerId!==undefined&&event.pointerId!==gesture.pointerId))return;
    const g=gesture;
    if(!cancel&&g.active&&g.kind==='move'&&event)g.target=pointTarget(event.clientX,event.clientY);
    // Clear before releasing capture, whose lostpointercapture event may run synchronously.
    gesture=null;cancelAnimationFrame(frame);frame=0;
    canvas.querySelectorAll('.drop-before,.drop-after,.paper-moving').forEach(el=>el.classList.remove('drop-before','drop-after','paper-moving'));
    canvas.classList.remove('layout-manipulating');
    if(g.handle.hasPointerCapture?.(g.pointerId))g.handle.releasePointerCapture(g.pointerId);
    if(g.kind==='resize'){
      g.space.style.height=g.originalStyle;g.handle.setAttribute('aria-valuenow',String(g.initial));g.handle.setAttribute('aria-valuetext',`${g.initial}毫米`);
      g.space.querySelector('[data-space-label]').textContent=`作答区 · ${g.initial} mm`;
      if(!cancel&&g.active&&g.value!==g.initial)resize(g.id,g.value);
    }else if(!cancel&&g.active&&g.target)move(g.id,g.target.id,g.target.before);
    announce(cancel?'操作已取消':g.active?'调整完成，正在保存':'可拖动排序，或拖动作答区底边调整留白');
  }
  function key(event) {
    if(event.key==='Escape'&&gesture){event.preventDefault();finish(null,true);return;}
    const handle=event.target.closest('[data-paper-drag],[data-space-resize]');if(!handle)return;
    const id=handle.closest('[data-paper-item]').dataset.paperItem;
    if(handle.hasAttribute('data-space-resize')&&['ArrowUp','ArrowDown','Home','End'].includes(event.key)){
      event.preventDefault();resize(id,event.key==='Home'?0:event.key==='End'?200:Math.max(0,Math.min(200,Number(handle.getAttribute('aria-valuenow'))+(event.key==='ArrowUp'?5:-5))));
    }else if(handle.hasAttribute('data-paper-drag')&&['ArrowUp','ArrowDown'].includes(event.key)){
      event.preventDefault();const rows=[...pages().querySelectorAll('[data-paper-item]')],index=rows.findIndex(el=>el.dataset.paperItem===id),target=rows[index+(event.key==='ArrowUp'?-1:1)];
      if(target)move(id,target.dataset.paperItem,event.key==='ArrowUp');
    }
  }
  canvas.addEventListener('pointerdown',start);canvas.addEventListener('pointermove',pointerMove);
  const end=e=>finish(e),cancel=e=>finish(e,true);
  canvas.addEventListener('pointerup',end);canvas.addEventListener('pointercancel',cancel);canvas.addEventListener('lostpointercapture',cancel);canvas.addEventListener('keydown',key);
  const blur=()=>finish(null,true);window.addEventListener('blur',blur);
  return ()=>{finish(null,true);clear();window.removeEventListener('blur',blur);canvas.removeEventListener('pointerdown',start);canvas.removeEventListener('pointermove',pointerMove);canvas.removeEventListener('pointerup',end);canvas.removeEventListener('pointercancel',cancel);canvas.removeEventListener('lostpointercapture',cancel);canvas.removeEventListener('keydown',key);};
}
