"""
审核判定逻辑的回归测试（不需要数据库 / 不需要模型权重）。

覆盖三类关键行为：
1. 零证据时必须转人工（fail-closed），绝不自动通过；
2. 有高危证据时必须拒绝；
3. 擦边但合规（成人礼、体育竞技）不应被一刀切拒绝。

运行：.venv\\Scripts\\python.exe tests\\test_audit_rules.py
"""
import os
import sys

# Windows 控制台默认 GBK，符号会 UnicodeEncodeError，先切 UTF-8 容错
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.agent import AuditAgent  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, actual, expect, extra: str = ''):
    global PASS, FAIL
    ok = actual == expect
    if ok:
        PASS += 1
        print(f'  ✅ {name}: {actual}')
    else:
        FAIL += 1
        print(f'  ❌ {name}: 期望 {expect}，实际 {actual}  {extra}')


def verdict_of(vision=None, ocr=None, kw=None, meta=None, ocr_text=''):
    vision = vision or {'available': False, 'scores': {}, 'labels': []}
    ocr = ocr or {'available': False, 'text': ocr_text}
    kw = kw or {'hits': {}}
    meta = meta or {'flags': []}
    return AuditAgent._rule_verdict(meta, vision, ocr, kw, {'hits': []})


print('== 场景 1：零证据（模型未安装）必须转人工 ==')
res = verdict_of()
check('verdict', res['verdict'], 'manual')
check('score 封顶 60', res['score'] <= 60, True, f"实际 {res['score']}")
check('evidence_strength', res.get('evidence_strength'), 0,
      '零证据必须显式为 0')

print('== 场景 2：视觉判色情 0.82（有证据）必须拒绝 ==')
res = verdict_of(vision={'available': True, 'scores': {'porn': 0.82, 'normal': 0.1},
                         'labels': ['normal', 'porn']})
check('verdict', res['verdict'], 'reject')
check('类别命中', '色情低俗' in res['categories'], True)

print('== 场景 3：成人礼类正常画面（视觉可用、无风险分）应通过 ==')
res = verdict_of(vision={'available': True, 'scores': {'porn': 0.05, 'normal': 0.9},
                         'labels': ['normal', 'porn']})
check('verdict', res['verdict'], 'pass')
check('无违规类别', res['categories'], [])

print('== 场景 4：文字命中灰产关键词应扣分（不通过）==')
res = verdict_of(kw={'hits': {'广告营销': ['兼职日结', '加微信'],
                              '违法违规': ['代考']}})
check('verdict 非 pass', res['verdict'] != 'pass', True)
check('分数已扣减', res['score'] < 100, True, f"实际 {res['score']}")

print('== 场景 5：文件名可疑信号（18+）轻微扣分 ==')
res = verdict_of(meta={'flags': ['18']})
check('score < 100', res['score'] < 100, True)

print('== 场景 6：OCR 有文字时 evidence_strength >= 1 ==')
res = verdict_of(ocr={'available': True, 'text': '校园歌唱比赛'},
                 ocr_text='校园歌唱比赛')
check('evidence_strength', res.get('evidence_strength') >= 1, True)

print()
print(f'结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(0 if FAIL == 0 else 1)
