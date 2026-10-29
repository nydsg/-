"""切片审核单元测试：切片规划 / 段级判定 / 聚合策略。

不依赖网络与 API Key —— 只测纯逻辑，可以在任何环境下跑。

用法：.venv\\Scripts\\python.exe tests\\test_segment.py
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

from app.ai.segment_audit import _aggregate, _segment_verdict  # noqa: E402
from app.ai.segmenter import extract_segment_frames, plan_segments  # noqa: E402

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


def main() -> int:
    video = os.path.join(ROOT, 'uploads', 'temp_video', 'case_mixed.mp4')

    print('== 1. 切片规划 ==')
    if not os.path.isfile(video):
        print('  跳过（缺少 case_mixed.mp4，请先运行 tools/make_test_videos.py）')
        video = os.path.join(ROOT, 'uploads', 'temp_video', 'case_normal.mp4')
    if not os.path.isfile(video):
        print('  没有可用测试视频，终止')
        return 2

    plan = plan_segments(video, segment_seconds=4, max_segments=8)
    check('规划成功', plan.get('ok') is True, str(plan.get('error')))
    check('时长 > 0', (plan.get('duration') or 0) > 0, str(plan.get('duration')))
    check('段数与时长匹配',
          plan.get('count') == max(1, round((plan.get('duration') or 0) / 4)),
          f"count={plan.get('count')} duration={plan.get('duration')}")
    segs = plan.get('segments') or []
    check('段区间连续无空洞',
          all(abs(segs[i]['end'] - segs[i + 1]['start']) < 0.05
              for i in range(len(segs) - 1)),
          f'{[(s["start"], s["end"]) for s in segs]}')
    check('首段从 0 开始', segs and segs[0]['start'] == 0, str(segs[:1]))
    check('末段到片尾', segs and abs(segs[-1]['end'] - plan['duration']) < 0.05,
          f"{segs[-1]['end']} vs {plan['duration']}")

    print('== 2. 段数上限生效 ==')
    plan3 = plan_segments(video, segment_seconds=1, max_segments=3)
    check('max_segments 限制段数', plan3.get('count') == 3, str(plan3.get('count')))

    print('== 3. 段内代表帧抽取 ==')
    frames = extract_segment_frames(video, plan_segments(video, 4, 6))
    check('抽帧成功', frames.get('ok') is True, str(frames.get('error')))
    seg_list = frames.get('segments') or []
    check('每段都有代表帧', all(s['frames'] for s in seg_list),
          f"{[len(s['frames']) for s in seg_list]}")
    paths = [s['frames'][0]['path'] for s in seg_list if s['frames']]
    check('帧文件真实存在且非空',
          all(os.path.isfile(p) and os.path.getsize(p) > 0 for p in paths),
          f'{len(paths)} 个文件')

    print('== 4. 段级判定 ==')
    high = _segment_verdict({'ok': True, 'risks': [{'type': '色情低俗', 'severity': 'high'}],
                             'categories': ['色情低俗']})
    check('high → reject', high['verdict'] == 'reject', high['verdict'])
    medium = _segment_verdict({'ok': True, 'risks': [{'type': '广告营销', 'severity': 'medium'}]})
    check('medium → manual', medium['verdict'] == 'manual', medium['verdict'])
    low = _segment_verdict({'ok': True, 'risks': [{'type': '校园安全', 'severity': 'low'}]})
    check('low → manual', low['verdict'] == 'manual', low['verdict'])
    clean = _segment_verdict({'ok': True, 'risks': [], 'categories': []})
    check('无风险 → pass', clean['verdict'] == 'pass', clean['verdict'])
    unknown = _segment_verdict({'ok': False, 'note': '不可用'})
    check('模型不可用 → unknown', unknown['verdict'] == 'unknown', unknown['verdict'])

    print('== 5. 聚合策略（从严）==')
    def seg(v, score=80, cat=None, start=0, end=4):
        return {'verdict': v, 'score': score, 'categories': cat or [],
                'risks': [], 'start': start, 'end': end, 'note': ''}

    a = _aggregate([seg('pass', 95), seg('reject', 15, ['色情低俗'], 4, 8),
                    seg('pass', 95, start=8, end=12)])
    check('任一段 reject → 整体 reject', a['verdict'] == 'reject', a['verdict'])
    check('整体分数取最严', a['score'] == 15, str(a['score']))
    check('理由定位到秒', any('4' in r and '8' in r for r in a['reasons']),
          str(a['reasons']))

    b = _aggregate([seg('pass', 95), seg('manual', 55, ['广告营销'], 4, 8)])
    check('任一段 manual → 整体 manual', b['verdict'] == 'manual', b['verdict'])

    c = _aggregate([seg('pass', 95), seg('pass', 95, start=4, end=8)])
    check('全部 pass → 整体 pass', c['verdict'] == 'pass', c['verdict'])

    d = _aggregate([seg('unknown', 50), seg('unknown', 50, start=4, end=8)])
    check('全部 unknown → manual（fail-closed）', d['verdict'] == 'manual', d['verdict'])
    check('unknown 时证据强度为 0', d.get('evidence_strength') == 0,
          str(d.get('evidence_strength')))

    e = _aggregate([])
    check('空切片 → manual（fail-closed）', e['verdict'] == 'manual', e['verdict'])

    print()
    print(f'结果：{PASS} 通过 / {FAIL} 失败')
    if FAILURES:
        print('失败项:', ', '.join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
