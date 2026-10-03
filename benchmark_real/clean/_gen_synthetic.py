"""阶段 180 · 生成「干净图误报探针」的合成压力样本（占位，待真实照片替换）。

仅用于验证 clean_bench_probe.py 端到端可跑、能正确量出误报。这些不是真实照片，
右下角内容刻意制造可能触发 translucent/edge_text 信号的情形，以压力测试 179 的
plate_footprint_prior 在干净图上是否误发整牌。真实干净图（自然照片，无 AI 水印）
由用户在豆包/图库生成后放入 images/ 并登记到 manifest/ground_truth。
"""
import os

from PIL import Image, ImageDraw, ImageFont

H = W = 1024
OUT = os.path.dirname(os.path.abspath(__file__))


def _bg(mode: str) -> Image.Image:
    if mode == "plain":
        return Image.new("RGB", (W, H), (128, 128, 128))
    if mode == "natural":
        img = Image.new("RGB", (W, H))
        px = img.load()
        for y in range(H):
            for x in range(W):
                px[x, y] = ((x * 255) // W, (y * 255) // H, 128)
        return img
    raise ValueError(mode)


def _bottom_right_patch(d: ImageDraw.ImageDraw, color, size=(260, 110), pad=24):
    """在右下角画一个块（模拟可能存在的角标/招牌/反光）。"""
    x0, y0 = W - size[0] - pad, H - size[1] - pad
    d.rectangle([x0, y0, x0 + size[0], y0 + size[1]], fill=color)


def make(path, kind):
    img = _bg("plain" if kind != "natural" else "natural")
    d = ImageDraw.Draw(img, "RGBA")
    if kind == "plain":
        pass
    elif kind == "corner_text":
        # 右下角一段文字（edge_text 候选）
        _bottom_right_patch(d, (20, 20, 20, 255), size=(300, 70))
        f = ImageFont.load_default()
        d.text((W - 300 - 18, H - 70 - 18), "SAMPLE", fill=(235, 235, 235, 255), font=f)
    elif kind == "corner_logo":
        # 右下角实心圆角 Logo 块（形状候选）
        _bottom_right_patch(d, (40, 60, 140, 255), size=(200, 120))
    elif kind == "corner_translucent":
        # 右下角半透明亮斑（translucent 候选）：在较暗底上叠一层淡白
        base = Image.new("RGB", (W, H), (60, 60, 60))
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        _bottom_right_patch(od, (255, 255, 255, 90), size=(320, 130))
        img = Image.alpha_composite(base.convert("RGBA"), ov).convert("RGB")
    elif kind == "natural":
        # 自然渐变场景 + 右下角招牌文字（更真实的干扰）
        _bottom_right_patch(d, (10, 10, 10, 230), size=(340, 90))
        f = ImageFont.load_default()
        d.text((W - 340 - 16, H - 90 - 16), "OPEN 24H", fill=(240, 240, 240, 255), font=f)
    else:
        raise ValueError(kind)
    img.save(path)


CASES = {
    "syn_01_plain": "plain",
    "syn_02_corner_text": "corner_text",
    "syn_03_corner_logo": "corner_logo",
    "syn_04_corner_translucent": "corner_translucent",
    "syn_05_natural": "natural",
}

if __name__ == "__main__":
    for name, kind in CASES.items():
        p = os.path.join(OUT, "images", name + ".png")
        make(p, kind)
        print("wrote", p)
