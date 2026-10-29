"""验证 fail-closed 总闸：LLM 可用但视觉/文字零证据时，不得自动通过。

复现方式：把 AI_VISION_MODEL 清空，视觉层失效、CLIP 也没装 →
evidence_strength 应该为 0，即使 LLM（glm-4-flash）判定 pass 也必须被压回 manual。

用法：.venv\\Scripts\\python.exe tools\\check_failclosed.py
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 必须在 import app 之前清掉视觉模型，模拟"多模态不可用"
os.environ['AI_VISION_MODEL'] = ''

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ai.agent import AuditAgent  # noqa: E402
from app.config import Config  # noqa: E402

print(f'AI 已启用: {bool(Config.AI_API_KEY)}')
print(f'文本模型 : {Config.AI_MODEL}')
print(f'视觉模型 : {Config.AI_VISION_MODEL!r}  ← 故意清空，模拟多模态不可用')
print()


def make_blank_video(path: str) -> bool:
    """造一个**没有任何文字**的纯色视频。

    关键：必须无文字，否则 OCR 会给出一路证据，
    evidence_strength 就不为 0，测不出 fail-closed。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False
    w, h, fps, n = 320, 180, 10, 20
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    if not writer.isOpened():
        return False
    try:
        for i in range(n):
            frame = np.full((h, w, 3), 40 + i, dtype=np.uint8)
            cv2.circle(frame, (80 + i * 4, 90), 24, (120, 140, 160), -1)
            writer.write(frame)
    finally:
        writer.release()
    return os.path.isfile(path) and os.path.getsize(path) > 0


video = os.path.join(ROOT, 'uploads', 'temp_video', 'case_blank.mp4')
if not make_blank_video(video):
    print('无法生成测试视频（缺 opencv？）')
    sys.exit(2)
print(f'测试视频: {os.path.basename(video)}（纯色、无文字，用于制造零证据）')
print()

result = None
for ev in AuditAgent().run(video_path=video, filename='case_blank.mp4', with_llm=True):
    if ev.get('type') == 'step' and ev['step']['id'] == 'vision':
        s = ev['step']
        print(f"  画面理解: {s['status']}  {s['detail'][:70]}")
    if ev.get('type') == 'step' and ev['step']['id'] == 'decide':
        print(f"  综合裁定: {ev['step']['detail'][:70]}")
    if ev.get('type') == 'result':
        result = ev

if not result:
    print('❌ 没有拿到结果')
    sys.exit(2)

print()
print(f"  结论      : {result['verdict']}")
print(f"  分数      : {result['score']}")
print(f"  置信度    : {result['confidence']}")
print(f"  证据强度  : {result.get('evidence_strength')}")
print(f"  使用模型  : {result.get('model')}")
for r in (result.get('reasons') or [])[:3]:
    print(f"    · {r[:78]}")

ok = (result['verdict'] != 'pass') and result.get('evidence_strength') == 0
print()
if ok:
    print('✅ fail-closed 生效：零证据没有被自动通过')
    sys.exit(0)
print('❌ 漏洞仍在：零证据却给出了 pass')
sys.exit(1)
