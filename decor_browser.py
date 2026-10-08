"""Local Tk artwork gallery and portable HTML catalog."""
from __future__ import annotations

import base64
import html
import json
import math
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from decor_library import load_catalog

THEME_LABELS = {'auto':'Match each chapter automatically', 'botanical':'Botanical sanctuary',
    'arcane':'Arcane observatory', 'machinery':'Clockwork foundry', 'power':'Energy nexus',
    'space':'Celestial frontier', 'exploration':'Lost frontier', 'farming':'Harvest homestead',
    'ocean':'Abyssal sanctuary', 'nether':'Ember citadel', 'end':'Void archipelago',
    'storage':'Archive vault', 'restoration':'World reborn'}
KIND_LABELS = {'background':'Backgrounds', 'frame':'Frames', 'ornament':'Ornaments',
               'emblem':'Emblems', 'divider':'Dividers', 'panel':'Patterns',
               'landmark':'Landmarks', 'animation':'Animations'}


def animation_info(asset):
    """Browser-neutral metadata; the native game uses the same frame timings."""
    value = getattr(asset, 'animation', None)
    if not value:
        return None
    return {key: int(value[key]) for key in ('frame_width', 'frame_height', 'frames', 'frametime')}


def _frame_photo(master, source, asset, width, height, frame=0):
    animation = animation_info(asset)
    if animation:
        fw, fh = animation['frame_width'], animation['frame_height']
        frame %= animation['frames']
        columns = asset.width // fw
        x, y = frame % columns * fw, frame // columns * fh
        crop = tk.PhotoImage(master=master, width=fw, height=fh)
        crop.tk.call(str(crop), 'copy', str(source), '-from', x, y, x + fw, y + fh, '-to', 0, 0)
    else:
        crop = source
    factor = max(1, math.ceil(max(crop.width()/width, crop.height()/height)))
    return crop.subsample(factor)


def show_art_library(parent, on_choose, current='auto'):
    catalog = load_catalog()
    top = tk.Toplevel(parent)
    top.title('Artwork library')
    top.geometry('1100x760')
    top.minsize(900, 580)
    top.transient(parent)
    outer = ttk.Frame(top, padding=18)
    outer.pack(fill='both', expand=True)
    animations = sum(bool(animation_info(a)) for a in catalog.assets)
    ttk.Label(outer, text=f'Artwork library · {len(catalog.assets)} original pieces',
              font=('Segoe UI', 17, 'bold')).pack(anchor='w')
    ttk.Label(outer, text=f'Inspect large scenes, small landmarks and {animations} animations. Choose a theme for your book.').pack(anchor='w', pady=(6, 16))
    controls = ttk.Frame(outer)
    controls.pack(fill='x', pady=(0, 12))
    theme = tk.StringVar(value=THEME_LABELS.get(current, THEME_LABELS['auto']))
    kind = tk.StringVar(value='All artwork')
    ttk.Label(controls, text='Theme').pack(side='left', padx=(0, 8))
    theme_combo = ttk.Combobox(controls, textvariable=theme, state='readonly',
                             values=list(THEME_LABELS.values()), width=32)
    theme_combo.pack(side='left')
    ttk.Label(controls, text='Show').pack(side='left', padx=(18, 8))
    kind_combo = ttk.Combobox(controls, textvariable=kind, state='readonly',
                            values=['All artwork'] + list(KIND_LABELS.values()), width=18)
    kind_combo.pack(side='left')
    body = ttk.Frame(outer)
    body.pack(fill='both', expand=True)
    gallery = ttk.Frame(body)
    gallery.pack(side='left', fill='both', expand=True)
    inspector = ttk.Frame(body, width=330, padding=(16, 0, 0, 0))
    inspector.pack(side='right', fill='y')
    inspector.pack_propagate(False)
    detail_canvas = tk.Canvas(inspector, width=310, height=280, bg='#0d1724', highlightthickness=0)
    detail_canvas.pack(fill='x')
    detail_title = ttk.Label(inspector, font=('Segoe UI', 15, 'bold'), wraplength=300)
    detail_title.pack(anchor='w', pady=(14, 6))
    detail_caption = ttk.Label(inspector, wraplength=300)
    detail_caption.pack(anchor='w')
    play = ttk.Button(inspector, text='Pause animation')
    play.pack(anchor='w', pady=(14, 8))
    ttk.Label(inspector, text='The large preview shows the selected artwork. Animations also play in Minecraft with the included resource pack.', wraplength=300).pack(anchor='w', pady=(12, 0))
    canvas = tk.Canvas(gallery, bg='#101c2a', highlightthickness=0)
    scroll = ttk.Scrollbar(gallery, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side='left', fill='both', expand=True)
    scroll.pack(side='right', fill='y')
    grid = ttk.Frame(canvas, padding=12)
    window = canvas.create_window((0, 0), window=grid, anchor='nw')
    grid.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
    canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(int(-e.delta/120), 'units'))
    top._art_photos = []
    top._art_detail_photo = None
    top._art_selected_id = None
    state = {'asset': None, 'source': None, 'playing': True, 'start': 0.0, 'frame': 0, 'job': None}

    def paint_detail():
        if not top.winfo_exists() or state['asset'] is None:
            return
        asset = state['asset']
        animation = animation_info(asset)
        if animation and state['playing']:
            state['frame'] = int((time.monotonic() - state['start']) * 1000 / (animation['frametime'] * 50)) % animation['frames']
        photo = _frame_photo(top, state['source'], asset, 306, 276, state['frame'])
        top._art_detail_photo = photo
        detail_canvas.delete('all')
        detail_canvas.create_image(155, 140, image=photo)
        top._art_preview_frame = state['frame']
        if animation and state['playing']:
            state['job'] = top.after(max(20, animation['frametime'] * 50), paint_detail)

    def inspect(asset):
        if state['job'] is not None:
            top.after_cancel(state['job'])
            state['job'] = None
        state.update(asset=asset, source=tk.PhotoImage(master=top, file=str(catalog.root / asset.path)),
                     playing=True, start=time.monotonic(), frame=0)
        top._art_selected_id = asset.id
        detail_title.configure(text=asset.title)
        detail_caption.configure(text=f'{THEME_LABELS[asset.theme]} · {KIND_LABELS[asset.kind]}')
        play.configure(text='Pause animation', state='normal' if animation_info(asset) else 'disabled')
        paint_detail()

    def toggle_animation():
        if state['job'] is not None:
            top.after_cancel(state['job'])
            state['job'] = None
        state['playing'] = not state['playing']
        if state['playing']:
            duration = animation_info(state['asset'])['frametime'] * .05
            state['start'] = time.monotonic() - state['frame'] * duration
        play.configure(text='Pause animation' if state['playing'] else 'Play animation')
        paint_detail()
    play.configure(command=toggle_animation)
    top._art_inspect = inspect

    def cleanup(event):
        if event.widget is top and state['job'] is not None:
            top.after_cancel(state['job'])
            state['job'] = None
    top.bind('<Destroy>', cleanup, add='+')

    def redraw(*_):
        for child in grid.winfo_children():
            child.destroy()
        top._art_photos.clear()
        key = next(k for k, v in THEME_LABELS.items() if v == theme.get())
        selected_kind = next((k for k, v in KIND_LABELS.items() if v == kind.get()), None)
        assets = [a for a in catalog.assets if (key == 'auto' or a.theme == key)
                  and (selected_kind is None or a.kind == selected_kind)]
        for index, a in enumerate(assets):
            card = ttk.Frame(grid, padding=10)
            card.grid(row=index//2, column=index%2, sticky='nsew', padx=5, pady=5)
            source = tk.PhotoImage(master=top, file=str(catalog.root / a.path))
            photo = _frame_photo(top, source, a, 220, 135)
            top._art_photos.append(photo)
            ttk.Button(card, image=photo, command=lambda asset=a: inspect(asset)).pack(fill='x')
            ttk.Label(card, text=a.title, wraplength=220, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(8, 3))
            ttk.Label(card, text=KIND_LABELS[a.kind] + ' · ' + THEME_LABELS[a.theme], wraplength=220).pack(anchor='w')
        for col in range(2):
            grid.columnconfigure(col, weight=1, uniform='art')
        canvas.yview_moveto(0)
        if assets and state['asset'] not in assets:
            inspect(assets[0])

    def choose():
        key = next(k for k, v in THEME_LABELS.items() if v == theme.get())
        on_choose(key)
        top.destroy()
    footer = ttk.Frame(outer)
    footer.pack(fill='x', pady=(12, 0))
    ttk.Label(footer, text='All files are bundled. No AI, downloads or API key is needed to use them.').pack(side='left')
    ttk.Button(footer, text='Use this theme', command=choose).pack(side='right')
    theme_combo.bind('<<ComboboxSelected>>', redraw)
    kind_combo.bind('<<ComboboxSelected>>', redraw)
    redraw()
    return top


def write_gallery(path, catalog=None):
    catalog = catalog or load_catalog()
    cards = []
    metadata = []
    for a in catalog.assets:
        uri = 'data:image/png;base64,' + base64.b64encode(catalog.read(a)).decode('ascii')
        animation = animation_info(a)
        index = len(metadata)
        metadata.append({'title': a.title, 'theme': THEME_LABELS[a.theme], 'kind': KIND_LABELS[a.kind],
                         'width': a.width, 'height': a.height, 'animation': animation})
        if animation:
            fw, fh = animation['frame_width'], animation['frame_height']
            image = f'<svg viewBox="0 0 {fw} {fh}" aria-label="{html.escape(a.title, quote=True)}"><defs><clipPath id="frame-{index}"><rect width="{fw}" height="{fh}"/></clipPath></defs><image href="{uri}" width="{a.width}" height="{a.height}" clip-path="url(#frame-{index})"/></svg>'
        else:
            image = f'<img loading="lazy" src="{uri}" alt="{html.escape(a.title, quote=True)}">'
        cards.append(f'<article data-index="{index}" data-theme="{a.theme}" data-kind="{a.kind}"><button class="thumbnail" aria-label="Inspect {html.escape(a.title, quote=True)}">{image}</button><h2>{html.escape(a.title)}</h2><p>{html.escape(THEME_LABELS[a.theme])} · {KIND_LABELS[a.kind]}</p></article>')
    options = ''.join(f'<option value="{k}">{html.escape(v)}</option>' for k, v in THEME_LABELS.items() if k != 'auto')
    kinds = ''.join(f'<option value="{k}">{v}</option>' for k, v in KIND_LABELS.items())
    count = len(catalog.assets)
    animation_count = sum(bool(a['animation']) for a in metadata)
    data = json.dumps(metadata, ensure_ascii=False).replace('<', '\\u003c')
    doc = _GALLERY.replace('__COUNT__', str(count)).replace('__ANIMATIONS__', str(animation_count))
    doc = doc.replace('__THEME_COUNT__', str(len({a.theme for a in catalog.assets})))
    doc = doc.replace('__THEMES__', options).replace('__KINDS__', kinds).replace('__CARDS__', ''.join(cards)).replace('__ART_DATA__', data)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(doc, encoding='utf-8')
    return target


_GALLERY = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Original quest artwork library</title><style>
body{margin:0;background:#0b121c;color:#eaf0f8;font:16px/1.5 system-ui}main{max-width:1300px;margin:auto;padding:36px 24px}p{color:#aebfd1}h1{font-size:36px}h2{font-size:18px}select,button{font:inherit;padding:10px;background:#17283b;color:#edf6ff;border:1px solid #36516c;border-radius:6px;cursor:pointer}select{margin:0 12px 24px 0}button:focus-visible,select:focus-visible{outline:2px solid #78dfc1}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}article{padding:15px;background:#172334;border:1px solid #304355;border-radius:12px}.thumbnail{width:100%;padding:0;background:#0d1724}.thumbnail img,.thumbnail svg{display:block;width:100%;height:185px;object-fit:contain;border-radius:8px;overflow:hidden}article[hidden]{display:none}small{color:#78dfc1;letter-spacing:2px}dialog{width:min(1000px,90vw);max-height:90vh;overflow:auto;border:1px solid #36516c;border-radius:14px;background:#101c2a;color:#eaf0f8;padding:24px}dialog::backdrop{background:#000b}.detailbar{display:flex;align-items:center;gap:12px}.detailbar h2{flex:1;font-size:26px}#preview{display:block;width:100%;height:56vh;max-height:600px;object-fit:contain;margin:auto;background:#0b1420}#play[hidden]{display:none}#count{display:inline-block;color:#aebfd1}
</style><main><small>QUEST ARTWORK / BUNDLED & OFFLINE</small><h1>__COUNT__ original pieces. __THEME_COUNT__ distinct worlds.</h1><p>Large scenes, small landmarks and __ANIMATIONS__ animated accents. Click an image to inspect it. The included resource pack brings the same artwork and animations into Minecraft.</p><select id="theme"><option value="">All themes</option>__THEMES__</select><select id="kind"><option value="">All artwork</option>__KINDS__</select><span id="count"></span><div class="grid">__CARDS__</div></main>
<dialog id="inspect"><div class="detailbar"><h2 id="title"></h2><button id="close" aria-label="Close artwork preview">Close</button></div><canvas id="preview"></canvas><p id="caption"></p><button id="play">Pause animation</button></dialog>
<script id="art-data" type="application/json">__ART_DATA__</script><script>
'use strict';const $=id=>document.getElementById(id),assets=JSON.parse($('art-data').textContent),dialog=$('inspect'),canvas=$('preview'),context=canvas.getContext('2d'),motion=matchMedia('(prefers-reduced-motion: reduce)');let selected=null,image=null,paused=motion.matches,start=0,frame=0,job=0;
function filter(){let count=0;for(const card of document.querySelectorAll('article')){card.hidden=!!(($('theme').value&&card.dataset.theme!==$('theme').value)||($('kind').value&&card.dataset.kind!==$('kind').value));if(!card.hidden)count++}$('count').textContent=`${count} shown`}
function paint(now){job=0;if(!dialog.open||!image?.complete||!selected)return;const a=selected.animation;if(a&&!paused&&!document.hidden)frame=Math.floor((now-start)/(a.frametime*50))%a.frames;context.clearRect(0,0,canvas.width,canvas.height);if(a){const columns=Math.floor(selected.width/a.frame_width);context.drawImage(image,frame%columns*a.frame_width,Math.floor(frame/columns)*a.frame_height,a.frame_width,a.frame_height,0,0,canvas.width,canvas.height)}else context.drawImage(image,0,0,canvas.width,canvas.height);if(a&&!paused&&!document.hidden)job=requestAnimationFrame(paint)}
function inspect(card){cancelAnimationFrame(job);selected=assets[+card.dataset.index];frame=0;paused=motion.matches;start=performance.now();$('title').textContent=selected.title;$('caption').textContent=`${selected.theme} · ${selected.kind}`;canvas.width=selected.animation?.frame_width||selected.width;canvas.height=selected.animation?.frame_height||selected.height;$('play').hidden=!selected.animation;$('play').textContent=paused?'Play animation':'Pause animation';image=new Image();image.onload=()=>paint(performance.now());image.src=card.querySelector('img')?.src||card.querySelector('image').getAttribute('href');dialog.showModal()}
for(const card of document.querySelectorAll('article'))card.querySelector('button').onclick=()=>inspect(card);$('theme').onchange=$('kind').onchange=filter;$('close').onclick=()=>dialog.close();dialog.onclose=()=>{cancelAnimationFrame(job);job=0};$('play').onclick=()=>{paused=!paused;start=performance.now()-frame*selected.animation.frametime*50;$('play').textContent=paused?'Play animation':'Pause animation';cancelAnimationFrame(job);paint(performance.now())};document.addEventListener('visibilitychange',()=>{cancelAnimationFrame(job);job=0;if(!document.hidden&&dialog.open){start=performance.now()-frame*(selected.animation?.frametime||1)*50;paint(performance.now())}});motion.addEventListener('change',()=>{if(motion.matches){paused=true;$('play').textContent='Play animation';cancelAnimationFrame(job);job=0}});filter();
</script></html>'''
