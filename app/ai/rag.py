"""RAG：校园内容规范 + 历史判例的检索增强。

工程取舍（29 天内最务实的选择）：
- **不引入向量库依赖**：用「字符 n-gram TF-IDF + 标签命中」做混合检索，
  零外部服务、可离线、可解释（能说出"命中哪几条规则"），对校园规范这种
  短文本、强关键词的场景效果足够，且省掉 embedding API 费用。
- 预留 upgrade path：`EmbeddingBackend` 接口留空，接向量库时只换这一处。

数据来源：
  1. 内置规范条款（RULEBOOK，可换成读文件/数据库）；
  2. audit_cases 表里人工复核沉淀的判例（越用越准的闭环）。
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

from ..db import use_cursor, DatabaseUnavailable

logger = logging.getLogger(__name__)

# ---------------- 内置规范库（可替换为外部文件/数据库） ----------------
RULEBOOK: list[dict] = [
    {'id': 'R1-1', 'cat': '政治敏感', 'title': '危害国家安全与政治敏感内容',
     'text': '禁止发布涉及危害国家安全、泄露国家秘密、破坏民族团结、宣扬恐怖主义的内容。',
     'keywords': '政治 分裂 暴恐 邪教 国旗 领导人 敏感'},
    {'id': 'R2-1', 'cat': '色情低俗', 'title': '色情与性暗示内容',
     'text': '禁止发布色情、裸露、性行为暗示内容；泳装、健身、舞蹈等正常场景需结合语境判断，不得一刀切。',
     'keywords': '色情 裸露 性 低俗 擦边 泳装 健身'},
    {'id': 'R2-2', 'cat': '色情低俗', 'title': '礼仪性场景豁免',
     'text': '成人礼、校庆典礼等正式校园仪式属正常内容，不因"礼"字或着装被判为低俗。',
     'keywords': '成人礼 典礼 仪式 校庆 毕业'},
    {'id': 'R3-1', 'cat': '暴力危险', 'title': '暴力血腥与危险行为',
     'text': '禁止展示真实暴力、血腥、自残、虐待动物；实验演示、体育竞技等需标注保护措施。',
     'keywords': '暴力 血腥 打架 自残 危险 实验 明火'},
    {'id': 'R4-1', 'cat': '违法违规', 'title': '违法违规与不良诱导',
     'text': '禁止赌博、毒品、烟草、酒精的展示与推广；禁止诱导未成年人充值打赏。',
     'keywords': '赌博 毒品 烟 酒 打赏 充值 未成年'},
    {'id': 'R5-1', 'cat': '侵权抄袭', 'title': '著作权与肖像权',
     'text': '转载他人作品需授权与署名；出现他人清晰面部需取得同意，公共场合远景除外。',
     'keywords': '转载 搬运 抄袭 授权 肖像 水印'},
    {'id': 'R6-1', 'cat': '广告营销', 'title': '商业推广与站外引流',
     'text': '校园社区禁止未经报备的商业推广、二维码引流、代刷代考等灰产信息。',
     'keywords': '广告 推广 二维码 加微信 代刷 代考 兼职'},
    {'id': 'R7-1', 'cat': '校园安全', 'title': '校园场景特别规定',
     'text': '涉及宿舍、实验室、食堂后厨的内容需符合学校管理规定；不得暴露门禁、监控等安防细节。',
     'keywords': '宿舍 实验室 后厨 门禁 监控 翻墙'},
]

CATEGORY_ORDER = ['政治敏感', '色情低俗', '暴力危险', '违法违规', '侵权抄袭', '广告营销', '校园安全']


# ---------------- 文本特征 ----------------
def _tokens(text: str) -> list[str]:
    text = (text or '').lower()
    latin = re.findall(r'[a-z0-9]+', text)
    cjk = re.findall(r'[\u4e00-\u9fff]', text)
    bigrams = [''.join(pair) for pair in zip(cjk, cjk[1:])]
    return latin + cjk + bigrams


@dataclass
class Hit:
    id: str
    title: str
    text: str
    cat: str
    score: float
    source: str = 'rulebook'

    def as_dict(self) -> dict:
        return {'id': self.id, 'title': self.title, 'text': self.text,
                'cat': self.cat, 'score': round(self.score, 3),
                'source': self.source}


class Retriever:
    """TF-IDF 检索器。语料小（几十条），每次请求重建索引也只要 <1ms。"""

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self._df: Counter = Counter()
        self._doc_tokens: list[Counter] = []
        for d in docs:
            toks = Counter(_tokens(f"{d.get('title','')} {d.get('text','')} "
                                   f"{d.get('keywords','')} {d.get('cat','')}"))
            self._doc_tokens.append(toks)
            for t in toks:
                self._df[t] += 1
        self._n = max(1, len(docs))

    def _idf(self, term: str) -> float:
        return math.log((self._n + 1) / (self._df.get(term, 0) + 1)) + 1.0

    def search(self, query: str, top_k: int = 3) -> list[Hit]:
        qtoks = Counter(_tokens(query))
        if not qtoks:
            return []
        scored: list[tuple[float, int]] = []
        for idx, dtoks in enumerate(self._doc_tokens):
            score = 0.0
            for term, qcount in qtoks.items():
                if term in dtoks:
                    score += (1 + math.log(qtf := dtoks[term])) * self._idf(term) * (1 + math.log(qcount))
            if score > 0:
                scored.append((score, idx))
        if not scored:
            return []
        top = max(s for s, _ in scored)
        scored.sort(reverse=True)
        out = []
        for score, idx in scored[:top_k]:
            d = self.docs[idx]
            out.append(Hit(
                id=d.get('id', ''), title=d.get('title', ''), text=d.get('text', ''),
                cat=d.get('cat', ''), score=score / top,
                source=d.get('source', 'rulebook'),
            ))
        return out


def _load_cases(limit: int = 200) -> list[dict]:
    """从 audit_cases 拉历史判例；表不存在时静默返回空。"""
    try:
        with use_cursor() as (_c, cur):
            cur.execute(
                'SELECT id, title, content, labels, verdict FROM audit_cases '
                'ORDER BY id DESC LIMIT %s', (limit,))
            rows = cur.fetchall() or []
    except DatabaseUnavailable:
        return []
    except Exception as e:
        logger.debug('判例读取跳过: %s', e)
        return []
    docs = []
    for r in rows:
        docs.append({
            'id': f"C{r['id']}", 'cat': r.get('labels') or '判例',
            'title': r.get('title') or '历史判例',
            'text': f"{r.get('content') or ''}（人工结论：{r.get('verdict') or '未知'}）",
            'keywords': r.get('labels') or '', 'source': 'case',
        })
    return docs


def build_retriever() -> Retriever:
    return Retriever(RULEBOOK + _load_cases())


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    return [h.as_dict() for h in build_retriever().search(query, top_k)]


def evidence_text(query: str, top_k: int = 3) -> str:
    """给 Prompt 用的检索结果文本。"""
    hits = retrieve(query, top_k)
    if not hits:
        return '（未检索到直接相关条款，请按通用社区规范判断）'
    return '\n'.join(
        f"- [{h['id']}]《{h['title']}》({h['cat']})：{h['text']}" for h in hits)


def save_case(title: str, content: str, labels: str, verdict: str) -> None:
    """人工结论回流成判例 —— 让检索库随使用变强。"""
    try:
        with use_cursor(dict_cursor=False, commit=True) as (_c, cur):
            cur.execute(
                'INSERT INTO audit_cases (title, content, labels, verdict, source) '
                'VALUES (%s,%s,%s,%s,%s)', (title[:255], content, labels[:255], verdict, 'manual'))
    except Exception as e:
        logger.warning('判例写入失败: %s', e)
