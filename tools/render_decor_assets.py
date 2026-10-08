"""Draw original AutoQuestGen map ornaments. No downloaded art or font glyphs.

Pillow is used as a native UI drawing surface. All artwork is defined by the
editable geometry below; text appears only on the separate review contact sheet.
Run from any folder: python tools/render_decor_assets.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
THEMES = {
    "botanical": ("Living Garden", "#9cd87b", "#3c9b83", ["botania", "nature", "flowers", "mana", "garden"]),
    "arcane": ("Astral Workshop", "#caa6fa", "#d6bc76", ["magic", "ritual", "spell", "arcane", "occultism"]),
    "machinery": ("Clockwork Foundry", "#e0b581", "#829aa9", ["create", "machine", "factory", "engineering", "gear"]),
    "power": ("Electric Circuit", "#8ae6ed", "#ffcf67", ["power", "energy", "mekanism", "generator", "electric"]),
    "space": ("Distant Orbits", "#9dbdec", "#e3dfbe", ["space", "rocket", "planet", "ad_astra", "stars"]),
    "exploration": ("Beyond the Trail", "#d9bd83", "#91b6a5", ["explore", "journey", "adventure", "travel", "compass"]),
    "farming": ("Golden Harvest", "#ebc76d", "#94ba69", ["farm", "crops", "food", "harvest", "cooking"]),
    "ocean": ("Tidal Sanctuary", "#79d0d9", "#e3a395", ["ocean", "water", "underwater", "sea", "aquatic"]),
    "nether": ("Ember Depths", "#f5b266", "#d77780", ["nether", "lava", "fire", "blaze", "infernal"]),
    "end": ("The Quiet Rift", "#c3a4ea", "#86d6bd", ["end", "ender", "dragon", "void", "portal"]),
    "storage": ("Ordered Archive", "#9acece", "#d4bc89", ["storage", "ae2", "refinedstorage", "inventory", "network"]),
    "restoration": ("The Returning Spring", "#a4d4a1", "#c7dfb3", ["restore", "restoration", "regrowth", "healing", "life"]),
}
KINDS = ("frame", "ornament", "emblem", "divider", "panel")
SIZES = {"frame": (512, 512), "ornament": (512, 256), "emblem": (256, 256),
         "divider": (512, 128), "panel": (512, 512)}
NAMES = {
    "botanical": ("Vinebound Corners", "Unfurling Boughs", "Seed of Mana", "Trailing Leaves", "Garden Trellis"),
    "arcane": ("Celestial Wards", "Forked Incantation", "Prism of Insight", "Orbital Notation", "Constellation Lattice"),
    "machinery": ("Riveted Housing", "Transmission Manifold", "Foundry Gear", "Rack and Pinion", "Workshop Grating"),
    "power": ("Circuit Terminals", "Branching Current", "Charged Cell", "Pulse Conductor", "Capacitor Matrix"),
    "space": ("Orbital Viewport", "Asterism Drift", "Ringed World", "Comet Passage", "Star Survey"),
    "exploration": ("Surveyor Corners", "Forking Trail", "Compass Rose", "Mountain Horizon", "Contour Atlas"),
    "farming": ("Wheatbound Border", "Harvest Fan", "The First Sheaf", "Furrow Rhythm", "Field Quilt"),
    "ocean": ("Tidal Curves", "Branching Coral", "Pearl and Shell", "Rolling Swell", "Reef Ripples"),
    "nether": ("Basalt Fracture", "Lava Tributaries", "The Ember Crucible", "Cinder Teeth", "Cooling Obsidian"),
    "end": ("Suspended Ruins", "Splintered Passage", "Rift Crystal", "Broken Orbit", "Void Mosaic"),
    "storage": ("Archive Compartments", "Linked Inventories", "The Index Vault", "Transfer Bus", "Repository Grid"),
    "restoration": ("Root and Rain", "Returning Roots", "A New Dawn", "Springwater Course", "Regrowth Rings"),
}


def rgba(hex_color, alpha=255):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5)) + (max(0, min(255, round(alpha))),)


class Canvas:
    def __init__(self, size, theme, scale=3):
        self.size, self.scale = size, scale
        self.image = Image.new("RGBA", (size[0] * scale, size[1] * scale))
        self.draw = ImageDraw.Draw(self.image)
        self.a, self.b = THEMES[theme][1:3]

    def color(self, color=None, alpha=230):
        return rgba(color or self.a, alpha)

    def points(self, pts):
        return [(round(x * self.scale), round(y * self.scale)) for x, y in pts]

    def line(self, pts, color=None, width=2, alpha=220):
        self.draw.line(self.points(pts), fill=self.color(color, alpha), width=max(1, round(width * self.scale)), joint="curve")

    def polygon(self, pts, color=None, width=2, alpha=220, fill=False):
        if fill:
            self.draw.polygon(self.points(pts), fill=self.color(color, alpha))
        else:
            self.line(list(pts) + [pts[0]], color, width, alpha)

    def ellipse(self, box, color=None, width=2, alpha=220, fill=False):
        box = tuple(round(v * self.scale) for v in box)
        self.draw.ellipse(box, fill=self.color(color, alpha) if fill else None,
                          outline=None if fill else self.color(color, alpha), width=max(1, round(width * self.scale)))

    def arc(self, box, start, end, color=None, width=2, alpha=220):
        self.draw.arc(tuple(round(v * self.scale) for v in box), start, end,
                      fill=self.color(color, alpha), width=max(1, round(width * self.scale)))

    def curve(self, pts, color=None, width=2, alpha=220):
        p0, p1, p2, p3 = pts
        self.line([((1-t)**3*p0[0]+3*(1-t)**2*t*p1[0]+3*(1-t)*t*t*p2[0]+t**3*p3[0],
                    (1-t)**3*p0[1]+3*(1-t)**2*t*p1[1]+3*(1-t)*t*t*p2[1]+t**3*p3[1])
                   for t in [i/32 for i in range(33)]], color, width, alpha)

    def finish(self):
        return self.image.resize(self.size, Image.Resampling.LANCZOS)


def polar(x, y, radius, angle):
    return x + radius * math.cos(angle), y + radius * math.sin(angle)


def star(c, x, y, r, points=4, color=None, alpha=230, turn=-math.pi/2):
    c.polygon([polar(x,y,r if i%2==0 else r*.3,turn+i*math.pi/points)
               for i in range(points*2)], color, width=1.7, alpha=alpha)


def leaf(c, x, y, r, angle, color=None, alpha=230):
    tip=polar(x,y,r,angle)
    c.curve([(x,y),polar(x,y,r*.72,angle-.8),polar(*tip,r*.32,angle-2.3),tip],color,1.8,alpha)
    c.curve([tip,polar(*tip,r*.32,angle+2.3),polar(x,y,r*.72,angle+.8),(x,y)],color,1.8,alpha)
    c.line([(x,y),tip],color,1,alpha*.65)


def gear(c, x, y, r, color=None, alpha=230, teeth=10):
    pts=[]
    for i in range(teeth*4):
        rr=r if i%4 in (1,2) else r*.78
        pts.append(polar(x,y,rr,i*math.tau/(teeth*4)))
    c.polygon(pts,color,2,alpha)
    c.ellipse((x-r*.42,y-r*.42,x+r*.42,y+r*.42),color,1.8,alpha)


def diamond(c,x,y,r,color=None,alpha=230):
    c.polygon([(x,y-r),(x+r*.6,y),(x,y+r),(x-r*.6,y)],color,2,alpha)


def sprig(c,x,y,r,angle,color=None,alpha=225):
    end=polar(x,y,r,angle)
    c.line([(x,y),end],color,1.7,alpha)
    for f in (.35,.62,.87):
        p=polar(x,y,r*f,angle)
        leaf(c,*p,r*.29,angle-1,color,alpha)
        leaf(c,*p,r*.29,angle+1,color,alpha)


def icon(c,theme,x,y,r,alpha=235):
    """Twelve hand-authored silhouettes, shared only within their own theme."""
    a,b=c.a,c.b
    if theme=="botanical":
        c.curve([(x,y+r*.82),(x-r*.1,y+r*.18),(x+r*.13,y-r*.25),(x,y-r*.65)],a,2.6,alpha)
        leaf(c,x,y+r*.15,r*.9,-2.6,a,alpha)
        leaf(c,x,y-r*.18,r*.77,-.58,b,alpha)
        c.ellipse((x-r*.13,y-r*.88,x+r*.13,y-r*.62),b,1.8,alpha)
    elif theme=="arcane":
        diamond(c,x,y,r*.8,a,alpha)
        c.line([(x,y-r*.8),(x-r*.23,y),(x,y+r*.8),(x+r*.23,y),(x,y-r*.8)],a,1.3,alpha*.7)
        c.ellipse((x-r*.95,y-r*.36,x+r*.95,y+r*.36),b,1.8,alpha)
        for angle in (0,math.pi): star(c,*polar(x,y,r*1.08,angle),r*.15,4,b,alpha)
    elif theme=="machinery":
        gear(c,x,y,r*.85,a,alpha,12)
        c.polygon([(x-r*.2,y-r*.12),(x,y-r*.25),(x+r*.2,y-r*.12),(x+r*.2,y+r*.13),(x,y+r*.25),(x-r*.2,y+r*.13)],b,1.8,alpha)
        for angle in (0,math.pi/2,math.pi,3*math.pi/2):
            p=polar(x,y,r*.61,angle);c.ellipse((p[0]-r*.045,p[1]-r*.045,p[0]+r*.045,p[1]+r*.045),b,1,alpha,True)
    elif theme=="power":
        c.polygon([(x-r*.48,y-r*.6),(x-r*.28,y-r*.82),(x+r*.3,y-r*.82),(x+r*.48,y-r*.6),
                   (x+r*.48,y+r*.65),(x+r*.25,y+r*.82),(x-r*.25,y+r*.82),(x-r*.48,y+r*.65)],a,2.3,alpha)
        c.polygon([(x+r*.15,y-r*.55),(x-r*.28,y+r*.05),(x+.08*r,y+.02*r),(x-r*.1,y+r*.56),
                   (x+r*.32,y-r*.13),(x-r*.03,y-r*.08)],b,1.6,alpha)
        for sy in (-.4,0,.4):
            c.line([(x-r*.72,y+r*sy),(x-r*.53,y+r*sy)],a,2,alpha)
            c.line([(x+r*.53,y+r*sy),(x+r*.72,y+r*sy)],a,2,alpha)
    elif theme=="space":
        c.ellipse((x-r*.57,y-r*.57,x+r*.57,y+r*.57),a,2.3,alpha)
        pts=[]
        for i in range(65):
            t=i*math.tau/64;dx=r*math.cos(t);dy=r*.24*math.sin(t)
            pts.append((x+dx*.91-dy*.41,y+dx*.41+dy*.91))
        c.line(pts,b,2,alpha)
        c.arc((x-r*.4,y-r*.37,x+r*.32,y+r*.3),195,295,a,1.3,alpha*.6)
        star(c,x+r*.79,y-r*.65,r*.12,4,b,alpha)
    elif theme=="exploration":
        c.ellipse((x-r*.67,y-r*.67,x+r*.67,y+r*.67),b,1.4,alpha)
        for i in range(8):
            ang=-math.pi/2+i*math.pi/4;rr=r if i%2==0 else r*.62
            c.polygon([polar(x,y,rr,ang),polar(x,y,r*.18,ang+.9),polar(x,y,r*.3,ang+math.pi)],a if i%2==0 else b,1.6,alpha)
        c.ellipse((x-r*.11,y-r*.11,x+r*.11,y+r*.11),a,1.5,alpha)
    elif theme=="farming":
        for offset,ang in [(-.3,-1.85),(0,-math.pi/2),(.3,-1.3)]:
            sprig(c,x+offset*r,y+r*.65,r*1.35,ang,a if offset==0 else b,alpha)
        c.line([(x-r*.43,y+r*.48),(x+r*.4,y+r*.28)],a,3,alpha)
    elif theme=="ocean":
        c.curve([(x-r*.85,y+r*.37),(x-r*.85,y-r*.86),(x+r*.85,y-r*.86),(x+r*.85,y+r*.37)],a,2.3,alpha)
        for t in range(7):
            end=polar(x,y-r*.12,r*.72,math.pi+math.pi*t/6)
            c.curve([(x,y+r*.65),(x+(.5-t/6)*r*.5,y),(end[0],end[1]+r*.15),end],a,1.6,alpha)
        c.curve([(x-r*.85,y+r*.37),(x-r*.4,y+r*.82),(x+r*.4,y+r*.82),(x+r*.85,y+r*.37)],a,2,alpha)
        c.ellipse((x-r*.2,y+r*.13,x+r*.2,y+r*.53),b,2,alpha)
    elif theme=="nether":
        c.polygon([(x-r*.7,y+r*.35),(x-r*.43,y+r*.85),(x+r*.43,y+r*.85),(x+r*.7,y+r*.35)],b,2.3,alpha)
        c.curve([(x-r*.48,y+r*.28),(x-r*.77,y-r*.14),(x-r*.1,y-r*.25),(x-r*.2,y-r*.89)],a,2.6,alpha)
        c.curve([(x-r*.2,y-r*.89),(x+r*.52,y-r*.43),(x+r*.59,y-r*.03),(x+r*.42,y+r*.29)],a,2.6,alpha)
        c.curve([(x-r*.15,y+r*.2),(x-r*.36,y-r*.05),(x+r*.28,y-r*.04),(x+r*.13,y-r*.43)],b,1.8,alpha)
        c.line([(x-r*.72,y+r*.35),(x+r*.72,y+r*.35)],a,2.2,alpha)
    elif theme=="end":
        c.polygon([(x-r*.05,y-r*.91),(x+r*.38,y-r*.35),(x+r*.17,y+r*.64),(x-r*.35,y+r*.28),(x-r*.45,y-r*.32)],a,2.3,alpha)
        c.line([(x-r*.05,y-r*.91),(x-r*.05,y-r*.22),(x+r*.17,y+r*.64),(x-r*.05,y-r*.22),(x-r*.45,y-r*.32)],a,1.5,alpha)
        for px,py,rr in [(-.73,-.37,.22),(.7,.1,.25),(-.57,.63,.15)]: diamond(c,x+px*r,y+py*r,rr*r,b,alpha)
        c.arc((x-r*.83,y-r*.62,x+r*.86,y+r*.81),25,118,b,1.6,alpha)
    elif theme=="storage":
        c.polygon([(x-r*.69,y-r*.6),(x,y-r*.94),(x+r*.69,y-r*.6),(x+r*.69,y+r*.51),(x,y+r*.89),(x-r*.69,y+r*.51)],a,2.2,alpha)
        c.line([(x-r*.69,y-r*.6),(x,y-r*.2),(x+r*.69,y-r*.6)],b,1.8,alpha)
        c.line([(x,y-r*.2),(x,y+r*.89)],a,1.6,alpha)
        for off in (.15,.47):
            c.line([(x-r*.5,y-r*.08+off*r),(x-r*.2,y+r*.08+off*r)],a,2,alpha)
            c.line([(x+r*.2,y+r*.08+off*r),(x+r*.5,y-r*.08+off*r)],b,2,alpha)
    else:  # restoration: a seedling rising above water and a partial sunrise
        c.arc((x-r*.55,y-r*.7,x+r*.55,y+r*.4),185,355,b,2,alpha)
        for angle in (-2.7,-2.2,-1.57,-.95,-.45):
            c.line([polar(x,y-r*.15,r*.73,angle),polar(x,y-r*.15,r*.94,angle)],b,1.7,alpha)
        c.line([(x,y+r*.45),(x,y-r*.2)],a,2.2,alpha)
        leaf(c,x,y+r*.02,r*.52,-2.3,a,alpha)
        leaf(c,x,y-r*.12,r*.47,-.8,a,alpha)
        for off in (.45,.68):
            c.curve([(x-r*.73,y+r*off),(x-r*.3,y+r*(off-.22)),(x+r*.3,y+r*(off+.22)),(x+r*.73,y+r*off)],a,1.7,alpha)


def frame(c,theme):
    """Distinct corner constructions leave the entire middle map unpainted."""
    for cx,cy,sx,sy in [(38,38,1,1),(474,38,-1,1),(38,474,1,-1),(474,474,-1,-1)]:
        p=lambda x,y:(cx+sx*x,cy+sy*y)
        if theme=="botanical":
            c.curve([p(0,115),p(-8,30),p(30,-8),p(115,0)],width=2.7)
            for x,y,a in [(0,64,0),(18,22,.6),(64,0,1.57)]:
                leaf(c,*p(x,y),22,math.atan2(sy*math.sin(a),sx*math.cos(a)),c.b)
        elif theme=="arcane":
            c.arc((cx-26,cy-26,cx+26,cy+26),0,360,width=2)
            c.line([p(0,113),p(0,40),p(40,0),p(113,0)],c.b,1.7)
            diamond(c,*p(0,0),16)
        elif theme=="machinery":
            c.line([p(0,120),p(0,0),p(120,0)],width=4)
            c.line([p(14,105),p(14,14),p(105,14)],c.b,2)
            for x,y in [(0,0),(0,80),(80,0)]:c.ellipse((p(x,y)[0]-5,p(x,y)[1]-5,p(x,y)[0]+5,p(x,y)[1]+5),c.b,2)
        elif theme=="power":
            c.line([p(0,112),p(0,52),p(26,26),p(78,26),p(103,0)],width=3)
            c.line([p(22,110),p(22,64),p(52,34),p(115,34)],c.b,1.6)
            for x,y in [(0,112),(115,34),(103,0)]:diamond(c,*p(x,y),5,c.b)
        elif theme=="space":
            pts=[p(0,105),p(19,60),p(5,22),p(54,12),p(111,0)]
            c.line(pts,width=1.5,alpha=130)
            for i,pt in enumerate(pts):star(c,*pt,7 if i%2==0 else 4,color=c.b)
            c.arc((cx-29,cy-29,cx+29,cy+29),15,275,width=1.5)
        elif theme=="exploration":
            c.line([p(0,110),p(0,0),p(110,0)],c.b,1.8)
            for k in range(20,110,15):
                c.line([p(k,0),p(k,6 if k%2 else 10)],width=1.6)
                c.line([p(0,k),p(7,k)],width=1.6)
            c.polygon([p(20,43),p(39,14),p(61,47)],width=1.8)
        elif theme=="farming":
            sprig(c,*p(8,108),100,math.atan2(-sy,0),alpha=205)
            sprig(c,*p(108,8),100,math.atan2(0,-sx),c.b,205)
            c.line([p(18,18),p(32,32)],width=3)
        elif theme=="ocean":
            c.curve([p(0,113),p(-13,70),p(66,25),p(15,15)],width=2.8)
            c.curve([p(15,15),p(25,66),p(70,-13),p(113,0)],c.b,2.2)
            for x,y,r in [(5,7,7),(43,43,4),(65,17,3)]:
                q=p(x,y);c.ellipse((q[0]-r,q[1]-r,q[0]+r,q[1]+r),width=1.5)
        elif theme=="nether":
            c.line([p(0,116),p(13,78),p(3,57),p(18,21),p(58,4),p(82,15),p(116,0)],width=3)
            c.line([p(18,105),p(24,69),p(41,44),p(69,24),p(104,18)],c.b,1.5)
            c.polygon([p(18,21),p(17,0),p(0,12)],c.b,1.4)
        elif theme=="end":
            for x,y,w,h in [(0,62,10,43),(0,0,36,12),(61,0,44,8),(20,27,10,10)]:
                c.polygon([p(x,y),p(x+w,y-4),p(x+w,y+h),p(x,y+h+4)],width=1.8)
            diamond(c,*p(45,45),14,c.b)
        elif theme=="storage":
            for k in (0,1,2):
                x=k*30;y=0;c.polygon([p(x,y),p(x+22,y),p(x+22,y+22),p(x,y+22)],width=1.6)
                if k:c.polygon([p(0,k*30),p(22,k*30),p(22,k*30+22),p(0,k*30+22)],c.b,1.6)
            c.line([p(34,70),p(34,34),p(70,34)],c.b,1.6)
        else:
            c.curve([p(0,110),p(0,32),p(32,0),p(110,0)],width=2.2)
            c.curve([p(13,100),p(16,30),p(30,16),p(100,13)],c.b,1.5)
            leaf(c,*p(24,24),31,math.atan2(sy,sx),alpha=220)
            for x,y in [(50,4),(4,50)]:diamond(c,*p(x,y),5,c.b)


def ornament(c,theme):
    if theme in ("botanical","farming","restoration"):
        for sx in (-1,1):
            c.curve([(256,228),(256+sx*20,156),(256+sx*120,138),(256+sx*197,41)],width=2.6)
            for i in range(4):
                x=256+sx*(40+i*38);y=175-i*30
                angle=(-2.45 if sx<0 else -.7)
                if theme=="farming":sprig(c,x,y,55,angle,c.b)
                else:
                    leaf(c,x,y,43,angle,c.b if i%2 else c.a)
                    if theme=="restoration":
                        c.line([(x,y),(x+sx*16,y+19),(x+sx*31,y+22)],c.b,1.5)
        if theme=="restoration":
            for i in range(3):c.arc((212-i*14,20-i*7,300+i*14,108+i*7),190,350,c.b,1.5,170)
        elif theme=="botanical":leaf(c,256,202,91,-math.pi/2)
        else:sprig(c,256,222,157,-math.pi/2)
    elif theme=="ocean":
        for sx in (-1,1):
            stem=[(256,225),(256+sx*26,183),(256+sx*72,152),(256+sx*112,86),(256+sx*174,39)]
            c.line(stem,width=4)
            for i,(x,y) in enumerate(stem[1:]):
                rise=.46 if i==3 else 1
                c.line([(x,y),(x+sx*34,y-9*rise),(x+sx*50,y-39*rise)],c.b,2.5)
                c.line([(x,y),(x-sx*9,y-30*rise),(x-sx*4,y-53*rise)],width=2.3)
                cy=y-53*rise;c.ellipse((x-4,cy-4,x+4,cy+4),c.b,1.3)
    elif theme in ("power","storage","machinery"):
        root=(256,215)
        for i,(x,y) in enumerate([(47,66),(157,30),(355,30),(465,66)]):
            if theme=="power":
                pts=[root,(256,156),(x,156),(x, y)]
                c.line(pts,c.a if i%2 else c.b,2.3)
                star(c,x,y,12,4,c.b)
            elif theme=="storage":
                c.line([root,(256,174),(x,174),(x,y+20)],c.b,2)
                c.polygon([(x-20,y-17),(x+20,y-17),(x+20,y+17),(x-20,y+17)],width=2)
                c.line([(x-11,y),(x+11,y)],c.b,2)
            else:
                c.line([root,(256+(x-256)*.42,165),(x,y+20)],width=5)
                c.line([root,(256+(x-256)*.42,165),(x,y+20)],c.b,1.4)
                gear(c,x,y,25,c.b,teeth=8)
        if theme=="machinery":gear(c,*root,25)
        elif theme=="storage":diamond(c,*root,22,c.b)
        else:c.ellipse((244,201,268,229),width=2.5)
    elif theme=="space":
        pts=[(36,100),(105,59),(167,99),(257,48),(330,89),(395,39),(479,99),(362,177),(272,213),(174,163),(78,199)]
        c.line(pts[:7],width=1.7,alpha=130);c.line([pts[2],pts[9],pts[8],pts[7],pts[4]],c.b,1.5,150)
        for i,p in enumerate(pts):star(c,*p,11 if i%3==0 else 5,4,c.a if i%2 else c.b)
    elif theme=="exploration":
        c.line([(254,231),(254,158),(174,107),(68,118),(34,47)],width=2.5)
        c.line([(254,158),(342,136),(394,61),(472,51)],c.b,2.5)
        for x,y in [(66,115),(174,107),(342,136),(394,61)]:c.ellipse((x-6,y-6,x+6,y+6),width=1.5)
        for x,y in [(73,34),(247,56),(446,166)]:
            c.line([(x-30,y+24),(x-6,y-16),(x+6,y+3),(x+15,y-7),(x+37,y+24)],c.b,1.8)
    elif theme=="arcane":
        c.line([(256,231),(256,145),(132,97),(62,38)],width=1.6)
        c.line([(256,145),(380,97),(450,38)],width=1.6)
        for x,y,r in [(256,145,29),(132,97,22),(380,97,22),(62,38,14),(450,38,14)]:
            c.ellipse((x-r,y-r,x+r,y+r),c.b,1.5);diamond(c,x,y,r*.6)
        c.arc((154,18,358,199),195,345,width=1.3,alpha=145)
        star(c,256,49,21,6)
    elif theme=="nether":
        paths=[[(256,238),(246,170),(172,137),(131,71),(46,26)],[(246,170),(339,151),(384,65),(463,41)],
               [(172,137),(73,151),(37,108)],[(339,151),(430,188),(476,141)],[(246,170),(285,84),(266,28)]]
        for i,path in enumerate(paths):
            c.line(path,c.b,7 if i<2 else 4,115);c.line(path,width=2)
        for x,y in [(95,42),(394,107),(258,207)]:c.polygon([(x-10,y),(x,y-12),(x+7,y+8)],c.b,1.4)
    else:
        for i in range(8):
            x=47+i*60;y=125+int(math.sin(i*1.4)*60)
            diamond(c,x,y,30 if i%3==0 else 19,c.a if i%2 else c.b)
            if i<7:c.line([(x+18,y),(x+31,y-14),(x+42,y-14)],c.b,1.2,120)


def divider(c,theme):
    if theme=="botanical":
        c.curve([(25,72),(147,34),(357,107),(487,54)],width=2.4)
        for i in range(11):leaf(c,46+i*41,66+math.sin(i*.7)*9,22,(-.9 if i%2 else -2.7),c.b)
    elif theme=="arcane":
        c.line([(22,64),(195,64)],width=1.5);c.line([(317,64),(490,64)],width=1.5)
        for x,r in [(91,14),(151,8),(256,31),(361,8),(421,14)]:
            c.ellipse((x-r,64-r,x+r,64+r),c.b,1.4);diamond(c,x,64,r*.7)
    elif theme=="machinery":
        pts=[(24,83)]
        for i in range(23):
            x=24+i*20;pts.extend([(x,83),(x,61),(x+10,61),(x+10,83)])
        c.line(pts,width=2.5);gear(c,256,50,27,c.b,teeth=10)
    elif theme=="power":
        c.line([(21,67),(125,67),(151,42),(191,42),(212,83),(236,23),(260,103),(285,43),(309,67),(491,67)],width=2.3)
        for x in (21,491):c.ellipse((x-5,62,x+5,72),c.b,1.5)
    elif theme=="space":
        for i in range(6):
            y=31+i*12;c.curve([(24+i*7,y),(191,y-15),(297,y+9),(419,66)],c.b,1.1,75+i*20)
        star(c,430,65,27,4);star(c,476,30,7,4,c.b)
    elif theme=="exploration":
        c.line([(20,94),(76,38),(108,76),(157,26),(207,88),(263,61),(320,97),(382,35),(438,82),(490,51)],width=2.2)
        c.line([(27,105),(485,105)],c.b,1.2,140)
        for x,y in [(76,38),(157,26),(382,35)]:c.line([(x-9,y+9),(x,y+15),(x+8,y+8)],c.b,1.5)
    elif theme=="farming":
        for i in range(4):c.curve([(22,97-i*12),(135,21+i*6),(371,130-i*7),(490,44-i*6)],c.b,1.7,140+i*20)
        sprig(c,256,96,67,-math.pi/2)
    elif theme=="ocean":
        for y in (45,68,91):
            pts=[(x,y+10*math.sin((x-20)*math.tau/94)) for x in range(20,493,3)]
            c.line(pts,c.a if y==68 else c.b,2 if y==68 else 1.4,220 if y==68 else 145)
    elif theme=="nether":
        for i in range(16):
            x=23+i*30;h=17+(i*13%31)
            c.polygon([(x,88),(x+8,88-h),(x+18,83),(x+24,99)],c.a if i%2 else c.b,1.8)
        c.line([(24,104),(488,104)],c.b,1.2,110)
    elif theme=="end":
        for i in range(12):
            x=28+i*40;dy=(i%3-1)*11
            c.line([(x,60+dy),(x+24,60+dy)],width=2)
            if i%3==0:diamond(c,x+12,60+dy,18,c.b)
            else:c.line([(x+24,60+dy),(x+29,55+dy)],c.b,1.5)
    elif theme=="storage":
        for y in (42,86):c.line([(20,y),(492,y)],c.b,1.5)
        for i in range(9):
            x=40+i*54;c.polygon([(x-12,54),(x+12,54),(x+12,74),(x-12,74)],width=1.7)
            c.line([(x,42),(x,54)],c.b,1.2);c.line([(x,74),(x,86)],c.b,1.2)
    else:
        c.curve([(20,81),(128,111),(335,16),(492,80)],width=2.4)
        c.curve([(20,94),(156,111),(335,41),(492,94)],c.b,1.6,160)
        for x,y in [(95,84),(205,67),(341,60),(432,71)]:leaf(c,x,y,28,-1.25,c.b)


def panel(c,theme):
    """A patterned perimeter, deliberately empty through the middle 240px."""
    index=list(THEMES).index(theme)
    for yi in range(8):
        for xi in range(8):
            if 1 < xi < 6 and 1 < yi < 6:continue
            x,y=32+xi*64,32+yi*64
            alpha=105 if (xi+yi)%2 else 155
            if theme=="botanical":
                leaf(c,x-9,y+13,29,-.9,c.a,alpha);leaf(c,x+9,y+13,25,-2.2,c.b,alpha)
            elif theme=="arcane":
                c.ellipse((x-21,y-21,x+21,y+21),width=1,alpha=alpha*.6);star(c,x,y,15,6,c.b,alpha)
            elif theme=="machinery":
                c.polygon([(x-19,y-19),(x+19,y-19),(x+19,y+19),(x-19,y+19)],c.b,1.3,alpha)
                for dx in (-11,11):c.ellipse((x+dx-2,y-2,x+dx+2,y+2),width=1,alpha=alpha,fill=True)
            elif theme=="power":
                c.line([(x-24,y+19),(x-10,y+19),(x+12,y-19),(x+24,y-19)],width=1.3,alpha=alpha)
                c.ellipse((x-14,y+15,x-6,y+23),c.b,1,alpha)
            elif theme=="space":
                star(c,x+((xi*11)%19)-9,y-5,8 if (xi+yi)%3 else 15,4,c.a,alpha)
                c.ellipse((x-17,y+15,x-14,y+18),c.b,1,alpha,True)
            elif theme=="exploration":
                for k in range(3):c.arc((x-24+k*6,y-18+k*5,x+24-k*4,y+24-k*5),25,298,c.a if k%2 else c.b,1.1,alpha)
            elif theme=="farming":
                c.line([(x-22,y-18),(x+21,y+19)],c.b,1.2,alpha)
                for k in (-11,0,11):c.line([(x-20,y+k),(x,y+k+14),(x+20,y+k)],width=1.2,alpha=alpha)
            elif theme=="ocean":
                for k in range(3):c.arc((x-24,y-16+k*9,x+24,y+23+k*9),200,340,c.a if k%2 else c.b,1.2,alpha)
            elif theme=="nether":
                c.polygon([(x-20,y-12),(x-7,y-22),(x+18,y-14),(x+21,y+8),(x+3,y+23),(x-16,y+14)],c.b,1.2,alpha)
                c.line([(x-7,y-22),(x-2,y-2),(x+21,y+8)],width=1.1,alpha=alpha*.7)
            elif theme=="end":
                c.polygon([(x-18,y-12),(x+12,y-20),(x+21,y+15),(x-12,y+22)],width=1.2,alpha=alpha)
                c.line([(x-11,y-7),(x+8,y-12)],c.b,1.3,alpha)
            elif theme=="storage":
                for dy in (-13,0,13):c.polygon([(x-21,y+dy-4),(x+21,y+dy-4),(x+21,y+dy+4),(x-21,y+dy+4)],width=1,alpha=alpha)
            else:
                for k in range(3):c.arc((x-22+k*6,y-22+k*6,x+22-k*6,y+22-k*6),15+index,310,c.a if k%2 else c.b,1.1,alpha)
                leaf(c,x,y+7,17,-1.2,c.b,alpha)


def render(theme,kind):
    c=Canvas(SIZES[kind],theme)
    if kind=="frame":frame(c,theme)
    elif kind=="ornament":ornament(c,theme)
    elif kind=="emblem":icon(c,theme,128,128,90)
    elif kind=="divider":divider(c,theme)
    else:panel(c,theme)
    return c.finish()


def contact_sheet(entries,dest):
    cell_w,cell_h=214,208
    sheet=Image.new('RGB',(cell_w*5,cell_h*12+54),'#121b29')
    d=ImageDraw.Draw(sheet)
    try:
        font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',13)
        big=ImageFont.truetype('C:/Windows/Fonts/seguisb.ttf',20)
    except OSError:
        font=ImageFont.load_default();big=font
    d.text((18,14),'AUTOQUESTGEN / 60 ORIGINAL MAP ORNAMENTS',font=big,fill='#e5eddc')
    for n,entry in enumerate(entries):
        x=(n%5)*cell_w;y=(n//5)*cell_h+54
        d.rounded_rectangle((x+6,y+6,x+cell_w-6,y+cell_h-6),radius=9,fill='#1b293a',outline='#304255')
        art=Image.open(dest/'assets/autoquestgen'/entry['texture'].split(':',1)[1])
        art.thumbnail((184,155),Image.Resampling.LANCZOS)
        sheet.paste(art,(x+(cell_w-art.width)//2,y+14+(155-art.height)//2),art)
        d.text((x+14,y+171),entry['theme'].upper()+' / '+entry['kind'],font=font,fill='#adbdcd')
        d.text((x+14,y+187),entry['title'],font=font,fill='#e5eddc')
    sheet.save(dest/'native_contact_sheet.png')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'decor_assets')
    args=parser.parse_args(argv)
    entries=[]
    for theme,data in THEMES.items():
        for index,kind in enumerate(KINDS):
            identity=theme+'_'+kind
            texture=f'autoquestgen:textures/{theme}/{identity}.png'
            path=args.output/'assets/autoquestgen'/texture.split(':',1)[1]
            path.parent.mkdir(parents=True,exist_ok=True)
            art=render(theme,kind);art.save(path,optimize=True)
            entries.append(dict(id=identity,theme=theme,kind=kind,texture=texture,title=NAMES[theme][index],
                keywords=data[3]+[kind],width=art.width,height=art.height,
                alpha={'frame':205,'ornament':205,'emblem':225,'divider':205,'panel':160}[kind],
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'native_manifest.json').write_text(json.dumps({'schema':1,'assets':entries,
        'provenance':'Original native geometry drawn by tools/render_decor_assets.py; no external artwork or text in the PNGs.'},indent=2)+'\n',encoding='utf-8')
    contact_sheet(entries,args.output)
    print(f'Rendered {len(entries)} original transparent assets to {args.output}')


if __name__=='__main__':
    main()
