# -*- coding: utf-8 -*-
"""生成默认封面图 cover.jpg (900x383, 公众号头图比例 2.35:1)"""
import os
from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
W, H = 900, 383

img = Image.new("RGB", (W, H), "#1D3557")
d = ImageDraw.Draw(img)
d.rectangle([0, H - 12, W, H], fill="#E63946")

def font(size):
    for name in ["msyhbd.ttc", "msyh.ttc", "simhei.ttf"]:
        p = os.path.join("C:\\Windows\\Fonts", name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

title = "每日新闻速览"
sub = "时政 · 财经 · 科技 · 民生 · 文体"
f1, f2 = font(86), font(34)
w1 = d.textlength(title, font=f1)
w2 = d.textlength(sub, font=f2)
d.text(((W - w1) / 2, 110), title, font=f1, fill="#FFFFFF")
d.text(((W - w2) / 2, 236), sub, font=f2, fill="#A8DADC")

out = os.path.join(BASE, "cover.jpg")
img.save(out, "JPEG", quality=90)
print("OK:", out)
