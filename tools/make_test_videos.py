"""生成两组对照测试视频，用来验证 OCR 是否真的影响审核结论。

为什么要自己造样本：
    cv2.putText 不支持中文，而校园违规里最常见的恰恰是画面上的中文
    （"加微信""日结""代考"）。这里用 PIL + 系统微软雅黑把中文画上去，
    再交给 cv2 编码，这样 OCR 才有真实内容可识别。

产物：
    uploads/temp_video/case_normal.mp4   干净画面 + 正常文案   → 期望 pass / manual
    uploads/temp_video/case_spam.mp4     画面叠加灰产文案       → 期望被关键词命中

用法：.venv\\Scripts\\python.exe tools\\make_test_videos.py
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'uploads', 'temp_video')
os.makedirs(OUT_DIR, exist_ok=True)

W, H, FPS, SECONDS = 640, 360, 12, 4
FRAMES = FPS * SECONDS

FONT_CANDIDATES = [
    r'C:\Windows\Fonts\msyh.ttc',
    r'C:\Windows\Fonts\msyhbd.ttc',
    r'C:\Windows\Fonts\simhei.ttf',
    r'C:\Windows\Fonts\simsun.ttc',
]


def load_font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_video(path: str, bg_text: str, overlay_lines: list[str]) -> str:
    """bg_text 用 cv2 画（英文）；overlay_lines 用 PIL 画（支持中文）。"""
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(path, fourcc, FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError(f'无法创建视频: {path}')

    font_big = load_font(34)
    font_small = load_font(24)

    for i in range(FRAMES):
        # 背景：渐变 + 轻微运动
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        for y in range(H):
            frame[y, :] = (40 + y // 6, 45 + y // 8, 55 + y // 10)
        cv2.circle(frame, (100 + i * 6, 300), 40, (90, 120, 200), -1)
        cv2.putText(frame, 'CAMPUS LIFE', (30, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (240, 240, 240), 2)

        # 用 PIL 叠加中文（cv2 不支持）
        if overlay_lines:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            draw = ImageDraw.Draw(img)
            y = 110
            for idx, line in enumerate(overlay_lines):
                font = font_big if idx == 0 else font_small
                # 半透明底条，模拟字幕
                bbox = draw.textbbox((0, 0), line, font=font)
                w = bbox[2] - bbox[0]
                draw.rectangle([24, y - 6, 24 + w + 20, y + (bbox[3] - bbox[1]) + 10],
                               fill=(0, 0, 0))
                draw.text((34, y), line, font=font, fill=(255, 230, 120))
                y += (bbox[3] - bbox[1]) + 22
            frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

        writer.write(frame)

    writer.release()
    size = os.path.getsize(path)
    print(f'  ✓ {os.path.basename(path):<16} {size:>8,} 字节  {FRAMES} 帧')
    return path


def make_timeline_video(path: str, spec: list[tuple[float, list[str]]],
                        fps: int = 10) -> str:
    """按时序生成视频：spec = [(持续秒数, 该段叠加的中文文案), ...]

    用来验证**切片审核**能不能把违规定位到具体的时间段。
    例如 5 段各 4 秒，只有第 3 段（8–12s）是灰产文案，其余正常。
    """
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(path, fourcc, fps, (W, H))
    if not writer.isOpened():
        raise RuntimeError(f'无法创建视频: {path}')

    font_big = load_font(34)
    font_small = load_font(24)
    frame_no = 0

    for seg_index, (seconds, lines) in enumerate(spec):
        count = max(1, int(seconds * fps))
        for _ in range(count):
            frame = np.zeros((H, W, 3), dtype=np.uint8)
            for y in range(H):
                frame[y, :] = (38 + y // 7, 44 + y // 9, 58 + y // 11)
            # 每段一个可区分的主色块，方便人眼确认"确实换了一段"
            hue = [(90, 120, 200), (80, 170, 120), (200, 90, 90),
                   (150, 110, 200), (200, 160, 80)][seg_index % 5]
            cv2.circle(frame, (120 + (frame_no % 60) * 6, 290), 42, hue, -1)
            cv2.putText(frame, f'SEG {seg_index + 1}', (30, 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (235, 235, 235), 2)

            if lines:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                draw = ImageDraw.Draw(img)
                y = 120
                for idx, line in enumerate(lines):
                    font = font_big if idx == 0 else font_small
                    bbox = draw.textbbox((0, 0), line, font=font)
                    w = bbox[2] - bbox[0]
                    draw.rectangle(
                        [24, y - 6, 24 + w + 20,
                         y + (bbox[3] - bbox[1]) + 10], fill=(0, 0, 0))
                    draw.text((34, y), line, font=font, fill=(255, 230, 120))
                    y += (bbox[3] - bbox[1]) + 22
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

            writer.write(frame)
            frame_no += 1

    writer.release()
    size = os.path.getsize(path)
    print(f'  ✓ {os.path.basename(path):<18} {size:>9,} 字节  '
          f'{frame_no} 帧 / {frame_no / fps:.0f}s / {len(spec)} 段')
    return path


def main():
    print('生成对照测试视频：')
    normal = make_video(
        os.path.join(OUT_DIR, 'case_normal.mp4'),
        'CAMPUS LIFE',
        ['校园图书馆日常', '期末复习记录'],
    )
    spam = make_video(
        os.path.join(OUT_DIR, 'case_spam.mp4'),
        'CAMPUS LIFE',
        ['加微信 日结兼职', '扫码进群 代考包过'],
    )

    # 切片审核专用：20 秒 5 段，只有第 3 段（8–12s）是违规内容
    mixed = make_timeline_video(
        os.path.join(OUT_DIR, 'case_mixed.mp4'),
        [
            (4, ['校园图书馆日常', '期末复习记录']),
            (4, ['食堂窗口打饭', '今天的番茄炒蛋']),
            (4, ['加微信 日结兼职', '扫码进群 代考包过']),   # ← 只有这一段违规
            (4, ['操场夜跑打卡', '三公里完成']),
            (4, ['宿舍收拾行李', '准备回家']),
        ],
    )

    print('\n提示：这三个样本的差异**只在画面文字**，')
    print('      如果 OCR / 多模态生效，case_spam 应命中「广告营销 / 违法违规」；')
    print('      case_mixed 用于验证切片审核能否把违规定位到第 3 段（8–12s）。')
    print(f'\n正常样本: {normal}')
    print(f'可疑样本: {spam}')
    print(f'切片样本: {mixed}')


if __name__ == '__main__':
    main()
