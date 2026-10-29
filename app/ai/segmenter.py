"""视频切片器：把一条视频按时间切成若干"片"，每片取最有代表性的帧。

为什么需要切片（而不是整片抽几帧）：
    整片均匀抽 3 帧，等于只看开头、中间、结尾三张照片。
    如果违规只出现在第 12 秒，很可能一张都没抽到；即使抽到了，
    也说不清"问题发生在第几秒"。
    切片之后每段单独判定，才能给出「第 8–12 秒：画面含二维码引流」这种定位。

切片策略（两级）：
    1. 按时间等分：duration / segment_seconds → N 段，超过 max_segments 就合并；
    2. 每段内部按**运动量**挑帧：沿用旧版 utils/vitalframe.py 的思路
       （相邻帧灰度差绝对值之和最大者），取该段里"信息量最大"的那一帧，
       而不是简单取中点——静止封面段会白看，转场段才不会漏。

产物：
    plan_segments() 返回纯元数据（不含图片），便于先给前端画时间轴；
    extract_segment_frames() 才真正解码抽帧（较慢，按需调用）。
"""
from __future__ import annotations

import logging
import os
import time

from ..config import Config

logger = logging.getLogger(__name__)


def _probe(video_path: str) -> dict:
    """读取视频基础信息。读不到时返回空 dict。"""
    try:
        import cv2
    except ImportError:
        return {}
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {}
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()
    duration = (total / fps) if fps > 0 else 0.0
    return {'fps': round(fps, 2), 'frames': total, 'width': width,
            'height': height, 'duration': round(duration, 2)}


def plan_segments(video_path: str,
                  segment_seconds: int | None = None,
                  max_segments: int | None = None) -> dict:
    """规划切片方案（不抽帧，只算时间区间）。

    返回：
        {'ok': True, 'duration': 12.5, 'count': 4,
         'segments': [{'index':0,'start':0.0,'end':4.0,'mid':2.0}, ...],
         'meta': {...}}
    """
    seg_sec = int(segment_seconds or Config.AUDIT_SEGMENT_SECONDS or 4)
    seg_max = int(max_segments or Config.AUDIT_SEGMENT_MAX or 6)
    seg_sec = max(1, seg_sec)
    seg_max = max(1, seg_max)

    info = _probe(video_path)
    if not info or not info.get('duration'):
        return {'ok': False, 'error': '无法读取视频时长', 'segments': [],
                'count': 0, 'duration': 0.0, 'meta': info}

    duration = float(info['duration'])
    # 太短的视频就一整段；切出来段数超过上限时，放大段长合并
    raw_count = max(1, int(round(duration / seg_sec)))
    if raw_count > seg_max:
        raw_count = seg_max
    span = duration / raw_count

    segments = []
    for i in range(raw_count):
        start = round(i * span, 2)
        end = round(duration if i == raw_count - 1 else (i + 1) * span, 2)
        segments.append({'index': i, 'start': start, 'end': end,
                         'mid': round((start + end) / 2, 2)})

    return {'ok': True, 'duration': round(duration, 2), 'count': len(segments),
            'segment_seconds': round(span, 2), 'segments': segments, 'meta': info}


def _pick_frame_by_motion(cap, start_frame: int, end_frame: int, fps: float,
                          out_dir: str, tag: str, max_scan: int = 240):
    """在一段里按运动量挑代表帧。

    做法与旧版 vitalframe.py 一致：逐帧与前一帧做 absdiff 求和，
    取差异最大的一帧。这样跳过静止画面，抓住"真正发生了什么"的那一帧。

    返回 dict 或 None。
    """
    import cv2
    import numpy as np

    total = max(1, end_frame - start_frame)
    stride = max(1, total // max_scan)      # 扫描步长，避免长视频太慢
    best = None
    best_diff = -1.0
    prev_gray = None
    idx = start_frame

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    while idx < end_frame:
        ok, frame = cap.read()
        if not ok:
            break
        # 按 stride 跳跃：只用于算运动量，命中最佳帧时再单独存图
        if (idx - start_frame) % stride == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                diff = float(np.sum(cv2.absdiff(gray, prev_gray)))
                if diff > best_diff:
                    best_diff = diff
                    best = frame.copy()
                    best_idx = idx
            else:
                best = frame.copy()
                best_idx = idx
            prev_gray = gray
        idx += 1

    if best is None:
        return None

    # Unicode 安全写图（cv2.imwrite 在中文路径下会静默失败）
    path = os.path.join(out_dir, f'{tag}.jpg')
    try:
        ok_enc, buf = cv2.imencode('.jpg', best)
        if not ok_enc:
            return None
        with open(path, 'wb') as f:
            f.write(buf.tobytes())
    except OSError as e:
        logger.warning('写切片帧失败 %s: %s', path, e)
        return None

    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None

    return {'path': path, 'frame_index': best_idx,
            'ts': round(best_idx / fps, 2) if fps else 0.0,
            'motion': round(best_diff, 1) if best_diff >= 0 else 0.0}


def extract_segment_frames(video_path: str, plan: dict | None = None,
                           picks: int | None = None) -> dict:
    """按切片方案抽每段的代表帧。

    返回：
        {'ok': True, 'segments': [{'index','start','end','frames':[{path,ts,motion}]}]}
    """
    plan = plan or plan_segments(video_path)
    if not plan.get('ok'):
        return {'ok': False, 'error': plan.get('error') or '切片规划失败',
                'segments': []}

    try:
        import cv2
    except ImportError:
        return {'ok': False, 'error': 'opencv 未安装', 'segments': []}

    info = plan.get('meta') or {}
    fps = float(info.get('fps') or 0)
    per_pick = max(1, int(picks or Config.AUDIT_SEGMENT_PICKS or 1))
    if not fps:
        return {'ok': False, 'error': '无法读取帧率', 'segments': []}

    out_dir = os.path.join(Config.TEMP_DIR, f'segs_{int(time.time() * 1000)}')
    os.makedirs(out_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {'ok': False, 'error': '视频无法解码', 'segments': []}

    try:
        result = []
        for seg in plan['segments']:
            start_frame = int(seg['start'] * fps)
            end_frame = max(start_frame + 1, int(seg['end'] * fps))
            picked = _pick_frame_by_motion(
                cap, start_frame, end_frame, fps, out_dir, f'seg{seg["index"]:02d}')
            frames = [picked] if picked else []
            result.append({**seg, 'frames': frames,
                           'reps': len(frames)})
        ok = any(s['frames'] for s in result)
        return {'ok': ok, 'dir': out_dir, 'segments': result,
                'error': '' if ok else '所有切片都没抽到可用帧'}
    finally:
        cap.release()


def cleanup_segment_dir(dir_path: str) -> None:
    """清理切片临时目录（抽帧产物，审核完即可删）。"""
    if not dir_path:
        return
    try:
        import shutil
        if os.path.isdir(dir_path) and Config.TEMP_DIR in dir_path:
            shutil.rmtree(dir_path, ignore_errors=True)
    except Exception:
        logger.debug('清理切片目录失败', exc_info=True)
