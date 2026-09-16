"""联网搜索与文件下载智能体。

让大模型在需要时可上网检索解决方案（网页搜索），或下载网络上找到的相关文件
（补丁 / 缺失 DLL / 存档工具等）。

- **搜索**：默认走 DuckDuckGo Lite 网页端点（无需 API Key），回退到 Bing HTML；
  结果回喂给大模型生成自然语言答复。
- **下载**：用 httpx 下载 URL 到游戏目录（或指定文件名），覆盖前自动备份原文件。
- 全程通过 :func:`BaseAgent._report` 上报事件，供 GUI 可视化。

安全边界：下载只落到 game_dir 内（或显式允许的绝对路径），拒绝改写成系统目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote, urlparse

import httpx

from ..base_agent import AgentResult, AgentTask, BaseAgent
from ...sandbox import SandboxViolation
from ...sandbox import io as sxio
from ...sandbox.session import ChangeOp

# 常见浏览器的 UA，避免被搜索引擎拒绝
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# DuckDuckGo Lite 端点（无需 API Key，返回精简 HTML）
_DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
_BING_SEARCH_URL = "https://www.bing.com/search"


@dataclass
class SearchHit:
    """单条搜索结果。"""

    title: str
    url: str
    snippet: str = ""


@dataclass
class WebSearchResult:
    """搜索/下载聚合结果。"""

    query: str = ""
    hits: List[SearchHit] = field(default_factory=list)
    downloaded_path: str = ""
    backup_path: str = ""
    notes: List[str] = field(default_factory=list)


class WebSearchAgent(BaseAgent):
    """联网搜索 / 文件下载智能体。"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__("web_search", config)
        self.capabilities = ["web_search", "online_search", "file_download", "download"]
        self.max_hits = int(self.config.get("max_hits", 5))
        self.timeout = float(self.config.get("timeout", 15))
        self.allow_absolute = bool(self.config.get("allow_absolute", True))

    def _do_initialize(self) -> None:
        self.logger.info("Initializing Web Search Agent")

    async def execute(self, task: AgentTask) -> AgentResult:
        """搜索或下载：按 task.data 里的 operation / url 自动分流。"""
        data = task.data or {}
        operation = data.get("operation") or ""
        url = data.get("url") or ""
        query = data.get("user_message") or data.get("question") or ""
        game_dir = data.get("game_dir") or ""
        dest_name = data.get("file") or ""

        # 下载优先：给了 url 或明确 operation=download
        if url or operation == "download":
            try:
                result = await self._download(
                    self._sandbox(data), url, game_dir, dest_name)
            except SandboxViolation as exc:
                return AgentResult(success=False, message=str(exc), errors=[str(exc)])
            sx = self._sandbox(data)
            if sx is not None and result.success:
                result.message += "（已在沙箱中完成，待你审核应用后才会真正生效）"
            return result
        # 否则视为搜索
        return await self._search(query, game_dir)

    # ------------------------------------------------------------------ #
    async def _search(self, query: str, game_dir: str) -> AgentResult:
        """执行网页搜索，返回结果供大模型二次生成答复。"""
        if not query:
            return AgentResult(success=False, message="搜索内容为空", errors=["query 为空"])
        self._report("phase", f"正在联网搜索：{query}")
        try:
            hits = await self._ddg_search(query)
        except httpx.HTTPError as exc:
            return AgentResult(
                success=False,
                message=f"联网搜索失败：{exc.__class__.__name__}",
                errors=[str(exc)],
            )

        result = WebSearchResult(query=query, hits=hits)
        if not hits:
            result.notes.append("未命中结果，可尝试更具体的关键词")
        return AgentResult(
            success=True,
            data=result,
            message=f"联网搜索完成，共 {len(hits)} 条结果",
        )

    async def _ddg_search(self, query: str) -> List[SearchHit]:
        """调用 DuckDuckGo HTML 端点，解析为 SearchHit 列表（失败抛 HTTPError）。"""
        client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            resp = await client.get(
                _DDG_SEARCH_URL,
                params={"q": query},
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            hits = _parse_ddg(resp.text, self.max_hits)
            if hits:
                return hits
            # 空结果回退 Bing
            resp2 = await client.get(
                _BING_SEARCH_URL,
                params={"q": query},
                headers={"User-Agent": _USER_AGENT},
            )
            resp2.raise_for_status()
            return _parse_bing(resp2.text, self.max_hits)
        finally:
            await client.aclose()

    # ------------------------------------------------------------------ #
    async def _download(self, sx, url: str, game_dir: str, dest_name: str) -> AgentResult:
        """下载 URL 文件到目标路径，覆盖前备份（沙箱模式写入重定向 overlay）。"""
        if not url:
            return AgentResult(success=False, message="下载失败：未提供 URL", errors=["url 为空"])

        target = self._resolve_dest(game_dir, url, dest_name)
        if isinstance(target, str):
            return AgentResult(success=False, message=target, errors=[target])
        if sxio.is_dir(sx, target):
            msg = f"下载目标是一个已存在的目录，请在 file 中指定目录内的具体文件名：{target}"
            return AgentResult(success=False, message=msg, errors=[msg])

        self._report("access", f"正在下载：{url}", str(target))
        backup: str = ""
        try:
            if sxio.exists(sx, target):
                backup = str(self._backup(sx, target))
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
                resp.raise_for_status()
            sxio.write_bytes(sx, target, resp.content, op=ChangeOp.DOWNLOAD)
        except httpx.HTTPError as exc:
            return AgentResult(
                success=False,
                message=f"下载失败：{exc.__class__.__name__}",
                errors=[str(exc)],
            )
        except OSError as exc:
            return AgentResult(success=False, message=f"写入失败：{exc}", errors=[str(exc)])

        result = WebSearchResult(
            query=url,
            downloaded_path=str(target),
            backup_path=backup,
        )
        self._report("modify", f"已下载：{target.name}", str(target))
        return AgentResult(
            success=True,
            data=result,
            message=f"已下载到 {target}",
        )

    def _resolve_dest(self, game_dir: str, url: str, dest_name: str) -> Path | str:
        """解析下载目标路径；优先放 game_dir 内，防路径穿越。"""
        filename = dest_name or self._guess_filename(url)
        if not filename:
            filename = "download.bin"
        root = Path(game_dir).resolve() if game_dir else None

        # 用户给了显式文件名（可能是相对/绝对路径）
        if dest_name:
            p = Path(dest_name)
            if p.is_absolute():
                if not self.allow_absolute:
                    return "已禁用下载到游戏目录外的绝对路径"
                return p
            if root is None:
                return "未设置游戏根目录，请填写游戏根目录或给绝对路径"
            return (root / p).resolve()

        if root is None:
            return "下载需指定游戏根目录（存放位置），或显式提供文件名"
        return (root / filename).resolve()

    @staticmethod
    def _guess_filename(url: str) -> str:
        """从 URL 末尾推断文件名；无法推断返回空串。"""
        path = urlparse(url).path
        name = unquote(path).rstrip("/").rsplit("/", 1)[-1]
        if "." in name:
            return Path(name).name
        return ""

    def _backup(self, sx, target: Path) -> Path:
        """下载覆盖前把已存在的目标备份到 ~/.gamedoctor/backups。

        沙箱模式下备份属授权根外辅助输出，重定向到 side 区，审核应用时写回。
        """
        backup_dir = (
            Path.home()
            / ".gamedoctor"
            / "backups"
            / f"download_{target.stem}"
            / datetime.now().strftime("%Y%m%d-%H%M%S")
        )
        sxio.ensure_dir(sx, backup_dir, side=True)
        dest = backup_dir / target.name
        sxio.copy_file(sx, target, dest, side_dst=True, pristine_src=True)
        return dest


# --------------------------------------------------------------------------- #
# HTML 解析
# --------------------------------------------------------------------------- #
def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _decode_uddg(url: str) -> str:
    """DuckDuckGo 跳转链接形如 //duckduckgo.com/l/?uddg=<url>，解出真实地址。"""
    m = re.search(r"uddg=([^&]+)", url)
    if m:
        return unquote(m.group(1))
    if url.startswith("//"):
        return "https:" + url
    return url


def _parse_ddg(html: str, limit: int) -> List[SearchHit]:
    """解析 DuckDuckGo HTML 结果页。"""
    hits: List[SearchHit] = []
    # 每个结果块由 class="result" 包裹，链接含 result__a，摘要 result__snippet
    for block in re.findall(r'<div[^>]*class="[^"]*result[^"]*"[^>]*>.*?</div>\s*</div>', html, re.DOTALL):
        link_m = re.search(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_m:
            continue
        url = _decode_uddg(link_m.group(1))
        title = _strip_tags(link_m.group(2)).strip()
        snip_m = re.search(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet = _strip_tags(snip_m.group(1)).strip() if snip_m else ""
        if title and url:
            hits.append(SearchHit(title=title, url=url, snippet=snippet))
        if len(hits) >= limit:
            break
    return hits


def _parse_bing(html: str, limit: int) -> List[SearchHit]:
    """解析 Bing HTML 结果页（回退方案）。"""
    hits: List[SearchHit] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL):
        link_m = re.search(r'<a[^>]*href="(http[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_m:
            continue
        title = _strip_tags(link_m.group(2)).strip()
        url = link_m.group(1)
        snip_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = _strip_tags(snip_m.group(1)).strip() if snip_m else ""
        if title and url:
            hits.append(SearchHit(title=title, url=url, snippet=snippet))
        if len(hits) >= limit:
            break
    return hits