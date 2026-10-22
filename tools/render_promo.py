"""Code-drawn vertical promo. Requires Pillow; no external footage or music."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS = 1080, 1920, 24
MINT, WHITE, MUTED = '#91ffcf', '#f4f4ff', '#a9aec5'
FONT = Path('C:/Windows/Fonts/segoeui.ttf')
BOLD = Path('C:/Windows/Fonts/seguibl.ttf')
_fonts = {}


def font(size, bold=False):
    key = size, bold
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(BOLD if bold else FONT), size)
    return _fonts[key]


def text(draw, xy, value, size=50, color=WHITE, bold=False, width=880, spacing=12):
    chosen = size
    for line in value.splitlines():
        while draw.textlength(line, font=font(chosen, bold)) > width and chosen > 20:
            chosen -= 1
    draw.multiline_text(xy, value, font=font(chosen, bold), fill=color, spacing=spacing)


def wrapped(draw, xy, value, size=44, color=WHITE, width=820, bold=False):
    lines, line = [], ''
    for word in value.split():
        candidate = (line + ' ' + word).strip()
        if line and draw.textlength(candidate, font=font(size, bold)) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    text(draw, xy, '\n'.join(lines), size, color, bold, width)
    return len(lines)*(size+12)


def bubble(draw, y, label, body, bot=False):
    draw.rounded_rectangle((94, y, 946, y+220), radius=36, fill='#183c3a' if bot else '#24273e',
                           outline='#397361' if bot else '#3d425e', width=2)
    text(draw, (128,y+24), label.upper(), 24, MINT if bot else MUTED, True)
    wrapped(draw, (128,y+72), body, 41, width=778)


def avatar(draw, x, y, label, active=False, scale=1.):
    r = round(100*scale)
    if active:
        draw.ellipse((x-r-10,y-r-10,x+r+10,y+r+10), outline=MINT, width=6)
    draw.ellipse((x-r,y-r,x+r,y+r), fill='#323653' if label != 'ИИ' else '#285444')
    f = font(48, True)
    box = draw.textbbox((0,0),label,font=f)
    draw.text((x-(box[2]-box[0])/2, y-35), label, font=f, fill=WHITE)


def background():
    ys = np.linspace(0,1,H)[:,None,None]
    top, bottom = np.array([13,15,30]), np.array([22,26,49])
    arr = np.repeat((top*(1-ys)+bottom*ys).astype(np.uint8), W, axis=1)
    image = Image.fromarray(arr, 'RGB').convert('RGBA')
    glow = Image.new('RGBA',(W,H))
    d = ImageDraw.Draw(glow)
    d.ellipse((450,-330,1350,650), fill=(94,77,255,65))
    d.ellipse((-650,900,300,1750), fill=(52,210,157,40))
    return Image.alpha_composite(image,glow.filter(ImageFilter.GaussianBlur(100))).convert('RGB')


def render_frame(t, timeline, repo, base):
    im = base.copy()
    d = ImageDraw.Draw(im)
    scene = next((s for s in timeline['scenes'] if s['start'] <= t < s['end']), timeline['scenes'][-1])
    u = t-scene['start']
    name = scene['name']
    text(d,(94,116),'СОБЕСЕДНИК',30,MINT,True)
    text(d,(94,163),'Ещё один в твоём войсе',27,MUTED)
    d.rounded_rectangle((94,220,946,226),radius=3,fill='#34394e')
    d.rounded_rectangle((94,220,94+852*min(1,t/timeline['duration']),226),radius=3,fill=MINT)
    titles = {'hook':'В ВОЙСЕ\nКОГО-ТО\nНЕ ХВАТАЕТ?', 'intro':'ОН НА СВЯЗИ.',
              'memory':'СЛУШАЕТ.\nЗАПОМИНАЕТ.', 'local':'ТВОЙ ПК.\nТВОЙ ИИ.',
              'stop':'ИНОГДА НУЖНА\nТИШИНА.', 'cta':'ЗАБИРАЙ\nСОБЕСЕДНИКА.'}
    text(d,(94,304),titles[name],100 if name=='hook' else 88,WHITE,True)
    if name == 'hook':
        for x,label in [(230,'ТЫ'),(530,'ДРУГ'),(830,'?')]:
            avatar(d,x,940,label,active=label=='?',scale=1+.025*math.sin(t*4))
        text(d,(94,1170),'Есть идея.',62,MINT,True)
        wrapped(d,(94,1270),'Локальный голосовой ИИ для Discord.',49)
    elif name == 'intro':
        avatar(d,522,690,'ИИ',active=t<scene['speech_end'])
        bubble(d,860,'Ты','Собеседник, ты тут?')
        if u > .6:
            bubble(d,1110,'Собеседник','Я на связи. О чём поговорим?',True)
        text(d,(94,1420),'Кратко. По теме. Голосом.',42,MINT,True)
    elif name == 'memory':
        bubble(d,650,'Ты','Запомни: моя любимая игра — Dota 2.')
        if u > .5:
            bubble(d,900,'Собеседник','Запомнил. Твоя любимая игра — Dota 2.',True)
        if u > 3.5:
            d.rounded_rectangle((94,1190,946,1420),radius=32,fill='#102f2c',outline='#417b67',width=2)
            text(d,(128,1220),'ПОЗЖЕ:',26,MUTED,True)
            text(d,(128,1270),'— Во что я люблю играть?\n— В Dota 2.',43,WHITE,width=770)
        text(d,(94,1500),'Память переживает перезапуск.',35,MINT,True)
    elif name == 'local':
        d.rounded_rectangle((94,660,946,1200),radius=40,fill='#20253a',outline='#555b7e',width=3)
        text(d,(136,710),'НА ТВОЁМ КОМПЬЮТЕРЕ',29,MUTED,True)
        for i,label in enumerate(['РАСПОЗНАЁТ РЕЧЬ','ДУМАЕТ','ГОВОРИТ']):
            y=810+i*114
            d.ellipse((136,y+8,176,y+48),fill=MINT)
            text(d,(199,y),label,43,WHITE,True,width=670)
        d.rounded_rectangle((252,1200,788,1225),radius=12,fill='#555b7e')
        text(d,(94,1330),'0 ₽ ЗА ПЛАТНЫЕ API',54,MINT,True)
        wrapped(d,(94,1430),'Модели работают локально. Discord требует интернет.',35,MUTED)
    elif name == 'stop':
        bubble(d,690,'Ты','Собеседник, заткнись.')
        stopped = u > 2.1
        for i in range(39):
            bar = 4 if stopped else 10+int(abs(math.sin(i*.7+t*9))*90)
            x = 110+i*21
            d.rounded_rectangle((x,1060-bar,x+9,1060+bar),radius=4,fill=MINT if not stopped else '#596176')
        text(d,(94,1250),'МОЛЧИТ.' if stopped else 'СЛЫШИТ КОМАНДУ.',66,MINT,True)
        if stopped:
            text(d,(94,1360),'До нового обращения.',42,MUTED)
    elif name == 'cta':
        text(d,(94,645),'БЕСПЛАТНО · ОТКРЫТЫЙ КОД',36,MINT,True)
        d.rounded_rectangle((94,770,946,1160),radius=38,fill='#e9fff5')
        text(d,(132,812),'GITHUB',36,'#285444',True)
        text(d,(132,904),repo.replace('https://github.com/','').replace('/','/\n',1),63,'#10231c',True,width=770)
        text(d,(94,1250),'ИНСТРУКЦИЯ ВНУТРИ',47,WHITE,True)
        text(d,(94,1370),'Поставь звезду, если зашёл.',39,MINT)
        text(d,(94,1485),'Windows · локальные модели',31,MUTED)
    text(d,(94,1690),'Постановочный пример · диалоги сокращены',24,MUTED)
    return im


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repo',required=True)
    p.add_argument('--ffmpeg')
    p.add_argument('--previews-only',action='store_true')
    args=p.parse_args()
    folder=ROOT/'artifacts/tiktok'
    timeline=json.loads((folder/'timeline.json').read_text(encoding='utf-8'))
    base=background()
    sheet=Image.new('RGB',(1080,1280))
    for i,t in enumerate([2,7,14,20,26.5,30]):
        preview=render_frame(t,timeline,args.repo,base)
        preview.save(folder/f'preview-{i}.png')
        sheet.paste(preview.resize((360,640)),((i%3)*360,(i//3)*640))
    sheet.save(folder/'contact-sheet.jpg',quality=90)
    if args.previews_only:
        return
    if not args.ffmpeg:
        raise ValueError('Pass a local FFmpeg executable.')
    output=folder/'sobesednik-tiktok.mp4'
    command=[args.ffmpeg,'-y','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}',
             '-r',str(FPS),'-i','pipe:0','-i',str(folder/'soundtrack.wav'),'-map','0:v','-map','1:a',
             '-c:v','libx264','-preset','fast','-crf','20','-threads','4','-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','192k','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-movflags','+faststart',
             '-t',str(timeline['duration']),str(output)]
    with subprocess.Popen(command,stdin=subprocess.PIPE) as encoder:
        for n in range(math.ceil(timeline['duration']*FPS)):
            encoder.stdin.write(render_frame(n/FPS,timeline,args.repo,base).tobytes())
            if n%(FPS*5)==0:
                print('Rendered',n//FPS,'seconds',flush=True)
        encoder.stdin.close()
        if encoder.wait():
            raise RuntimeError('Video encoding failed.')
    print(output,flush=True)


if __name__=='__main__':
    main()
