"""视觉工具回归测试：锁住两个「中文路径」缺陷。

背景（真实踩过的坑）：
    1) `cv2.imwrite` 在 Windows 非 ASCII 路径下静默返回 False 而不抛异常，
       导致 frames 列表里记着根本不存在的文件；
    2) `easyocr` 内部同样用 `cv2.imread`，中文路径下拿到 None，
       进而抛 "'NoneType' object has no attribute 'shape'"。

本测试特意在**含中文的临时目录**里跑，确保这两条链路始终安全。

用法：.venv\\Scripts\\python.exe tests\\test_vision_tools.py
"""
import os
import shutil
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  OK   {name}')
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f'  FAIL {name}  {detail}')


def make_video(path: str, text: str) -> bool:
    """造一个带中文字的短视频。"""
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        print(f'  跳过：缺少依赖 ({e})')
        return False

    font = None
    for p in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
        if os.path.isfile(p):
            try:
                font = ImageFont.truetype(p, 40)
                break
            except OSError:
                pass

    w, h, fps, n = 480, 270, 10, 20
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    if not writer.isOpened():
        return False
    try:
        for _ in range(n):
            frame = np.full((h, w, 3), 60, dtype=np.uint8)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            if font is not None:
                img = Image.fromarray(rgb)
                ImageDraw.Draw(img).text((30, 100), text, font=font,
                                         fill=(255, 220, 120))
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            writer.write(frame)
    finally:
        writer.release()
    return os.path.isfile(path) and os.path.getsize(path) > 0


def main() -> int:
    from app.ai import tools

    # 关键：临时目录名带中文，复现真实环境（C:\Users\联想\...）
    tmp_root = os.path.join(tempfile.gettempdir(), '审计测试_中文路径')
    shutil.rmtree(tmp_root, ignore_errors=True)
    os.makedirs(tmp_root, exist_ok=True)

    try:
        print('== 1. 抽帧：中文路径写入 ==')
        video = os.path.join(tmp_root, '样例视频.mp4')
        if not make_video(video, '加微信 日结'):
            print('  SKIP 无法生成测试视频')
            return 2

        res = tools.extract_frames(video, max_frames=3)
        frames = res.get('frames') or []
        check('抽帧返回 ok', res.get('ok') is True, str(res.get('error')))
        check('拿到帧路径', len(frames) > 0, f'frames={len(frames)}')
        if frames:
            all_exist = all(os.path.isfile(f['path']) and os.path.getsize(f['path']) > 0
                            for f in frames)
            check('每个帧文件真实存在且非空', all_exist,
                  f'检查 {len(frames)} 个文件')

        print('== 2. Unicode 安全读图 ==')
        if frames:
            img = tools._imread_unicode(frames[0]['path'])
            check('_imread_unicode 不返回 None', img is not None,
                  '中文路径下 cv2.imread 会返回 None，必须走 imdecode')
            if img is not None:
                check('读出的图像有 shape', hasattr(img, 'shape') and img.shape[0] > 0,
                      str(getattr(img, 'shape', None)))

        print('== 3. OCR（可选依赖）==')
        try:
            import easyocr  # noqa: F401
            has_ocr = True
        except ImportError:
            has_ocr = False
        if not has_ocr:
            print('  SKIP easyocr 未安装')
        elif frames:
            out = tools.ocr_text([f['path'] for f in frames])
            check('ocr_text 未抛异常且 available=True',
                  out.get('available') is True and 'note' not in out, str(out.get('note')))
            text = out.get('text') or ''
            check('识别出了文字', len(text) > 0, f'text={text!r}')
            check('没出现 NoneType/shape 报错',
                  'NoneType' not in str(out.get('note') or ''), str(out.get('note')))
            print(f'       识别结果: {text[:60]!r}')

        print('== 4. 关键词联动 ==')
        if has_ocr and frames:
            kw = tools.keyword_risk(tools.ocr_text([f['path'] for f in frames]).get('text', ''))
            check('灰产文案命中关键词', bool(kw.get('hits')), str(kw.get('hits')))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print()
    print(f'结果：{PASS} 通过 / {FAIL} 失败')
    if FAILURES:
        print('失败项:', ', '.join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
