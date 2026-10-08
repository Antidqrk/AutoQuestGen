"""Portable, offline preview of the exact chapters exported to FTB Quests."""
from __future__ import annotations

import json
from pathlib import Path


def write_preview(chapters, path, title="Quest book", notes=()):
    """No external scripts/fonts/assets and no game-folder writes."""
    references = {i.get('image', '') for _, chapter in chapters for i in chapter.get('images') or []
                  if str(i.get('image', '')).startswith('autoquestgen:')}
    textures = {}
    animations = {}
    if references:
        from decor_library import load_catalog, texture_data_uri
        catalog = load_catalog()
        catalog.validate_references(references)
        textures = {texture:texture_data_uri(texture, catalog=catalog) for texture in sorted(references)}
        for texture in references:
            asset = catalog.get(texture)
            animation = getattr(asset, 'animation', None)
            if animation:
                animations[texture] = {key: int(animation[key]) for key in
                                       ('frame_width', 'frame_height', 'frames', 'frametime')}
                animations[texture].update(width=asset.width, height=asset.height)
    payload = {"title": title or "Quest book", "notes": list(notes),
               "chapters": [chapter for _slug, chapter in chapters], "textures":textures,
               "texture_animations": animations}
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("&", "\\u0026")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_HTML.replace("__BOOK_DATA__", data), encoding="utf-8")
    return target


_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quest Atlas • Offline preview</title><style>
:root{color-scheme:dark;--bg:#0c101b;--panel:#151c2c;--line:#2c3853;--ink:#e8eefb;--muted:#99aac6;--accent:#74e0c2}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,sans-serif}
button,input{font:inherit}button{cursor:pointer;color:inherit;background:#202b43;border:1px solid #334361;border-radius:8px;padding:8px 12px}button:hover{border-color:var(--accent)}button:focus-visible,input:focus-visible{outline:2px solid var(--accent)}
header{height:86px;display:flex;align-items:center;justify-content:space-between;padding:16px 26px;border-bottom:1px solid var(--line)}h1{font-size:23px;letter-spacing:-.5px;margin:0}h2{font-size:20px;margin:0 0 8px}h3{font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);margin-top:24px}.eyebrow{color:var(--accent);letter-spacing:2px;font-size:10px;font-weight:750}.muted{color:var(--muted)}#stats{color:var(--muted)}
main{display:grid;grid-template-columns:240px minmax(300px,1fr) 320px;height:calc(100vh - 86px)}nav{padding:20px 12px;overflow:auto;border-right:1px solid var(--line)}nav button{display:block;text-align:left;width:100%;margin:4px 0;background:transparent;border-color:transparent}nav button.active{background:#25364c;color:var(--accent);border-color:#385c64}nav small{display:block;color:var(--muted)}
input[type=search]{width:100%;padding:10px;background:#0c1424;border:1px solid var(--line);border-radius:7px;color:var(--ink);margin-bottom:12px}section{min-width:0;min-height:0;display:flex;flex-direction:column}.toolbar{display:flex;align-items:center;gap:10px;padding:16px 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}.toolbar .chapter{flex:1;min-width:170px}.toolbar h2{margin:0;font-size:18px}.toolbar small{color:var(--muted)}.toolbar input{width:95px}#viewport{flex:1;overflow:auto;background-image:radial-gradient(#283146 1px,transparent 1px);background-size:22px 22px;position:relative}svg{display:block}.edge{stroke:#546684;stroke-width:1.8;fill:none}.edge.focus{stroke:var(--accent);stroke-width:3}.node{cursor:pointer;outline:none}.node polygon,.node circle,.node rect{fill:#1b2940;stroke:#6982a4;stroke-width:2}.node.optional polygon,.node.optional circle,.node.optional rect{stroke-dasharray:4 3}.node.selected polygon,.node.selected circle,.node.selected rect{stroke:var(--accent);stroke-width:3;fill:#234d4c}.node:hover polygon,.node:hover circle,.node:hover rect,.node:focus-visible polygon,.node:focus-visible circle{stroke:#fff}.node text{pointer-events:none}.label{fill:#cfdbee;font:10px system-ui;text-anchor:middle;paint-order:stroke;stroke:#0c101b;stroke-width:3px}.glyph{font:15px system-ui;fill:#e2ecff;text-anchor:middle;dominant-baseline:central}aside{border-left:1px solid var(--line);padding:24px;overflow:auto;background:var(--panel)}aside p{white-space:pre-wrap}#detail .badge{display:inline-block;padding:3px 8px;border-radius:5px;background:#28403e;color:var(--accent);font-size:11px;margin-bottom:12px}.task pre{white-space:pre-wrap;font-size:11px}.task details{margin-top:8px}.task summary{cursor:pointer;color:var(--muted)}.task{padding:10px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.dep{display:block;width:100%;text-align:left;margin:6px 0;font-size:12px}.foot{padding:9px 18px;font-size:11px;color:var(--muted);border-top:1px solid var(--line)}#notice{color:#ebc782;font-size:12px;margin-top:18px}code{font-size:11px;color:#afc1db;overflow-wrap:anywhere}
@media(max-width:1050px){main{grid-template-columns:190px minmax(280px,1fr) 270px}aside{padding:16px}}@media(max-width:760px){main{height:auto;grid-template-columns:1fr}nav{max-height:230px}section{height:65vh}aside{min-height:250px}header{height:auto;gap:15px}#stats{text-align:right}}
svg.compact .node:not(.selected):not(:hover):not(:focus-visible) .label{display:none}
</style></head><body><header><div><div class="eyebrow">QUEST ATLAS / OFFLINE</div><h1 id="bookTitle"></h1></div><div id="stats"></div></header>
<main><nav><input id="search" type="search" placeholder="Find a quest or chapter…" aria-label="Find a quest or chapter"><div id="chapters"></div></nav><section><div class="toolbar"><div class="chapter"><h2 id="chapterTitle"></h2><small id="chapterStats"></small></div><button id="fit">Fit map</button><button id="motion" hidden>Pause artwork</button><label>Zoom <input id="zoom" type="range" min="1" max="180" value="70" aria-label="Map zoom"></label></div><div id="viewport"><svg id="map" role="group" aria-label="Quest dependency map"></svg></div><div class="foot">Click a quest for details · Hover for titles · Arrows follow prerequisites</div></section><aside><div id="detail"><div class="eyebrow">EXPLORE THE BOOK</div><h2>Choose your next adventure.</h2><p class="muted">Select a chapter, then click any quest to read its objectives and follow its prerequisites.</p><p class="muted">This preview uses the exported positions, shapes, and dependencies. Minecraft renders the item textures in game.</p></div><div id="notice"></div></aside></main>
<script id="book-data" type="application/json">__BOOK_DATA__</script><script>
'use strict';
const book=JSON.parse(document.getElementById('book-data').textContent), $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const clean=s=>String(s??'').replace(/(?:&|§)[0-9a-fk-or]/gi,''), all=new Map();
book.chapters.forEach((c,ci)=>(c.quests||[]).forEach(q=>all.set(q.id,{q,ci})));
let current=0, selected='', scale=.7;
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');let artworkPlaying=!reducedMotion.matches, animatedArt=[], artworkJob=0, artworkTime=0, artworkLast=performance.now();
function animateArtwork(now){artworkJob=0;if(artworkPlaying&&!document.hidden)artworkTime+=now-artworkLast;artworkLast=now;for(const row of animatedArt){const a=row.animation,frame=Math.floor(artworkTime/(a.frametime*50))%a.frames,columns=Math.floor(a.width/a.frame_width);row.image.setAttribute('x',-(frame%columns)*a.frame_width);row.image.setAttribute('y',-Math.floor(frame/columns)*a.frame_height)}if(artworkPlaying&&animatedArt.length&&!document.hidden)artworkJob=requestAnimationFrame(animateArtwork)}
function syncArtwork(){cancelAnimationFrame(artworkJob);artworkLast=performance.now();$('motion').hidden=!animatedArt.length;$('motion').textContent=artworkPlaying?'Pause artwork':'Play artwork';animateArtwork(artworkLast)}
$('motion').onclick=()=>{artworkPlaying=!artworkPlaying;syncArtwork()};document.addEventListener('visibilitychange',syncArtwork);reducedMotion.addEventListener('change',()=>{if(reducedMotion.matches){artworkPlaying=false;syncArtwork()}});
$('bookTitle').textContent=clean(book.title);$('stats').textContent=`${book.chapters.length} chapters · ${all.size} quests · No internet required`;
$('notice').textContent=(book.notes||[]).map(clean).join('\n');
function el(tag,attrs={},text){const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!=null)e.textContent=text;return e}
function html(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e}
function names(){const term=$('search').value.toLowerCase();$('chapters').replaceChildren();book.chapters.forEach((c,i)=>{const hit=!term||clean(c.title).toLowerCase().includes(term)||(c.quests||[]).some(q=>clean(q.title).toLowerCase().includes(term));if(!hit)return;const b=html('button',clean(c.title),i===current?'active':'');b.append(html('small',`${(c.quests||[]).length} quests`));b.onclick=()=>{current=i;selected='';names();fit();if(c.quests?.length)select(c.quests[0].id);};$('chapters').append(b)})}
function points(shape,r){const n={diamond:4,pentagon:5,hexagon:6,heptagon:7,octagon:8,gear:16}[shape]||4;const start=shape==='square'?Math.PI/4:-Math.PI/2;return Array.from({length:n},(_,i)=>{let rad=r*(shape==='gear'&&i%2?0.8:1);return `${Math.cos(start+i*2*Math.PI/n)*rad},${Math.sin(start+i*2*Math.PI/n)*rad}`}).join(' ')}
function bounds(){const qs=book.chapters[current]?.quests||[];if(!qs.length)return [0,0,2,2];return [Math.min(...qs.map(q=>+q.x||0)),Math.min(...qs.map(q=>+q.y||0)),Math.max(...qs.map(q=>+q.x||0)),Math.max(...qs.map(q=>+q.y||0))]}
function draw(){const c=book.chapters[current];if(!c)return;const qs=c.quests||[], [minx,miny,maxx,maxy]=bounds(), unit=74*scale,pad=65, width=Math.max($('viewport').clientWidth,(maxx-minx)*unit+2*pad),height=Math.max($('viewport').clientHeight,(maxy-miny)*unit+2*pad), xy=q=>[(+q.x-minx)*unit+pad,(+q.y-miny)*unit+pad];
$('chapterTitle').textContent=clean(c.title);$('chapterStats').textContent=`${qs.length} quests · ${qs.filter(q=>q.optional).length} optional · ${(c.subtitle||[]).map(clean).join(' ')}`;
const map=$('map');map.replaceChildren();map.setAttribute('width',width);map.setAttribute('height',height);const defs=el('defs'), marker=el('marker',{id:'arrow',viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});marker.append(el('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#7b91ad'}));defs.append(marker);map.append(defs);
const local=new Map(qs.map(q=>[q.id,q]));
animatedArt=[];
for(const art of [...(c.images||[])].sort((a,b)=>(+a.order||0)-(+b.order||0))){const uri=(book.textures||{})[art.image];if(!uri)continue;const [x,y]=xy(art),w=(+art.width||1)*unit,h=(+art.height||1)*unit,attrs={x:x-w/2,y:y-h/2,width:w,height:h,preserveAspectRatio:'none',opacity:Math.max(0,Math.min(255,Number(art.alpha??255)))/255,transform:`rotate(${+art.rotation||0} ${x} ${y})`,'pointer-events':'none'},animation=(book.texture_animations||{})[art.image];if(animation){const viewport=el('svg',{...attrs,viewBox:`0 0 ${animation.frame_width} ${animation.frame_height}`,overflow:'hidden'}),image=el('image',{href:uri,x:0,y:0,width:animation.width,height:animation.height,preserveAspectRatio:'none',style:'image-rendering:pixelated'});viewport.append(image);map.append(viewport);animatedArt.push({image,animation})}else map.append(el('image',{...attrs,href:uri}))}
syncArtwork();
const crowded=scale<.7||qs.some((q,i)=>qs.slice(i+1).some(p=>Math.abs(+q.x-+p.x)*unit<92&&Math.abs(+q.y-+p.y)*unit<62));map.classList.toggle('compact',crowded);
for(const q of qs)for(const d of q.dependencies||[]){const p=local.get(d);if(!p||p.hide_dependent_lines||q.hide_dependency_lines)continue;const a=xy(p),b=xy(q),len=Math.hypot(b[0]-a[0],b[1]-a[1])||1,r=21*scale,dx=(b[0]-a[0])/len,dy=(b[1]-a[1])/len;map.append(el('line',{x1:a[0]+dx*r,y1:a[1]+dy*r,x2:b[0]-dx*r,y2:b[1]-dy*r,class:'edge'+(q.id===selected||d===selected?' focus':''),'marker-end':'url(#arrow)'}))}
for(const q of qs){const [x,y]=xy(q),r=19*scale*Math.min(6,Number(q.size)||1),g=el('g',{transform:`translate(${x} ${y})`,class:`node${q.optional?' optional':''}${q.id===selected?' selected':''}`,tabindex:0,role:'button','aria-label':clean(q.title)}),shape=q.shape||c.default_quest_shape||'circle';
g.append(shape==='circle'?el('circle',{r}):shape==='rsquare'?el('rect',{x:-r,y:-r,width:r*2,height:r*2,rx:7}):el('polygon',{points:points(shape,r)}));const kind=(q.tasks||[])[0]?.type||'checkmark',glyph={item:'◆',kill:'⚔',checkmark:'✓',dimension:'◇',advancement:'★',biome:'♧',structure:'⌂',location:'⌖'}[kind]||'•';g.append(el('text',{class:'glyph',style:`font-size:${Math.max(6,15*scale)}px`},glyph));
const title=clean(q.title),words=title.split(' '),lines=[''];for(const word of words){let i=lines.length-1;if((lines[i]+' '+word).trim().length>16&&lines[i]){if(lines.length===2){lines[1]=lines[1].slice(0,15)+'…';break;}lines.push(word);}else lines[i]=(lines[i]+' '+word).trim();}const labelNode=el('text',{class:'label',y:r+14});lines.forEach((line,i)=>labelNode.append(el('tspan',{x:0,dy:i?11:0},line)));g.append(labelNode);g.append(el('title',{},title));g.onclick=()=>select(q.id);g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(q.id)}};map.append(g)}
}
function item(t){const v=t.item;return typeof v==='object'?(v.id||''):v||''}
function stackCount(t){return Number(t.count)||(typeof t.item==='object'?Number(t.item?.count):0)||1}
function taskText(t){if(t.type==='item')return `${stackCount(t)} × ${item(t)}${t.match_components?' · exact item data required':''}`;if(t.type==='kill')return `Defeat ${Number(t.value)||1} × ${t.entity||''}`;if(t.type==='checkmark')return clean(t.title||'Read and confirm when ready');return `${t.type}: ${t.advancement||t.dimension||t.biome||t.structure||clean(t.title)||'Complete this objective'}`}
function taskElement(t){const row=html('div',taskText(t),'task');if(t.item?.components){const more=document.createElement('details');more.append(html('summary','Required item details'),html('pre',JSON.stringify(t.item.components,null,2)));row.append(more)}return row}
function jump(id){const entry=all.get(id);if(!entry)return;current=entry.ci;names();fit();select(id);const node=[...$('map').querySelectorAll('.node')].find(n=>n.getAttribute('aria-label')===clean(entry.q.title));node?.scrollIntoView({block:'center',inline:'center'})}
function select(id){selected=id;const q=all.get(id)?.q;if(!q)return;draw();const box=$('detail');box.replaceChildren(html('div',q.optional?'OPTIONAL QUEST':'PROGRESSION QUEST','badge'),html('h2',clean(q.title)));if(q.subtitle)box.append(html('p',clean(q.subtitle),'muted'));for(const line of q.description||[])if(clean(line).trim())box.append(html('p',clean(line)));box.append(html('h3','Objectives'));for(const t of q.tasks||[])box.append(taskElement(t));if((q.rewards||[]).length){box.append(html('h3','Rewards'));for(const r of q.rewards){const text=r.type==='item'?`${stackCount(r)} × ${item(r)}`:r.type==='xp'?`${r.xp||0} XP`:r.type==='xp_levels'?`${r.xp_levels||r.levels||1} experience levels`:r.type==='loot'?'Loot reward':r.type;box.append(html('div',text,'task'))}}box.append(html('h3','Prerequisites'));if(!(q.dependencies||[]).length)box.append(html('p','Start here — no prerequisites.','muted'));for(const d of q.dependencies||[]){const ent=all.get(d);const b=html('button',ent?clean(ent.q.title)+(ent.ci!==current?' · '+clean(book.chapters[ent.ci].title):''):d,'dep');b.onclick=()=>jump(d);box.append(b)}box.append(html('h3','Quest ID'),html('code',q.id))}
function fit(){const [x1,y1,x2,y2]=bounds();scale=Math.max(.01,Math.min(1,($('viewport').clientWidth-130)/Math.max(1,(x2-x1)*74),($('viewport').clientHeight-130)/Math.max(1,(y2-y1)*74)));$('zoom').value=scale*100;draw();$('viewport').scrollTo(0,0)}
$('fit').onclick=fit;$('zoom').oninput=()=>{scale=+$('zoom').value/100;draw()};$('search').oninput=names;window.addEventListener('resize',draw);names();fit();
</script></body></html>'''
