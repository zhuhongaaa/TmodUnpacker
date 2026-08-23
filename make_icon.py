"""生成应用图标 app.ico(柔和蓝主色,打开的箱子 + 提取箭头)。"""
import math
from PIL import Image, ImageDraw


def rounded_rect(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


def make_icon(size=256):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 主色渐变背景(圆角方形)
    primary = (61, 111, 176)       # #3D6FB0
    primary_dark = (45, 88, 142)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bgd = ImageDraw.Draw(bg)
    # 简单纵向渐变
    for y in range(size):
        t = y / size
        r = int(primary[0] * (1 - t) + primary_dark[0] * t)
        g = int(primary[1] * (1 - t) + primary_dark[1] * t)
        b = int(primary[2] * (1 - t) + primary_dark[2] * t)
        bgd.line([(0, y), (size, y)], fill=(r, g, b, 255))
    # 圆角遮罩
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=int(size * 0.22), fill=255)
    img.paste(bg, (0, 0), mask)

    cx = size / 2
    # 上升的内容方块(三个小方块,表示提取出的资源)
    block = int(size * 0.11)
    gap = int(size * 0.035)
    cols = [cx - block - gap, cx - block / 2, cx + gap]
    block_top = int(size * 0.20)
    block_color = (255, 255, 255, 230)
    for i, bx in enumerate(cols):
        # 错落高度,营造上升感
        h = block + (1 - i) * int(size * 0.04)
        d.rounded_rectangle([bx, block_top + (i * int(size * 0.03)),
                             bx + block, block_top + (i * int(size * 0.03)) + h],
                            radius=int(block * 0.18), fill=block_color)

    # 箱子(打开的盖子 + 箱体),白色描边 + 半透明白填充
    box_top = int(size * 0.50)
    box_bottom = int(size * 0.78)
    box_left = int(size * 0.24)
    box_right = int(size * 0.76)
    line_w = max(3, int(size * 0.028))
    white = (255, 255, 255, 255)
    fill = (255, 255, 255, 60)

    # 翻开的盖子(上方梯形)
    lid_l_top = (box_left + int(size * 0.03), box_top - int(size * 0.10))
    lid_r_top = (box_right - int(size * 0.03), box_top - int(size * 0.10))
    d.polygon([lid_l_top, lid_r_top,
               (box_right, box_top), (box_left, box_top)],
              fill=fill, outline=white, width=line_w)

    # 箱体(梯形)
    body_bl = (box_left + int(size * 0.02), box_bottom)
    body_br = (box_right - int(size * 0.02), box_bottom)
    d.polygon([(box_left, box_top), (box_right, box_top),
               body_br, body_bl], fill=fill, outline=white, width=line_w)
    # 箱体开口横线
    d.line([(box_left, box_top), (box_right, box_top)],
           fill=white, width=line_w)

    return img


if __name__ == "__main__":
    import os
    icon = make_icon(256)
    out_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(out_dir, "app.ico")
    png_path = os.path.join(out_dir, "app.png")
    icon.save(png_path, "PNG")
    # 多尺寸 ico
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    icon.save(ico_path, format="ICO", sizes=sizes)
    print(f"已生成: {ico_path}")
    print(f"已生成: {png_path}")
