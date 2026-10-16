"""Original, deterministic flat illustration renderer for the MV director.

This is authored procedural artwork, not an image-generation model. No reference
artist assets, fonts, or character designs are bundled with the project.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 960, 540
PALETTES = {
    "bloom": {"bg": "#f4b3b7", "paper": "#fff0ce", "ink": "#25212f", "accent": "#e34362", "cool": "#799fa1"},
    "fracture": {"bg": "#e94c62", "paper": "#f7e5cb", "ink": "#222531", "accent": "#e9ee7a", "cool": "#9ca6ca"},
    "orbit": {"bg": "#738ac8", "paper": "#f7eccd", "ink": "#222536", "accent": "#f9a19a", "cool": "#a8d6cd"},
}


def font_provenance() -> dict:
    path = _font_path()
    return {"family": "Malgun Gothic" if path and "malgun" in path.name.lower() else "system fallback",
            "source": "read-only operating-system font reference" if path else "Pillow default",
            "redistributed": False, "korean_supported": bool(path and "malgun" in path.name.lower())}


@lru_cache(maxsize=1)
def _font_path():
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = [fonts / "malgunbd.ttf", fonts / "malgun.ttf",
                  Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    return next((p for p in candidates if p.is_file()), None)


@lru_cache(maxsize=64)
def font(size: int):
    path = _font_path()
    return ImageFont.truetype(str(path), max(8, int(size))) if path else ImageFont.load_default()


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _line(draw, points, fill, width=2):
    draw.line(points, fill=fill, width=width, joint="curve")


def _polygon(draw, center, radius, sides, rotation, fill, outline=None, width=1):
    points = [(center[0] + radius * math.cos(rotation + j * math.tau / sides),
               center[1] + radius * math.sin(rotation + j * math.tau / sides)) for j in range(sides)]
    draw.polygon(points, fill=fill, outline=outline, width=width)


def flower(draw, x, y, radius, angle, paper, ink, accent):
    """The recurring six-petal seed/flower used by the bloom direction."""
    for j in range(6):
        a = angle + j * math.tau / 6
        px, py = x + math.cos(a) * radius * .54, y + math.sin(a) * radius * .54
        r = radius * .43
        draw.ellipse((px-r, py-r, px+r, py+r), fill=paper, outline=ink, width=2)
    r = radius * .25
    draw.ellipse((x-r, y-r, x+r, y+r), fill=accent, outline=ink, width=2)


@lru_cache(maxsize=6)
def _background(theme: str, dark: bool):
    p = PALETTES[theme]
    top = np.array(_rgb(p["ink"] if dark else p["bg"]), dtype=float)
    bottom = np.array(_rgb(p["cool"] if dark else p["paper"]), dtype=float)
    yy = np.linspace(0, 1, HEIGHT)[:, None, None]
    arr = np.broadcast_to(top[None, None, :] * (1 - yy * .30) + bottom[None, None, :] * yy * .30,
                          (HEIGHT, WIDTH, 3)).astype(np.uint8).copy()
    return Image.fromarray(arr)


def _character(time, energy, pose, emotion, palette, scale=1., facing=1, singing=0.):
    """Original paper-doll figure: split ink hair, crescent clip, ribbon coat."""
    p = palette
    im = Image.new("RGBA", (320, 470))
    d = ImageDraw.Draw(im)
    ink, paper, accent = p["ink"], p["paper"], p["accent"]
    sway = math.sin(time * 2.5) * (4 + energy * 4)
    headx, heady = 160 + sway, 110
    # Ribbon silhouettes and hair behind the body create real layer parallax.
    d.polygon([(100,100),(220,100),(235,280),(208,255),(186,295),(153,248),(117,284),(88,240)], fill=ink)
    d.ellipse((92+sway,33,231+sway,192), fill=ink)
    # Legs, boots and the off-center coat seam.
    _line(d,[(142,319),(135,410),(119,444)],ink,20)
    _line(d,[(177,319),(188,411),(207,441)],ink,20)
    d.rounded_rectangle((101,430,141,451), radius=8, fill=paper, outline=ink, width=3)
    d.rounded_rectangle((188,428,227,449), radius=8, fill=paper, outline=ink, width=3)
    d.polygon([(129,182),(191,182),(215,329),(171,346),(112,326)], fill=accent, outline=ink, width=4)
    d.polygon([(145,182),(174,183),(183,325),(157,338)], fill=paper, outline=ink, width=3)
    _line(d,[(168,214),(171,324)],ink,3)
    for y in (242,270,300):
        d.ellipse((173,y,179,y+6),fill=ink)
    # Arms are posed, not just translated together with the character.
    wave = math.sin(time * 3) * 10
    if pose in ("reach", "crown"):
        left, right = [(130,204),(92,160),(91+wave,105)], [(192,205),(236,156),(230-wave,100)]
    elif pose == "fall":
        left, right = [(129,204),(81,203),(52,172+wave)], [(194,205),(242,216),(273,185-wave)]
    elif pose == "fold":
        left, right = [(130,206),(114,246),(177,246)], [(193,206),(207,255),(145,266)]
    elif pose == "point":
        left, right = [(130,204),(103,261),(120,288)], [(192,206),(234,177),(270,158+wave)]
    else:
        left, right = [(130,206),(99,260),(102+wave,302)], [(191,206),(218,252),(244,251-wave)]
    for points in (left,right):
        _line(d,points,ink,22); _line(d,points,accent,15)
        x,y = points[-1]
        d.ellipse((x-9,y-10,x+9,y+10), fill=paper, outline=ink, width=3)
    # Oversized angular face, asymmetric dark fringe and distinct oval eyes.
    d.polygon([(111+sway,78),(201+sway,70),(216+sway,119),(192+sway,166),(161+sway,181),(123+sway,159),(106+sway,119)],fill=paper,outline=ink,width=4)
    d.polygon([(101+sway,85),(116+sway,47),(177+sway,33),(216+sway,72),(219+sway,109),(183+sway,74),(161+sway,115),(151+sway,81),(117+sway,115)],fill=ink)
    # Crescent hair clip is the identifying recurring character mark.
    d.ellipse((196+sway,65,219+sway,89),fill=accent)
    d.ellipse((204+sway,62,223+sway,82),fill=ink)
    blink = abs(math.sin(time * .91)) < .035
    for x in (140+sway,183+sway):
        if blink or emotion == "quiet":
            _line(d,[(x-7,129),(x+7,127)],ink,4)
        else:
            d.ellipse((x-6,115,x+6,134),fill=ink)
            d.ellipse((x-3,117,x,121),fill=paper)
    if emotion == "hurt":
        _line(d,[(131+sway,108),(146+sway,111)],ink,3)
        _line(d,[(176+sway,111),(191+sway,108)],ink,3)
        d.line([(184+sway,140),(185+sway,157)],fill=p["cool"],width=4)
    if singing > .25:
        r=3+singing*5
        d.ellipse((161+sway-r,145,161+sway+r,150+r),fill=ink)
    elif emotion == "hope":
        d.arc((150+sway,141,174+sway,157),0,180,fill=ink,width=3)
    else:
        _line(d,[(156+sway,150),(167+sway,151)],ink,2)
    # Collar and long scarf are in front of the face/body.
    d.polygon([(134,180),(159,194),(179,181),(177,207),(156,217),(134,201)],fill=paper,outline=ink,width=3)
    d.polygon([(160,205),(182,211),(213+wave,305),(190+wave,297)],fill=p["cool"],outline=ink,width=3)
    if facing < 0:
        im=im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if scale != 1:
        im=im.resize((max(2,int(320*scale)),max(2,int(470*scale))),Image.Resampling.LANCZOS)
    return im


def _fit_lines(text, max_width=780, max_lines=2):
    """Wrap CJK by glyph and Latin by words, shrink rather than truncate lyrics."""
    text = " ".join(str(text).split())
    if not text:
        return [], 32
    measure = ImageDraw.Draw(Image.new("L", (1,1)))
    for size in (34,32,29,26,23,20):
        f=font(size); lines=[]; line=""
        for char in text:
            if line and measure.textlength(line+char,font=f)>max_width:
                lines.append(line.strip()); line=char.lstrip()
            else:
                line += char
        if line.strip(): lines.append(line.strip())
        if len(lines)<=max_lines:
            return lines,size
    return lines,20


class MVArtRenderer:
    def __init__(self, project: dict):
        self.project=project
        self.duration=project["duration"]
        self.shots=project["shots"]
        self.features=project["audio_analysis"]["frames"]
        self.seed=int(project.get("seed",7))

    def _feature(self, t):
        if not self.features: return {"energy":0.,"onset":0.,"brightness":0.}
        i=min(len(self.features)-1,max(0,int(t/self.project["audio_analysis"]["frame_hop_seconds"])))
        return self.features[i]

    def render(self, time: float) -> Image.Image:
        t=max(0.,min(float(time),self.duration))
        shot=next((s for s in self.shots if s["start"]<=t<s["end"]),self.shots[-1])
        local=t-shot["start"]; length=max(.1,shot["end"]-shot["start"]); u=local/length
        feature=self._feature(t); energy=feature["energy"]; onset=feature["onset"]
        theme=shot["theme"]; p=PALETTES[theme]; dark=shot["lighting"]=="night"
        im=_background(theme,dark).copy(); d=ImageDraw.Draw(im)
        ink=p["paper"] if dark else p["ink"]
        # The retained symbol changes from seed -> bloom / tear -> fragments /
        # small moon -> orbit. It remains visible through successive scenes.
        growth=shot["motif_state"]
        phase=t*.45
        if shot["composition"] in ("tunnel","crown"):
            for j in reversed(range(7)):
                r=55+j*72+(t*15)%72
                color=p["ink"] if j%2 else p["accent"]
                if theme=="orbit":
                    d.ellipse((480-r*1.5,245-r*.7,480+r*1.5,245+r*.7),outline=color,width=8)
                else:
                    _polygon(d,(480,245),r,6,phase*.12,color if j==6 else None,color,5)
        else:
            # Slanted paper planes and stripes give a composed stage rather
            # than a waveform screen. Depth layers move at different speeds.
            d.polygon([(0,355),(960,270),(960,540),(0,540)],fill=p["ink"] if dark else p["cool"])
            for j in range(12):
                x=j*110-120+(t*12)%110
                _line(d,[(x,540),(x+135,330)],p["paper"] if dark else p["bg"],2)
            d.ellipse((75,45,265,235),fill=p["accent"],outline=p["ink"],width=3)
            d.ellipse((105,45,265,205),fill=p["ink"] if dark else p["bg"])
        if theme=="bloom":
            for j in range(8):
                a=j*2.399+self.seed*.4
                x=480+math.cos(a+phase*.08)*(240+j*21)
                y=235+math.sin(a+phase*.08)*(125+j*12)
                r=16+growth*20+(j%3)*6+onset*3
                _line(d,[(x,520),(x+math.sin(t+j)*14,y)],ink,2)
                flower(d,x,y,r,a+phase*.22,p["paper"],p["ink"],p["accent"])
        elif theme=="fracture":
            for j in range(14):
                x=(j*173+self.seed*13)%1100-70
                y=(j*97)%500+math.sin(t*.5+j)*25
                dx=math.sin(j*3.1)*growth*52
                _polygon(d,(x+dx,y),14+(j%4)*10,3,phase*(.1 if j%2 else -.1)+j,
                         p["paper"] if j%3 else p["accent"],p["ink"],2)
            for j in range(3):
                x=260+j*250
                _line(d,[(x-12*growth,0),(x+25,110),(x-18,225),(x+20*growth,330)],ink,2)
        else:
            for j in range(5):
                rx=170+j*58; ry=65+j*22
                d.ellipse((480-rx,235-ry,480+rx,235+ry),outline=ink,width=2)
                a=phase*(.35+j*.13)+j*1.6
                x,y=480+math.cos(a)*rx,235+math.sin(a)*ry
                _polygon(d,(x,y),10+j*3,4,a,p["accent"],p["ink"],2)
            for j in range(30):
                x=(j*127+self.seed*37)%960; y=(j*83)%400
                r=2+(j%4==0)*3
                d.ellipse((x-r,y-r,x+r,y+r),fill=ink)
        comp=shot["composition"]
        # Scale and camera movement are assigned by the director, bounded per
        # composition so heads/eyes remain deliberately framed.
        if comp=="close":
            scale=1.65+u*.12; x,y=480,-40
        elif comp=="wide":
            scale=.80+u*.05; x,y=490,60
        elif comp=="duet":
            scale=.96; x,y=365,10
        elif comp=="fall":
            scale=.97; x,y=480+math.sin(t)*20,10+u*42
        elif comp=="crown":
            scale=1.04; x,y=480,12
        else:
            scale=1.02+u*.06; x,y=480+math.sin(local*.7)*16,12
        bob=math.sin(t*math.tau*self.project["audio_analysis"]["tempo_bpm"]/60)*energy*4
        character=_character(t,energy,shot["pose"],shot["emotion"],p,scale,1,energy if shot.get("lyric") else 0)
        angle=(math.sin(t*.8)*2+onset*2)*(1 if comp!="fall" else 4)
        if comp=="dutch": angle+=12
        character=character.rotate(angle,resample=Image.Resampling.BICUBIC,expand=True)
        if comp=="duet":
            echo=_character(t+.8,energy,"fold","quiet",p,.91,-1)
            echo.putalpha(echo.getchannel("A").point(lambda a:int(a*.5)))
            im.paste(echo,(int(650-echo.width/2),int(y+12)),echo)
        im.paste(character,(int(x-character.width/2),int(y+bob)),character)
        d=ImageDraw.Draw(im)
        # Foreground symbols mask the lower edge and establish an extra plane.
        if theme=="bloom":
            for j in range(5):
                xx=j*260-40+math.sin(t*.3+j)*15
                flower(d,xx,510,70+(j%2)*18,phase*.12,p["ink"],p["ink"],p["accent"])
        else:
            for j in range(4):
                xx=(j*310+40+t*9)%1180-110
                _polygon(d,(xx,525),65,3,phase*.1+j,p["ink"],p["paper"],2)
        # Lyric is attached to the actual transcript segment, never invented
        # from the theme. Segment timing may be estimated and is documented.
        segment=next((s for s in self.project["transcript"]["segments"] if s["start"]<=t<s["end"]),None)
        if segment and segment["text"].strip():
            self._lyrics(im,segment,t,p)
        # A single thin frame, shot-number code and progress markers create a
        # cohesive visual identity without intrusive software branding.
        d=ImageDraw.Draw(im)
        d.rectangle((23,22,936,518),outline=ink,width=1)
        d.text((38,32),"N O C T U R N E   /   L Y R I C   F I L M",font=font(10),fill=ink)
        d.text((889,31),f"{shot['id']:02d}",font=font(13),fill=ink)
        for j in range(12):
            xx=40+j*10
            d.line((xx,506,xx+5,506),fill=p["accent"] if j/12<=t/max(.1,self.duration) else ink,width=3)
        # Short dip-to-paper at only musically selected cuts; not a constant
        # effect that would obscure the artwork.
        if shot["transition"]=="paper-flash" and 0<local<.12:
            overlay=Image.new("RGB",im.size,p["paper"])
            im=Image.blend(im,overlay,(1-local/.12)*.70)
        return im

    def _lyrics(self,im,segment,t,p):
        lines,size=_fit_lines(segment["text"])
        f=font(size); d=ImageDraw.Draw(im)
        h=len(lines)*(size+9)+24
        top=477-h
        # Localized dark backing leaves the upper two-thirds of the artwork
        # intact and remains legible on every palette.
        overlay=Image.new("RGBA",im.size)
        od=ImageDraw.Draw(overlay)
        od.rounded_rectangle((65,top,895,483),radius=6,fill=(*_rgb(p["ink"]),224))
        for j,line in enumerate(lines):
            width=od.textlength(line,font=f)
            od.text(((WIDTH-width)/2,top+9+j*(size+9)),line,font=f,fill=p["paper"])
        # A line advances through the real segment interval; word-level
        # timing remains in project JSON for a later karaoke editor.
        progress=max(0,min(1,(t-segment["start"])/max(.01,segment["end"]-segment["start"])))
        od.rectangle((81,477,81+798*progress,480),fill=p["accent"])
        im.paste(overlay,(0,0),overlay)
