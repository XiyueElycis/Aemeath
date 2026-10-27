"""心动模式 · 记忆向量检索（可插拔）。

对外只有一件事：给定一句「当前输入」和一批候选记忆文本，返回最相关的下标。
检索后端通过 :class:`Retriever` 协议插拔，默认实现两种：

- :class:`HashingRetriever` —— 纯 Python、零第三方依赖、可离线可单测。用
  「单字 + 相邻双字」字符 n-gram 做哈希，语料内就地估 IDF 后取余弦。
- :class:`EmbeddingRetriever` —— 调用 OpenAI 兼容的 ``/embeddings`` 端点取稠密
  向量。任何失败（无密钥 / 网络 / 维度不符）都会自动降级到本地实现，
  保证「检索可以变准，但不能因为联网失败而失忆」。

:func:`build_retriever` 是唯一装配入口：设置里没配 embedding 就直接给本地实现，
配了才尝试远程。日后接别的向量库，只需新增一个实现类并在工厂里登记。
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Any, Callable, Dict, List, Optional, Sequence

# 哈希桶数量：几十条短记忆下冲突率可接受，且计算量恒定。
DIM = 512

# ASCII 侧只取长度 ≥2 的整词（事件 id、英文专名）；单个字母噪声太大
_ASCII_TOKEN = re.compile(r"[a-zA-Z0-9_]{2,}")
# 汉字区间（含扩展 A 区），只对汉字做字符 n-gram
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# 远程 embedding 的缓存上限（条）。记忆条目本身有总量上限，这里留足余量。
_CACHE_LIMIT = 512


# --------------------------------------------------------------------------- #
# 协议
# --------------------------------------------------------------------------- #
class Retriever:
    """检索后端协议。

    实现方只需保证：分数不为正的下标不返回；结果按相关度从高到低；最多
    ``top_k`` 个。``boosts`` 是逐条权重（显著度），可为 None。
    """

    name = "base"

    def rank(self, query: str, documents: Sequence[str], top_k: int,
             boosts: Optional[Sequence[float]] = None) -> List[int]:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# 本地实现：字符 n-gram 哈希 + 语料内 TF-IDF
# --------------------------------------------------------------------------- #
def tokenize(text: str) -> List[str]:
    """把文本切成检索用 token。

    中文没有天然空格，用「单字 + 相邻双字」的字符 n-gram 代替分词：既零依赖，
    又能让「雨伞」命中「雨天」，对短句比粗粒度分词更稳。ASCII 侧取整词。
    """
    text = text or ""
    han = [ch for ch in text if _HAN.match(ch)]
    tokens: List[str] = list(han)
    tokens += [han[i] + han[i + 1] for i in range(len(han) - 1)]
    tokens += _ASCII_TOKEN.findall(text.lower())
    return tokens


def _bucket(token: str, dim: int) -> int:
    """把 token 稳定映射到 ``[0, dim)``。

    刻意用 :func:`zlib.crc32` 而非内置 ``hash()``：后者受 ``PYTHONHASHSEED``
    影响，跨进程结果不一致，会让「同一存档两次检索结果不同」。
    """
    return zlib.crc32(token.encode("utf-8")) % dim


def embed(text: str, dim: int = DIM) -> Dict[int, float]:
    """把文本编码成稀疏词频向量（未归一化，未加权）。"""
    vec: Dict[int, float] = {}
    for token in tokenize(text):
        key = _bucket(token, dim)
        vec[key] = vec.get(key, 0.0) + 1.0
    return vec


def _normalize(vec: Dict[int, float]) -> Dict[int, float]:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm <= 0.0:
        return {}
    return {k: v / norm for k, v in vec.items()}


def _dot(a: Dict[int, float], b: Dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _recent(total: int, top_k: int) -> List[int]:
    """检索不出结果时的退化策略：按时间取最近若干条。"""
    start = max(0, total - top_k)
    return list(range(start, total))


class HashingRetriever(Retriever):
    """默认检索后端：离线可用、无外部依赖、结果可复现。"""

    name = "hashing"

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def rank(self, query: str, documents: Sequence[str], top_k: int,
             boosts: Optional[Sequence[float]] = None) -> List[int]:
        total = len(documents)
        if total <= 0 or top_k <= 0:
            return []
        if top_k >= total:
            return list(range(total))

        query_vec = _normalize(embed(query, self.dim))
        if not query_vec:
            return _recent(total, top_k)

        doc_vecs = [embed(doc or "", self.dim) for doc in documents]
        # 就地估计 IDF：df 按「桶」统计，与向量维度对齐
        df: Dict[int, int] = {}
        for vec in doc_vecs:
            for key in vec:
                df[key] = df.get(key, 0) + 1
        weighted = []
        for vec in doc_vecs:
            tf_idf = {k: v * (math.log((1.0 + total) / (1.0 + df.get(k, 0))) + 1.0)
                      for k, v in vec.items()}
            weighted.append(_normalize(tf_idf))

        scored = []
        for idx, vec in enumerate(weighted):
            score = _dot(query_vec, vec)
            if boosts is not None:
                score *= float(boosts[idx])
            if score > 0.0:
                scored.append((score, idx))

        if not scored:
            return _recent(total, top_k)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [idx for _score, idx in scored[:top_k]]


# --------------------------------------------------------------------------- #
# 远程实现：OpenAI 兼容 /embeddings
# --------------------------------------------------------------------------- #
def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


def _default_post(url: str, json: Any, headers: Any, timeout: float) -> Any:
    """默认 HTTP POST：延迟 import httpx，本地默认路径完全不碰网络栈。"""
    import httpx

    return httpx.post(url, json=json, headers=headers, timeout=timeout)


class EmbeddingRetriever(Retriever):
    """调用远程 embedding 端点做检索；失败即降级到本地实现。

    :param post_fn: 测试注入用的 HTTP POST；默认 ``httpx.post``。
    :param fallback: 降级后端，默认 :class:`HashingRetriever`。
    :param disabled: 一旦远程调用失败就置位，避免每轮都等一次超时。
    """

    name = "embedding"

    def __init__(self, *, provider: str = "", model: str = "",
                 api_base: str = "", api_key: str = "",
                 timeout: float = 20.0, post_fn: Optional[Callable[..., Any]] = None,
                 fallback: Optional[Retriever] = None) -> None:
        self.provider = provider
        self.model = model
        self.api_base = (api_base or "").rstrip("/")
        self._api_key = api_key or ""
        self.timeout = timeout
        self._post = post_fn or _default_post
        self._fallback = fallback or HashingRetriever()
        # 远程不可用时永久降级；下一次 rank 直接走本地，不再发请求
        self.disabled = not (self.api_base and self.model and self._api_key)
        self.failure = "" if not self.disabled else "embedding 未配置完整"
        self._cache: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------ #
    def _vectors(self, texts: Sequence[str]) -> Optional[List[List[float]]]:
        """批量取向量；任意异常返回 None 交由调用方降级。"""
        missing = [t for t in texts if t not in self._cache]
        if missing:
            try:
                resp = self._post(
                    f"{self.api_base}/embeddings",
                    json={"model": self.model, "input": missing},
                    headers={"Authorization": f"Bearer {self._api_key}",
                             "Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                if getattr(resp, "status_code", 200) >= 400:
                    self._disable(f"HTTP {resp.status_code}")
                    return None
                data = resp.json().get("data") or []
                if len(data) != len(missing):
                    self._disable("返回向量数量不符")
                    return None
                for text, row in zip(missing, data):
                    vec = [float(x) for x in (row.get("embedding") or [])]
                    if not vec:
                        self._disable("返回空向量")
                        return None
                    self._cache[text] = vec
                if len(self._cache) > _CACHE_LIMIT:
                    # 简单裁剪：丢掉最早写入的一半，避免缓存无界增长
                    for key in list(self._cache)[: len(self._cache) // 2]:
                        self._cache.pop(key, None)
            except Exception as exc:  # noqa: BLE001 — 任何失败都降级
                self._disable(exc.__class__.__name__)
                return None
        out = []
        for text in texts:
            vec = self._cache.get(text)
            if vec is None:
                return None
            out.append(vec)
        return out

    def _disable(self, reason: str) -> None:
        self.disabled = True
        self.failure = reason

    # ------------------------------------------------------------------ #
    def rank(self, query: str, documents: Sequence[str], top_k: int,
             boosts: Optional[Sequence[float]] = None) -> List[int]:
        if self.disabled:
            return self._fallback.rank(query, documents, top_k, boosts)
        total = len(documents)
        if total <= 0 or top_k <= 0:
            return []
        if top_k >= total:
            return list(range(total))

        vecs = self._vectors([query, *documents])
        if vecs is None:
            return self._fallback.rank(query, documents, top_k, boosts)
        query_vec, doc_vecs = vecs[0], vecs[1:]

        scored = []
        for idx, vec in enumerate(doc_vecs):
            score = _cosine(query_vec, vec)
            if boosts is not None:
                score *= float(boosts[idx])
            if score > 0.0:
                scored.append((score, idx))
        if not scored:
            return _recent(total, top_k)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [idx for _score, idx in scored[:top_k]]


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
def build_retriever(settings: Optional[Any] = None,
                    post_fn: Optional[Callable[..., Any]] = None) -> Retriever:
    """按设置装配检索后端。

    设置里 ``embedding`` 段缺 api_base / model / api_key 任一项时直接返回本地
    实现——**不联网探测**，因为「启动时先等一次超时」是不可接受的体验。
    """
    cfg: Dict[str, Any] = {}
    if settings is not None:
        try:
            cfg = settings.get_embedding() or {}
        except Exception:  # noqa: BLE001 — 设置只读旁路
            cfg = {}
    if not (cfg.get("api_base") and cfg.get("model") and cfg.get("api_key")):
        return HashingRetriever()
    return EmbeddingRetriever(
        provider=str(cfg.get("provider") or ""),
        model=str(cfg.get("model") or ""),
        api_base=str(cfg.get("api_base") or ""),
        api_key=str(cfg.get("api_key") or ""),
        timeout=float(cfg.get("timeout") or 20.0),
        post_fn=post_fn,
    )


__all__ = [
    "Retriever",
    "HashingRetriever",
    "EmbeddingRetriever",
    "build_retriever",
    "tokenize",
    "embed",
]
