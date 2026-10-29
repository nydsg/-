"""OCR 效果对照验证：同一套审核流程，只有画面文字不同。

用途：证明「多模态依赖」不是装了就完事，而是真的改变了审核结论。
      两个样本的区别**只在帧上的中文文案**，如果 OCR 生效，
      可疑样本应当命中「广告营销 / 违法违规」并因此被判更严。

用法：.venv\\Scripts\\python.exe tools\\compare_ocr.py
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ai.agent import AuditAgent  # noqa: E402
from app.ai import tools  # noqa: E402

CASES = [
    ('正常样本', os.path.join(ROOT, 'uploads', 'temp_video', 'case_normal.mp4')),
    ('可疑样本', os.path.join(ROOT, 'uploads', 'temp_video', 'case_spam.mp4')),
]


def run_one(label: str, video: str) -> dict:
    print(f'\n{"=" * 58}')
    print(f'{label}  {os.path.basename(video)}')
    print('=' * 58)

    result = None
    for ev in AuditAgent().run(video_path=video, filename=os.path.basename(video),
                               with_llm=False):
        t = ev.get('type')
        if t == 'step':
            st = ev['step']
            if st['id'] in ('text', 'vision', 'rules', 'decide'):
                print(f"  [{st['name']:<6}] {st['status']:<6} {st['elapsed_ms']:>5}ms  "
                      f"{st['detail'][:56]}")
        elif t == 'result':
            result = ev

    if not result:
        print('  ✗ 没有拿到结果')
        return {}

    print(f"  结论: {result['verdict']}  score={result['score']}  "
          f"conf={result['confidence']}  evidence_strength={result.get('evidence_strength')}")
    if result['categories']:
        print(f"  命中类别: {', '.join(result['categories'])}")
    for r in result['reasons']:
        print(f"    · {r[:90]}")
    return result


def main() -> int:
    print('=== OCR 依赖状态 ===')
    try:
        import easyocr  # noqa: F401
        print('  easyocr: 已安装')
    except ImportError:
        print('  ✗ easyocr 未安装，本脚本无法验证（请先装依赖）')
        return 2
    try:
        import torch
        print(f'  torch: {torch.__version__}')
    except ImportError:
        print('  torch: 缺失')

    o = tools.ocr_text
    probe = tools.extract_frames(CASES[0][1], 1)
    probe_frames = [f['path'] for f in probe.get('frames') or []]
    avail = bool(o(probe_frames).get('available')) if probe_frames else False
    print('  ocr_text 可用性:', '是' if avail else '否（模型未就绪）')

    results = {}
    for label, video in CASES:
        if not os.path.isfile(video):
            print(f'\n跳过 {label}：文件不存在 {video}')
            print('  请先运行 tools/make_test_videos.py')
            return 2
        results[label] = run_one(label, video)

    print(f'\n{"=" * 58}')
    print('对照结论')
    print('=' * 58)
    normal = results.get('正常样本') or {}
    spam = results.get('可疑样本') or {}
    print(f"  正常样本: verdict={normal.get('verdict')}  "
          f"score={normal.get('score')}  "
          f"类别={normal.get('categories')}")
    print(f"  可疑样本: verdict={spam.get('verdict')}  "
          f"score={spam.get('score')}  "
          f"类别={spam.get('categories')}")

    hit = bool(spam.get('categories'))
    if hit and (spam.get('score', 100) < normal.get('score', 100)):
        print('\n  ✅ OCR 生效：可疑样本被额外打低分并命中违规类别')
        return 0
    print('\n  ⚠️ 未观察到 OCR 带来的差异（可能是模型未下载或文字未识别）')
    return 1


if __name__ == '__main__':
    sys.exit(main())
