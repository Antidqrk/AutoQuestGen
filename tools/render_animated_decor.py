"""Original animated UI landmarks, drawn from editable native geometry.

No source illustrations are read, modified, traced, or recolored. Pillow is the
raster drawing surface for these new vector-like ornaments. Each scene is a
periodic function; frame 32 is identical to frame 0 but is not stored twice.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_decor_assets import Canvas, THEMES, leaf, polar, rgba

ROOT = Path(__file__).resolve().parents[1]
FRAMES = 32
FRAME_SIZE = 256
DURATION_MS = 100
TAU = math.tau
NAMES = {
    'botanical': ('mana_bloom', 'The Mana Bloom', 'Opening petals, moving mana motes, and a living stem'),
    'arcane': ('celestial_sigil', 'Celestial Sigil', 'Counter-rotating ritual rings and orbiting sigils'),
    'machinery': ('kinetic_heart', 'Kinetic Heart', 'Meshing gears drive a connected crank and reciprocating piston'),
    'power': ('storm_core', 'Storm Core', 'Orbiting plasma, traveling energy, and shifting electric arcs'),
    'space': ('event_horizon', 'Event Horizon', 'A dark central void, luminous accretion disk, and orbiting particles'),
    'exploration': ('wayfinder', 'The Wayfinder', 'A revolving compass and a traveler following a closed route'),
    'farming': ('pollinator_garden', 'Pollinator Garden', 'A flying bee, flapping wings, swaying wheat, and opening flowers'),
    'ocean': ('tidal_heart', 'Tidal Heart', 'Flowing waves, swaying coral branches, and rising bubbles'),
    'nether': ('ember_forge', 'Ember Forge', 'Curling flames, rising embers, and a circulating lava pool'),
    'end': ('void_oracle', 'Void Oracle', 'A turning faceted crystal with orbiting shards and a rift ring'),
    'storage': ('memory_nexus', 'Memory Nexus', 'Traveling packets flow between a central archive and storage nodes'),
    'restoration': ('renewal_cycle', 'Cycle of Renewal', 'Rain, new leaves, opening blossoms, and a returning sun'),
}


class MotionCanvas(Canvas):
    def __init__(self, theme, size=FRAME_SIZE):
        super().__init__((256, 256), theme, scale=3)
        self.output_size = size

    def disk(self, x, y, r, color, alpha=255):
        self.ellipse((x-r, y-r, x+r, y+r), color, alpha=alpha, fill=True)

    def path(self, points, color, width=2, alpha=255):
        self.line(points, color, width, alpha)

    def ring(self, x, y, rx, ry=None, color=None, width=2, alpha=235, turn=0):
        ry = rx if ry is None else ry
        pts = [rotate(x+rx*math.cos(i*TAU/100), y+ry*math.sin(i*TAU/100), x, y, turn)
               for i in range(101)]
        self.line(pts, color, width, alpha)

    def finish(self):
        base = self.image
        # Controlled light bloom is generated from this frame's own geometry.
        glow = base.filter(ImageFilter.GaussianBlur(3.4 * self.scale))
        glow.putalpha(glow.getchannel('A').point(lambda a: round(a*.43)))
        result = Image.alpha_composite(glow, base)
        return result.resize((self.output_size, self.output_size), Image.Resampling.LANCZOS)


def rotate(x, y, cx, cy, angle):
    dx,dy=x-cx,y-cy
    return cx+dx*math.cos(angle)-dy*math.sin(angle),cy+dx*math.sin(angle)+dy*math.cos(angle)


def blend(first, second, t):
    t=max(0,min(1,t));a=rgba(first);b=rgba(second)
    return '#'+''.join(f'{round(a[i]*(1-t)+b[i]*t):02x}' for i in range(3))


def polygon(c, x, y, r, sides, angle, color, fill=False, alpha=240, width=1.5):
    c.polygon([polar(x,y,r,angle+i*TAU/sides) for i in range(sides)],color,width,alpha,fill)


def spark(c,x,y,r,color,phase=0,alpha=245):
    c.polygon([polar(x,y,r if i%2==0 else r*.23,phase+i*TAU/8) for i in range(8)],color,1,alpha,True)


def filled_leaf(c,x,y,length,angle,color,alpha=245):
    # Pointed asymmetric leaf, with a clearly visible vein.
    points=[]
    for i in range(25):
        t=i/24;along=length*t;width=math.sin(math.pi*t)*length*.24
        points.append(rotate(x+along,y-width,x,y,angle))
    for i in range(24,-1,-1):
        t=i/24;along=length*t;width=math.sin(math.pi*t)*length*.19
        points.append(rotate(x+along,y+width,x,y,angle))
    c.polygon(points,color,alpha=alpha,fill=True)
    c.line([(x,y),polar(x,y,length*.9,angle)],blend(color,'#ffffff',.5),.9,alpha*.75)


def petal(c,x,y,length,width,angle,color,alpha=250):
    points=[]
    for i in range(37):
        t=i*TAU/36
        px=x+length*(.45+.55*math.cos(t));py=y+width*math.sin(t)*(.8+.2*math.cos(t))
        points.append(rotate(px,py,x,y,angle))
    c.polygon(points,color,alpha=alpha,fill=True)


def dotted_orbit(c,x,y,rx,ry,phase,color,count=14,turn=0):
    for i in range(count):
        angle=i*TAU/count+phase
        px,py=rotate(x+rx*math.cos(angle),y+ry*math.sin(angle),x,y,turn)
        c.disk(px,py,1.2+(i%3)*.35,color,120+(i%4)*30)


def gear(c,x,y,r,teeth,angle,body,rim):
    pts=[]
    for i in range(teeth*4):
        radius=r if i%4 in (1,2) else r*.83
        pts.append(polar(x,y,radius,angle+i*TAU/(teeth*4)))
    c.polygon(pts,body,alpha=255,fill=True);c.polygon(pts,rim,1.6,255)
    c.disk(x,y,r*.63,'#142839');c.ring(x,y,r*.63,color=rim,width=1.3)
    for i in range(4):
        p=polar(x,y,r*.4,angle+i*TAU/4)
        c.disk(*p,r*.095,rim)
    c.disk(x,y,r*.18,body);c.ring(x,y,r*.18,color='#fff0c5',width=1.2)


def botanical(c,p):
    t=TAU*p; sway=5*math.sin(t);cx=128+sway;cy=107
    c.curve([(128,215),(111,175),(145,142),(cx,cy)],'#65bb8b',4,250)
    filled_leaf(c,129,185,52,-2.7+.1*math.sin(t),'#439f85')
    filled_leaf(c,130,169,51,-.48+.1*math.sin(t+.8),'#9ae679')
    c.ring(128,215,37,9,'#5bfbd5',1.2,170)
    bloom=.88+.12*math.cos(t)
    for layer in (1,0):
        for i in range(7):
            angle=i*TAU/7+layer*.4+.045*math.sin(t)
            color=blend('#3c8fce' if layer else '#83f3ec','#e0ffff',(i%3)/4)
            petal(c,cx,cy,(48 if layer else 34)*bloom,(13 if layer else 12)*bloom,angle,color,225)
    c.disk(cx,cy,13,'#eddf92');c.disk(cx-3,cy-3,5,'#fffbd7')
    for i in range(7):
        a=t+i*TAU/7;x=cx+71*math.cos(a);y=cy+50*math.sin(a)
        spark(c,x,y,3.8,'#a7ffdf',a,210)


def arcane(c,p):
    t=TAU*p
    for radius,color,phase in [(88,'#9871e9',t*.5),(72,'#e7c56c',-t),(49,'#b5a1ff',t)]:
        c.ring(128,128,radius,color=color,width=1.5)
        count=12 if radius>80 else 6
        for i in range(count):
            a=i*TAU/count+phase;x,y=polar(128,128,radius,a)
            polygon(c,x,y,5 if radius>80 else 8,4,a,color,False)
            if radius>80:
                c.line([polar(128,128,94,a-.02),polar(128,128,99,a-.02)],color,1.5)
    polygon(c,128,128,59,3,t,'#a387ff');polygon(c,128,128,59,3,-t+math.pi,'#f4d888')
    for i in range(6):
        x,y=polar(128,128,29,t+i*TAU/6);c.line([(128,128),(x,y)],'#9277d2',1,170)
    c.disk(128,128,16+2*math.sin(t),'#644da4');c.ring(128,128,15,color='#e6cdff',width=2)
    spark(c,128,128,19,'#fff6c8',-t)


def machinery(c,p):
    t=TAU*p
    # Twelve/eight teeth imply a 3:2 angular ratio. The smaller gear's
    # quarter-turn-symmetric hub also closes after its one-and-a-half turns.
    gear(c,94,112,48,12,t,'#b87338','#ffe1a0')
    gear(c,166,112,32,8,-t*1.5+math.pi/8,'#467b8c','#a9e4e8')
    # A slider-crank with a rigid connecting rod and a visible track.
    crank=polar(94,112,23,t);rod=80
    piston_y=crank[1]+math.sqrt(rod*rod-(94-crank[0])**2)
    c.polygon([(72,168),(72,226),(116,226),(116,168)],'#263a49',1,255,True)
    for x in (74,114):c.line([(x,165),(x,228)],'#97a9ac',3)
    c.line([crank,(94,piston_y)],'#292d36',8);c.line([crank,(94,piston_y)],'#e8cf94',3.5)
    c.polygon([(77,piston_y-8),(111,piston_y-8),(111,piston_y+8),(77,piston_y+8)],'#6c8794',1,255,True)
    c.line([(78,piston_y-7),(110,piston_y-7)],'#cbe5de',2)
    c.disk(*crank,4,'#f4d08b');c.disk(94,piston_y,3,'#dbeced')
    for i in range(4):
        x=157+i*13;c.line([(x,177),(x+6,177),(x+6,218)],'#436573',2)
        c.disk(x+6,188+17*math.sin(t-i*.7),2,'#ffd276')


def power(c,p):
    t=TAU*p
    polygon(c,128,128,79,6,math.pi/6,'#2b647d',True,140)
    polygon(c,128,128,79,6,math.pi/6,'#88ddec',False,245,2.2)
    c.disk(128,128,32,'#123846');c.ring(128,128,34,color='#7af1ec',width=3)
    for j in range(3):
        a=j*math.pi/3
        c.ring(128,128,54,21,'#56c5da',1.3,200,a)
        angle=t+j*TAU/3;x,y=rotate(128+54*math.cos(angle),128+21*math.sin(angle),128,128,a)
        c.disk(x,y,5,'#efffba');c.disk(x-1,y-1,2,'#ffffff')
    for i in range(6):
        a=i*TAU/6;start=polar(128,128,39,a);end=polar(128,128,67,a)
        mid=polar(128,128,53,a+.13*math.sin(t*3+i))
        c.line([start,mid,end],'#d5ffbe',1.5,230)
        outer=polar(128,128,97,a);inner=polar(128,128,83,a)
        c.line([inner,outer],'#688b98',4)
        phase=(p+i/6)%1;pos=(inner[0]+(outer[0]-inner[0])*phase,inner[1]+(outer[1]-inner[1])*phase)
        c.disk(*pos,2.2,'#fbe799',round(240*math.sin(math.pi*phase)))
    spark(c,128,128,21,'#ecffff',t)


def space(c,p):
    t=TAU*p;cx=128;cy=127;tilt=-.19
    for x,y,r in [(37,52,2),(205,63,2),(64,209,2),(212,196,1.2),(42,177,1),(161,36,1),(189,216,1)]:
        spark(c,x,y,r,'#b6caff',t)
    # Three-dimensional accretion rings: back half first, then the occluding void.
    def disk(front):
        for layer in range(15):
            radius=47+layer*3.4;ry=15+layer*1.28
            color=blend('#ffeed0','#da6546',layer/17)
            pts=[]
            for j in range(101):
                a=(0 if front else math.pi)+j*math.pi/100
                wobble=1.2*math.sin(a*3+t)
                pts.append(rotate(cx+(radius+wobble)*math.cos(a),cy+ry*math.sin(a),cx,cy,tilt))
            c.line(pts,color,1.4,220-layer*5)
        for i in range(25):
            a=(i*TAU/25+t*2)%TAU
            if (a<math.pi)!=front:continue
            radius=54+(i%5)*8.2;x,y=rotate(cx+radius*math.cos(a),cy+radius*.31*math.sin(a),cx,cy,tilt)
            c.disk(x,y,1.5,'#fff3cd',235)
    disk(False)
    c.disk(cx,cy,42,'#060912');c.ring(cx,cy,43,color='#fff1bf',width=2.1)
    c.ring(cx,cy,46,45,'#e9a784',1,150)
    # A gravitationally lensed upper arc and a bright forward disk.
    pts=[(cx+56*math.cos(a),cy+65*math.sin(a)) for a in [math.pi+i*math.pi/80 for i in range(81)]]
    c.line(pts,'#e7b9fa',1.8,190)
    disk(True)
    c.disk(cx,cy-3,30,'#040610')


def exploration(c,p):
    t=TAU*p
    for r in (69,77,85):c.ring(128,128,r,color='#b7bc83' if r==77 else '#768c96',width=1.2)
    for i in range(32):
        a=i*TAU/32;c.line([polar(128,128,76 if i%4 else 70,a),polar(128,128,82,a)],'#e2c98c',1.4)
    for i in range(4):
        a=i*TAU/4;toppoint=polar(128,128,100,a)
        c.polygon([toppoint,polar(128,128,88,a-.07),polar(128,128,88,a+.07)],'#e4cb91',1,255,True)
    for ang,col in [(t-.6,'#efcf85'),(t+math.pi-.6,'#669b9b')]:
        c.polygon([polar(128,128,59,ang),polar(128,128,13,ang+1.5),polar(128,128,8,ang+math.pi)],col,1,250,True)
    c.disk(128,128,8,'#22353b');c.ring(128,128,8,color='#fff0bb',width=1.5)
    path=[(39,211),(73,188),(107,205),(151,187),(191,207),(222,184)]
    c.line(path,'#719c9e',1.4)
    for x,y in path:c.disk(x,y,2,'#c5ddaa')
    # Closed elliptical travel route avoids teleporting at the loop boundary.
    x=128+103*math.cos(t);y=128+99*math.sin(t)
    spark(c,x,y,4,'#fff0ad',-t)


def farming(c,p):
    t=TAU*p
    for i,x in enumerate((76,115,156)):
        sway=6*math.sin(t+i*.65);height=67+i*8
        c.curve([(x,218),(x+sway,177),(x-sway,146),(x+sway,218-height)],'#88aa55',2.4)
        for j in range(4):
            yy=210-height+j*11;xx=x+sway*(1-j*.12)
            filled_leaf(c,xx,yy,18,-2.4,'#edcf73')
            filled_leaf(c,xx,yy,18,-.75,'#d8b75d')
        filled_leaf(c,x,192,32,-2.4+.08*math.sin(t+i),'#78b86c')
    for x,y,phase in [(56,175,0),(194,180,.5)]:
        for k in range(6):petal(c,x,y,15+2*math.sin(t+phase),5,k*TAU/6,'#ebc0b3')
        c.disk(x,y,4,'#fff1ad');c.line([(x,y+8),(x,219)],'#70a774',2)
    bx=129+51*math.cos(t);by=90+22*math.sin(t)
    flap=.4+.6*abs(math.sin(6*t))
    for s in (-1,1):
        c.ellipse((bx-7+s*6,by-7-14*flap,bx+7+s*6,by+2),'#b5f1f4',alpha=145,fill=True)
    c.ellipse((bx-12,by-5,bx+12,by+7),'#ffd772',alpha=255,fill=True)
    for off in (-5,2):c.line([(bx+off,by-4),(bx+off,by+6)],'#53413e',3)
    c.disk(bx+10,by,3,'#39414c')
    c.line([(38,221),(211,221)],'#6b9771',1.4)


def ocean(c,p):
    t=TAU*p
    for j in range(4):
        yy=123+j*16
        pts=[(34+x*2.35,yy+7*math.sin(x*.15+t+j*.8)) for x in range(81)]
        c.line(pts,blend('#35aeb9','#affcf2',j/4),1.6,170+j*13)
    for x,base,height,color in [(64,217,64,'#edafa7'),(180,217,73,'#73d7bc')]:
        for s in (-1,1):
            pts=[(x,base),(x+s*6,base-height*.4),(x+s*22+4*math.sin(t),base-height)]
            c.line(pts,color,3)
            c.line([pts[1],(x+s*28,base-height*.5),(x+s*38+3*math.sin(t+.4),base-height*.85)],color,2)
            c.line([pts[-1],(pts[-1][0]-s*3,pts[-1][1]-13)],color,2)
    for i in range(8):
        phase=(p+i/8)%1;alpha=round(225*math.sin(math.pi*phase))
        x=51+(i*27)%163+5*math.sin(t+i);y=209-162*phase;r=3+i%4
        c.ring(x,y,r,color='#b8f9ff',width=1,alpha=alpha)
        c.disk(x-r*.25,y-r*.35,1,'#ffffff',alpha)
    c.ring(127,218,55,7,'#72cbb9',1.2,140)


def nether(c,p):
    t=TAU*p
    c.polygon([(62,193),(82,226),(175,226),(197,193)],'#543641',1,250,True)
    c.line([(63,193),(196,193)],'#f5b977',3)
    for i in range(7):
        x=79+i*17;c.line([(x,198),(x+7,219)],'#98544d',1.5)
    c.ring(128,193,63,10,'#ed8e49',3,245)
    for j in range(5):
        base=83+j*22;height=65+(2-abs(j-2))*16
        pts=[]
        for k in range(25):
            y=190-height*k/24;off=(9+8*k/24)*math.sin(t*2+j*.9+k*.11)
            pts.append((base+off-16*(1-k/24),y))
        for k in range(24,-1,-1):
            y=190-height*k/24;off=(9+8*k/24)*math.sin(t*2+j*.9+k*.11)
            pts.append((base+off+16*(1-k/24),y))
        c.polygon(pts,blend('#f57846','#ffe29a',(j%3)/3),alpha=225,fill=True)
    for i in range(10):
        phase=(p+i/10)%1;alpha=round(240*math.sin(math.pi*phase))
        x=70+(i*31)%117+8*math.sin(t+i);y=165-124*phase
        spark(c,x,y,2+i%2,'#ffdd8d',t+i,alpha)


def crystal(c,x,y,r,turn,color):
    # Four rotating 3D facets with an actual changing silhouette.
    points=[(x+r*.6*math.cos(turn+i*TAU/4),y+r*.18*math.sin(turn+i*TAU/4)) for i in range(4)]
    facets=sorted(range(4),key=lambda i:math.sin(turn+(i+.5)*TAU/4))
    top=(x,y-r);bottom=(x,y+r)
    for i in facets:
        nxt=(i+1)%4;light=.3+.45*(1+math.cos(turn+i*TAU/4))/2
        face=[top,points[i],bottom,points[nxt]]
        c.polygon(face,blend('#30233f',color,light),alpha=248,fill=True)
        c.polygon(face,blend(color,'#ffffff',.2),1.25,220)


def end(c,p):
    t=TAU*p
    c.ring(128,151,76,29,'#8c7aa8',1.4,190,-.2)
    back=[];front=[]
    for i in range(5):
        a=t+i*TAU/5;pt=(128+76*math.cos(a),145+29*math.sin(a))
        (back if math.sin(a)<0 else front).append((pt,i,a))
    for (x,y),i,a in back:crystal(c,x,y,12+i%2*4,-t,'#86d8c7')
    crystal(c,128,115+5*math.sin(t),57,t,'#c6a4f3')
    for (x,y),i,a in front:crystal(c,x,y,12+i%2*4,-t,'#b0f5db')
    for i in range(8):
        a=t+i*TAU/8;x,y=polar(128,123,99,a)
        spark(c,x,y,2.3,'#bc9ed5',-a,140)


def storage(c,p):
    t=TAU*p;center=(128,127)
    nodes=[(51,63),(128,39),(205,63),(205,192),(128,217),(51,192)]
    for i,(x,y) in enumerate(nodes):
        elbow=(x,127)
        c.line([center,elbow,(x,y)],'#3f7883',2)
        c.polygon([(x-13,y-10),(x+13,y-10),(x+13,y+10),(x-13,y+10)],'#294a56',1,255,True)
        c.polygon([(x-13,y-10),(x+13,y-10),(x+13,y+10),(x-13,y+10)],'#91dbce',1.2)
        c.line([(x-7,y),(x+4,y)],'#e1d59f',1.4)
        length=abs(x-128)+abs(y-127)
        for packet in range(2):
            phase=(p+i/6+packet*.5)%1;distance=phase*length
            if distance<=abs(x-128):
                xx=128+(1 if x>=128 else -1)*distance;yy=127
            else:
                xx=x;yy=127+(1 if y>=127 else -1)*(distance-abs(x-128))
            alpha=round(245*math.sin(math.pi*phase))
            c.polygon([(xx-3,yy-3),(xx+3,yy-3),(xx+3,yy+3),(xx-3,yy+3)],'#f6e6a9',1,alpha,True)
    c.polygon([(95,109),(128,90),(161,109),(161,145),(128,164),(95,145)],'#234354',1,255,True)
    c.polygon([(95,109),(128,90),(161,109),(161,145),(128,164),(95,145)],'#a2e8e1',1.7)
    c.line([(95,109),(128,127),(161,109)],'#d8cc94',1.5);c.line([(128,127),(128,164)],'#a3d8d4',1.5)
    for j in range(3):
        c.line([(101,120+j*8),(118,129+j*8)],'#b4e6da',1.6)
        c.disk(150,124+j*8,1.8,blend('#587f79','#ffe8aa',(.5+.5*math.sin(t+j*1.4))))


def restoration(c,p):
    t=TAU*p;growth=.25+.75*(.5-.5*math.cos(t))
    c.ring(128,215,55,9,'#6cc0a7',1.4,170)
    c.curve([(128,215),(111,178),(137,147),(128,105)],blend('#9c795b','#93c980',growth),3,250)
    for j,(x,y,s) in enumerate([(128,184,-1),(128,164,1),(128,144,-1),(128,123,1)]):
        angle=-2.5 if s<0 else -.7
        c.line([(x,y),polar(x,y,26,angle)],'#a68b69',1.4)
        filled_leaf(c,x,y,(27+j*3)*growth,angle+.12*math.sin(t+j),'#a4dd85',100+155*growth)
    blossom=6+growth*15
    for j in range(5):petal(c,128,103,blossom,blossom*.4,j*TAU/5+.1*math.sin(t),'#f3d6ac',230)
    c.disk(128,103,5,'#ffeeaa')
    sx,sy=177,69;c.disk(sx,sy,17,'#e6c782',180)
    for j in range(10):
        a=j*TAU/10+t*.2;c.line([polar(sx,sy,22,a),polar(sx,sy,28,a)],'#ecdca7',1.2,200)
    # Rain phases fade completely at wrap-around to avoid a visible jump.
    for i in range(8):
        phase=(p+i/8)%1;xx=55+(i*21)%139;yy=45+119*phase
        alpha=round(135*math.sin(math.pi*phase))
        c.line([(xx,yy),(xx-3,yy+8)],'#a6dfdc',1.5,alpha)
    for i in range(4):
        a=t+i*TAU/4;spark(c,128+75*math.cos(a),140+47*math.sin(a),2.3,'#fff4c7',a,200)


DRAW = {name: globals()[name] for name in NAMES}


def render_frame(theme, frame, size=FRAME_SIZE):
    canvas=MotionCanvas(theme,size)
    DRAW[theme](canvas,(frame % FRAMES)/FRAMES)
    return canvas.finish()


def save_gif(frames,path):
    # Preview-only dark cards keep translucent light readable in any viewer.
    cards=[]
    for frame in frames:
        card=Image.new('RGBA',frame.size,'#101724');card.alpha_composite(frame)
        cards.append(card.convert('RGB').quantize(colors=128,method=Image.Quantize.MEDIANCUT))
    cards[0].save(path,save_all=True,append_images=cards[1:],duration=DURATION_MS,loop=0,disposal=2,optimize=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'decor_assets')
    parser.add_argument('--themes',nargs='*',choices=list(NAMES))
    args=parser.parse_args(argv)
    entries=[];posters=[]
    for theme in args.themes or NAMES:
        slug,title,motion=NAMES[theme];ident=theme+'_'+slug+'_animated'
        frames=[render_frame(theme,index) for index in range(FRAMES)]
        folder=args.output/'assets/autoquestgen/textures/block'/theme
        folder.mkdir(parents=True,exist_ok=True)
        # AtlasSpriteIcon uses Minecraft's block atlas. Frame positions are
        # row-major; decor_library emits the corresponding .png.mcmeta.
        sheet=Image.new('RGBA',(FRAME_SIZE*4,FRAME_SIZE*8))
        for index,frame in enumerate(frames):sheet.paste(frame,((index%4)*FRAME_SIZE,(index//4)*FRAME_SIZE))
        path=folder/(ident+'.png');sheet.save(path,optimize=True)
        previews=args.output/'animation_previews';previews.mkdir(parents=True,exist_ok=True)
        frames[0].save(previews/(ident+'_poster.png'),optimize=True)
        save_gif(frames,previews/(ident+'.gif'))
        entries.append(dict(id=ident,theme=theme,kind='animation',texture=f'autoquestgen:block/{theme}/{ident}',
            path=f'assets/autoquestgen/textures/block/{theme}/{ident}.png',
            title=title,width=sheet.width,height=sheet.height,
            animation=dict(frame_width=FRAME_SIZE,frame_height=FRAME_SIZE,frames=FRAMES,frametime=2,interpolate=False),alpha=255,
            motion=motion,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            preview=f'animation_previews/{ident}.gif',
            poster=f'animation_previews/{ident}_poster.png'))
        posters.append((title,frames[0]));print('Rendered '+theme,flush=True)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'animated_manifest.json').write_text(json.dumps({'schema':1,'assets':entries,
        'provenance':'Original native animated geometry; tools/render_animated_decor.py; no external artwork.'},indent=2)+'\n',encoding='utf-8')
    sheet=Image.new('RGB',(1024,870),'#101724');draw=ImageDraw.Draw(sheet)
    try:font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',17)
    except OSError:font=ImageFont.load_default()
    for index,(title,poster) in enumerate(posters):
        x=(index%4)*256;y=(index//4)*290
        sheet.paste(poster,(x,y),poster);draw.text((x+15,y+255),title,font=font,fill='#dfe9ec')
    sheet.save(args.output/'animated_contact_sheet.png')
    cards=''.join(f'<article><img src="{html.escape(e["preview"])}" alt="{html.escape(e["motion"])}">'
                  f'<h2>{html.escape(e["title"])}</h2><p>{html.escape(e["motion"])}</p></article>' for e in entries)
    (args.output/'animated_preview.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Original animated quest landmarks</title>'
        '<style>body{margin:32px;background:#101724;color:#dfe9ec;font:15px/1.5 system-ui}'
        'h1{font-size:30px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(256px,1fr));gap:24px}'
        'article{background:#172330;border:1px solid #2a4050;border-radius:14px;padding:14px}'
        'img{width:256px;max-width:100%;display:block;margin:auto}h2{font-size:20px}p{color:#a9bcc8}</style>'
        '<h1>Twelve original animated landmarks</h1><p>32 frames each · 3.2-second loops · fully offline</p>'
        '<main>'+cards+'</main></html>',encoding='utf-8')


if __name__=='__main__':
    main()
