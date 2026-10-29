"""Agent 可调用工具集（Function Calling / MCP 同构）。

每个工具都是纯函数 + JSON Schema，既能给 LLM 做 tool calling，
也能原样暴露成 MCP Server 的 tool —— 这是赛题「MCP/工具集成 4%」的落点。

真实实现尽量复用已有能力：
- 抽帧：opencv（复用原 utils/video_utils.py 的思路）
- 视觉风险：CLIP（可选依赖，缺失则降级为占位信号）
- 文字风险：EasyOCR（可选依赖）
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any

from ..config import Config
from . import rag

logger = logging.getLogger(__name__)

# 关键词兜底表：即使没有任何模型依赖，也能做出可用判定
RISK_LEXICON: dict[str, list[str]] = {
    '政治敏感': ['分裂', '暴恐', '邪教', '颠覆'],
    '色情低俗': ['裸', '色情', '一夜情', '约炮', '低俗'],
    '暴力危险': ['血腥', '砍', '自杀', '自残', '爆炸', '打架'],
    '违法违规': ['赌博', '毒品', '大麻', '香烟', '代考', '代刷', '翻墙'],
    '广告营销': ['加微信', '二维码', '扫码', '私聊', '兼职日结', '代理'],
    '侵权抄袭': ['搬运', '未授权', '无水印转载'],
}


# ---------------- 工具 1：抽帧 ----------------
def extract_frames(video_path: str, max_frames: int | None = None) -> dict:
    """从视频里等间隔抽 N 帧，返回临时图片路径。"""
    max_frames = max_frames or Config.MAX_FRAMES
    if not os.path.isfile(video_path):
        return {'ok': False, 'error': '视频文件不存在', 'frames': []}
    try:
        import cv2
    except ImportError:
        return {'ok': False, 'error': 'opencv 未安装，跳过抽帧', 'frames': []}

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {'ok': False, 'error': '视频无法解码', 'frames': []}

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    step = max(1, total // max(1, max_frames))
    out_dir = os.path.join(Config.TEMP_DIR, f'frames_{int(time.time() * 1000)}')
    os.makedirs(out_dir, exist_ok=True)

    frames: list[dict] = []
    idx = 0
    saved = 0
    write_errors = 0
    while saved < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        path = os.path.join(out_dir, f'f{saved:02d}.jpg')
        # 关键修复：不能用 cv2.imwrite 写中文路径。
        # OpenCV 的 imwrite 在 Windows 上走 ANSI API，遇到非 ASCII 路径
        # （比如 C:\Users\联想\...）会**静默返回 False 而不抛异常**，
        # 结果是把不存在的文件路径记进 frames，后续 OCR 统统读不到。
        # 改用 imencode + 二进制写入，对 Unicode 路径安全。
        try:
            ok_enc, buf = cv2.imencode('.jpg', frame)
            if not ok_enc:
                raise OSError('imencode 失败')
            with open(path, 'wb') as f:
                f.write(buf.tobytes())
            if os.path.getsize(path) > 0:
                frames.append({'index': idx, 'path': path,
                               'ts': round(idx / fps, 2) if fps else 0})
                saved += 1
            else:
                raise OSError('写入 0 字节')
        except Exception as e:
            write_errors += 1
            if write_errors == 1:          # 只记一次，避免刷屏
                logger.warning('写帧失败（%s）: %s', path, e)
        idx += step
    cap.release()
    if not frames and write_errors:
        return {'ok': False, 'frames': [], 'total_frames': total,
                'fps': round(fps, 2), 'dir': out_dir,
                'error': f'抽帧写入失败 {write_errors} 次，请检查目录权限或路径字符'}
    return {'ok': bool(frames), 'frames': frames, 'total_frames': total,
            'fps': round(fps, 2), 'dir': out_dir}


# ---------------- 工具 1.5：帧选择（视觉层前置，省 token 提准确率） ----------------
def frame_select(frame_paths: list[str], keep: int = 4) -> list[dict]:
    """从抽出的 15 帧里挑出最有信息量的几帧。

    两个筛选维度（纯 OpenCV，零模型）：
      1. 清晰度：拉普拉斯方差，滤掉运动模糊帧；
      2. 去重：16x16 灰度二值指纹的汉明距离，跳过和已选帧太像的。

    收益：送多模态模型的帧从 15 → 4，token 成本降到 1/3，
          且不会因为一堆重复/模糊帧稀释判断。
    """
    if not frame_paths:
        return []
    try:
        import cv2
        import numpy as np
    except ImportError:
        return [{'path': p, 'index': i, 'sharp': 0.0, 'reason': 'no-cv2'}
                for i, p in enumerate(frame_paths[:keep])]

    candidates: list[dict] = []
    for i, p in enumerate(frame_paths):
        img = _imread_unicode(p)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        small = cv2.resize(gray, (16, 16))
        fingerprint = (small > small.mean()).flatten()
        candidates.append({'path': p, 'index': i, 'sharp': sharp,
                           'fp': fingerprint})

    if not candidates:
        return []

    # 清晰度优先；同分时保持原始时间顺序
    candidates.sort(key=lambda c: -c['sharp'])
    selected: list[dict] = []
    for cand in candidates:
        if len(selected) >= keep:
            break
        # 与已选帧做汉明距离去重（阈值 24/256 ≈ 90% 相似）
        dup = False
        for chosen in selected:
            diff = int(np.count_nonzero(cand['fp'] != chosen['fp']))
            if diff < 24:
                dup = True
                break
        if not dup:
            selected.append(cand)

    # 不足时放宽，用剩下的补齐（避免只有 1~2 帧）
    if len(selected) < min(keep, len(candidates)):
        for cand in candidates:
            if len(selected) >= min(keep, len(candidates)):
                break
            if cand not in selected:
                selected.append(cand)

    selected.sort(key=lambda c: c['index'])
    return [{'path': c['path'], 'index': c['index'],
             'sharp': round(c['sharp'], 1)} for c in selected]


# ---------------- 工具 2：视觉理解（VLM，主路径） ----------------
VLM_PROMPT = """你是校园内容安全审核的视觉分析员。请仔细观察这张视频关键帧，输出严格 JSON（不要 markdown 代码块）：

{
  "scene": "一句话描述画面场景，如：食堂后厨、图书馆自习区、操场跑道",
  "persons": 0,
  "text_in_image": "画面中出现的文字（原样抄录，没有就填空字符串）",
  "risky": [
    {"type": "风险类别", "severity": "low|medium|high", "detail": "具体是什么问题"}
  ],
  "tags": ["3-5 个内容标签"],
  "safe": true
}

判定要求：
1. risky 只写**真的看到**的问题，没看到就留空数组，不要臆测；
2. 风险类别从这几类里选：色情低俗 / 暴力危险 / 违法违规 / 广告营销 / 侵权抄袭 / 校园安全 / 政治敏感；
3. 以下属于**正常校园内容**，不要误判：成人礼等正式仪式、体育竞技、课堂实验演示（有防护）、社团活动、食堂后厨规范操作；
4. 画面里有二维码、联系方式、"加微信""日结""代考"等，必须计入 广告营销 或 违法违规。
5. severity=high 仅用于明确违规（裸露、血腥、赌博、代考等）。"""


def vlm_describe(frame_paths: list[str], filename: str = '',
                 max_frames: int = 3) -> dict:
    """用多模态模型逐帧理解画面，产出可引用的结构化证据。

    相比 CLIP 的优势：
      - 能说出"第 2 帧：食堂后厨，明火灶台旁堆放纸箱"，而不是一个 0.72 的分数；
      - 一次调用同时产出风险证据 + 内容标签（标签可喂给推荐系统）；
      - 能识别 CLIP 完全无能为力的场景（二维码引流、代考广告、安防细节泄露）。

    失败/无 key 时返回 available=False，由调用方降级到 CLIP 或转人工。
    """
    from .llm import LLMClient

    client = LLMClient()
    if not client.vision_enabled or not frame_paths:
        return {'ok': False, 'available': False, 'frames': [],
                'note': '多模态模型未启用（需 AI_API_KEY + AI_VISION_MODEL）',
                'risks': [], 'categories': [], 'tags': []}

    picked = frame_select(frame_paths, keep=max_frames)
    cache_key = _vlm_cache_key(picked, client.vision_model)
    cached = _VLM_CACHE.get(cache_key)
    if cached and time.time() - cached['at'] < _VLM_TTL:
        result = dict(cached['result'])
        result['cached'] = True
        result['elapsed_ms'] = 0
        return result

    results: list[dict] = []
    errors: list[str] = []
    started = time.time()

    for item in picked:
        try:
            with open(item['path'], 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
        except OSError as e:
            errors.append(f"读取帧失败 {item['index']}: {e}")
            continue
        parsed = None
        for attempt in (0, 1):
            try:
                res = client.vision(VLM_PROMPT, [b64], max_tokens=700)
                parsed = _parse_vision_json(res.content)
                if parsed is None:
                    errors.append(f"帧 {item['index']} 返回非 JSON")
                else:
                    parsed.update(frame=item['index'], ts=item.get('ts', 0),
                                  latency_ms=res.latency_ms)
                break
            except Exception as e:
                msg = str(e)
                # 免费额度常有 QPS 限制：命中 429 时退避重试一次
                if attempt == 0 and ('429' in msg or 'rate' in msg.lower()
                                     or 'concurrency' in msg.lower()):
                    time.sleep(1.5)
                    continue
                errors.append(f"帧 {item['index']} 调用失败: {msg[:80]}")
                break
        if parsed:
            results.append(parsed)

    if not results:
        return {'ok': False, 'available': True, 'frames': [], 'risks': [],
                'categories': [], 'tags': [], 'errors': errors[:3],
                'note': f'多模态调用失败：{errors[0] if errors else "未知原因"}'}

    # 汇总风险与标签
    category_scores: dict[str, float] = {}
    risks: list[dict] = []
    seen_text: list[str] = []
    tags: list[str] = []
    for r in results:
        if r.get('text_in_image'):
            seen_text.append(str(r['text_in_image']))
        for t in r.get('tags') or []:
            if t and t not in tags:
                tags.append(str(t))
        for risk in r.get('risky') or []:
            if not isinstance(risk, dict):
                continue
            cat = str(risk.get('type') or '其他')
            sev = str(risk.get('severity') or 'low').lower()
            weight = {'high': 1.0, 'medium': 0.6, 'low': 0.3}.get(sev, 0.3)
            category_scores[cat] = max(category_scores.get(cat, 0), weight)
            risks.append({'type': cat, 'severity': sev, 'frame': r['frame'],
                          'detail': str(risk.get('detail') or '')[:120]})

    payload = {
        'ok': True,
        'available': True,
        'frames': results,
        'risks': risks,
        'categories': sorted(category_scores, key=lambda c: -category_scores[c]),
        'category_scores': category_scores,
        'tags': tags[:8],
        'text': ' '.join(dict.fromkeys(seen_text))[:500],
        'elapsed_ms': int((time.time() - started) * 1000),
        'errors': errors[:3],
        'model': client.vision_model,
    }
    _remember_vlm(cache_key, payload)
    return payload


def _vlm_cache_key(picked: list[dict], model: str) -> str:
    """按帧内容（而非路径）做缓存键：同一视频重复审核不会重复烧 token。"""
    import hashlib

    h = hashlib.sha1(model.encode())
    for item in picked:
        try:
            with open(item['path'], 'rb') as f:
                h.update(f.read(65536))
        except OSError:
            h.update(str(item['path']).encode())
    return h.hexdigest()


_VLM_CACHE: dict[str, dict] = {}
_VLM_TTL = 3600          # 1 小时
_VLM_CACHE_MAX = 64


def _remember_vlm(key: str, result: dict) -> None:
    if len(_VLM_CACHE) >= _VLM_CACHE_MAX:
        oldest = min(_VLM_CACHE.items(), key=lambda kv: kv[1]['at'])[0]
        _VLM_CACHE.pop(oldest, None)
    _VLM_CACHE[key] = {'at': time.time(), 'result': result}


def _parse_vision_json(text: str) -> dict | None:
    """容忍模型输出里的 ```json 包裹或前后废话。"""
    if not text:
        return None
    raw = text.strip()
    if raw.startswith('```'):
        raw = raw.strip('`')
        if raw.lower().startswith('json'):
            raw = raw[4:]
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
        if not isinstance(data, dict):
            return None
        data.setdefault('risky', [])
        data.setdefault('tags', [])
        data.setdefault('scene', '')
        data.setdefault('text_in_image', '')
        return data
    except json.JSONDecodeError:
        return None


# ---------------- 工具 2.1：视觉风险（CLIP，本地兜底） ----------------
_CLIP_CACHE: dict[str, Any] = {}


def _load_clip():
    if 'model' in _CLIP_CACHE:
        return _CLIP_CACHE['model'], _CLIP_CACHE.get('prep')
    try:
        import clip  # type: ignore
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        model, prep = clip.load('ViT-B/32', device=device)
        _CLIP_CACHE.update(model=model, prep=prep, device=device, torch=torch,
                           prompts=clip.tokenize([
                               'a normal campus life video',
                               'pornographic or nude content',
                               'bloody violence or fighting',
                               'weapons or dangerous objects',
                               'gambling or drugs or alcohol',
                           ]).to(device))
        return model, prep
    except Exception as e:
        logger.info('CLIP 不可用，视觉风险走降级信号: %s', e)
        _CLIP_CACHE['model'] = None
        return None, None


def clip_risk(frame_paths: list[str]) -> dict:
    """返回每个风险类别的最高相似度（0~1）。无 CLIP 时给出中性信号。"""
    labels = ['normal', 'porn', 'violence', 'weapon', 'addiction']
    model, prep = _load_clip()
    if model is None or not frame_paths:
        return {'ok': False, 'available': False, 'scores': {}, 'labels': labels,
                'note': 'CLIP 未安装，视觉侧使用关键词/LLM 判断'}

    try:
        import torch
        from PIL import Image
        device = _CLIP_CACHE['device']
        prompts = _CLIP_CACHE['prompts']
        best = {name: 0.0 for name in labels}
        with torch.no_grad():
            for p in frame_paths[:8]:
                try:
                    img = prep(Image.open(p)).unsqueeze(0).to(device)
                except Exception:
                    continue
                logits, _ = model.encode_image(img), None
                probs = (logits @ _CLIP_CACHE['model'].encode_text(prompts).t()).softmax(-1)[0]
                for i, name in enumerate(labels):
                    best[name] = max(best[name], float(probs[i]))
        return {'ok': True, 'available': True, 'scores': best, 'labels': labels}
    except Exception as e:
        logger.warning('CLIP 推理失败: %s', e)
        return {'ok': False, 'available': False, 'scores': {}, 'labels': labels,
                'note': f'CLIP 推理异常: {e}'}


# ---------------- 工具 3：文字风险（OCR，可选） ----------------
_OCR_CACHE: dict[str, Any] = {}


def _imread_unicode(path: str):
    """Unicode 安全地读图。

    为什么不用 cv2.imread：它在 Windows 上对非 ASCII 路径（如 C:\\Users\\联想\\...）
    返回 None 而不是抛异常，会让下游 `.shape` 直接崩掉。
    这里用 numpy + imdecode 绕开路径编码问题。
    """
    try:
        import cv2
        import numpy as np

        with open(path, 'rb') as f:
            data = np.frombuffer(f.read(), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.debug('读图失败 %s: %s', path, e)
        return None


def ocr_text(frame_paths: list[str]) -> dict:
    if not frame_paths:
        return {'ok': False, 'text': '', 'available': False}
    if 'reader' not in _OCR_CACHE:
        try:
            import easyocr
            _OCR_CACHE['reader'] = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
        except Exception as e:
            logger.info('EasyOCR 不可用: %s', e)
            _OCR_CACHE['reader'] = None
    reader = _OCR_CACHE['reader']
    if reader is None:
        return {'ok': False, 'text': '', 'available': False,
                'note': 'EasyOCR 未安装'}
    chunks: list[str] = []
    errors: list[str] = []
    try:
        for p in frame_paths[:8]:
            # 关键：传 ndarray 而不是路径。
            # easyocr 内部同样用 cv2.imread，中文路径下会拿到 None 并抛
            # "'NoneType' object has no attribute 'shape'"。
            img = _imread_unicode(p)
            if img is None:
                errors.append(f'无法读取 {os.path.basename(p)}')
                continue
            for item in reader.readtext(img, detail=0) or []:
                if item and str(item).strip():
                    chunks.append(str(item).strip())
    except Exception as e:
        return {'ok': False, 'text': ' '.join(chunks), 'available': True,
                'note': str(e), 'errors': errors}
    text = ' '.join(dict.fromkeys(chunks))[:2000]
    return {'ok': bool(text), 'text': text, 'available': True,
            'frames_read': len(frame_paths[:8]) - len(errors),
            'errors': errors}


# ---------------- 工具 4：关键词风险 ----------------
def keyword_risk(*texts: str) -> dict:
    blob = ' '.join(t for t in texts if t)
    hits: dict[str, list[str]] = {}
    for cat, words in RISK_LEXICON.items():
        found = [w for w in words if w in blob]
        if found:
            hits[cat] = found
    return {'ok': bool(hits), 'hits': hits, 'text_len': len(blob)}


# ---------------- 工具 5：RAG 规范检索 ----------------
def rule_lookup(query: str, top_k: int = 3) -> dict:
    hits = rag.retrieve(query, top_k)
    return {'ok': bool(hits), 'hits': hits}


# ---------------- 工具 6：文件名/元信息风险 ----------------
_SUSPICIOUS_NAME = re.compile(r'(18|成年|色情|裸|赌博|代考|代刷|外挂|破解)', re.I)


def meta_risk(filename: str) -> dict:
    flags = _SUSPICIOUS_NAME.findall(filename or '')
    return {'ok': bool(flags), 'flags': sorted(set(flags)), 'filename': filename}


# ---------------- 工具清单（OpenAI tools 协议） ----------------
TOOL_SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'extract_frames',
        'description': '从本地视频文件等间隔抽取若干关键帧，供后续视觉分析',
        'parameters': {'type': 'object', 'properties': {
            'video_path': {'type': 'string', 'description': '视频绝对路径'},
            'max_frames': {'type': 'integer', 'description': '抽帧数量，默认 15'}},
            'required': ['video_path']}}},
    {'type': 'function', 'function': {
        'name': 'vlm_describe',
        'description': '用多模态模型逐帧理解画面，输出场景描述、风险证据与内容标签（可解释性最强）',
        'parameters': {'type': 'object', 'properties': {
            'frame_paths': {'type': 'array', 'items': {'type': 'string'},
                            'description': '关键帧图片路径列表'},
            'filename': {'type': 'string', 'description': '原始文件名，用于辅助判断'},
            'max_frames': {'type': 'integer', 'description': '最多分析几帧，默认 3'}},
            'required': ['frame_paths']}}},
    {'type': 'function', 'function': {
        'name': 'frame_select',
        'description': '从抽出的帧里按清晰度与去重挑选最有信息量的关键帧，降低后续模型成本',
        'parameters': {'type': 'object', 'properties': {
            'frame_paths': {'type': 'array', 'items': {'type': 'string'}},
            'keep': {'type': 'integer', 'description': '保留帧数，默认 4'}},
            'required': ['frame_paths']}}},
    {'type': 'function', 'function': {
        'name': 'clip_risk',
        'description': '对抽出的关键帧做视觉风险打分（正常/色情/暴力/武器/成瘾）',
        'parameters': {'type': 'object', 'properties': {
            'frame_paths': {'type': 'array', 'items': {'type': 'string'},
                            'description': '关键帧图片路径列表'}},
            'required': ['frame_paths']}}},
    {'type': 'function', 'function': {
        'name': 'ocr_text',
        'description': '识别关键帧里的文字内容，用于发现画面上的违规文字',
        'parameters': {'type': 'object', 'properties': {
            'frame_paths': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['frame_paths']}}},
    {'type': 'function', 'function': {
        'name': 'rule_lookup',
        'description': '检索校园内容规范条款与历史判例，为判定提供依据',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': '检索关键词或内容摘要'},
            'top_k': {'type': 'integer', 'description': '返回条款数，默认 3'}},
            'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'keyword_risk',
        'description': '对文本做违规关键词匹配，作为模型判断的交叉验证',
        'parameters': {'type': 'object', 'properties': {
            'texts': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['texts']}}},
    {'type': 'function', 'function': {
        'name': 'meta_risk',
        'description': '检查文件名中的可疑信号（如 18+/裸/破解 等）',
        'parameters': {'type': 'object', 'properties': {
            'filename': {'type': 'string'}}, 'required': ['filename']}}},
]

REGISTRY = {
    'extract_frames': lambda **kw: extract_frames(kw.get('video_path', ''),
                                                  kw.get('max_frames')),
    'frame_select': lambda **kw: {'ok': bool(kw.get('frame_paths')),
                                  'frames': frame_select(kw.get('frame_paths') or [],
                                                         kw.get('keep', 4))},
    'vlm_describe': lambda **kw: vlm_describe(kw.get('frame_paths') or [],
                                              kw.get('filename', ''),
                                              kw.get('max_frames', 3)),
    'clip_risk': lambda **kw: clip_risk(kw.get('frame_paths') or []),
    'ocr_text': lambda **kw: ocr_text(kw.get('frame_paths') or []),
    'rule_lookup': lambda **kw: rule_lookup(kw.get('query', ''), kw.get('top_k', 3)),
    'keyword_risk': lambda **kw: keyword_risk(*(kw.get('texts') or [])),
    'meta_risk': lambda **kw: meta_risk(kw.get('filename', '')),
}


def call_tool(name: str, arguments: dict) -> dict:
    fn = REGISTRY.get(name)
    if not fn:
        return {'ok': False, 'error': f'未知工具: {name}'}
    try:
        started = time.time()
        result = fn(**(arguments or {}))
        result['_elapsed_ms'] = int((time.time() - started) * 1000)
        return result
    except Exception as e:
        logger.exception('工具执行失败: %s', name)
        return {'ok': False, 'error': str(e)}
