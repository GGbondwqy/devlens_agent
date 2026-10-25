"""知识库检索工具：项目文档 / 历史故障库的轻量 RAG。

定位：零依赖的 TF-IDF 兜底实现。接口与向量检索同构（query -> chunks），
接真实模型后可平滑替换为 embedding + 向量库（如 PGVector），
工具层与 Agent 循环无需改动。
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from devlens.tools.base import Tool, ToolResult
from devlens.tools.sandbox import Sandbox

_TOKEN_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]|[0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "for", "on", "with"}


def tokenize(text: str) -> list[str]:
    """极简中英混合分词：英文按标识符切，中文按单字（bigram 效果更好，单字够用）。"""
    return [t.lower() for t in _TOKEN_RX.findall(text) if t.lower() not in _STOP]


class DocKnowledgeBase:
    """文档知识库：把 md/txt 文档按段落切块，TF-IDF 打分检索。"""

    def __init__(self, roots: list[Path]) -> None:
        self.chunks: list[dict[str, Any]] = []
        self._df: dict[str, int] = {}
        for root in roots:
            if root.exists():
                self._ingest(root)
        self._n = max(1, len(self.chunks))

    def _ingest(self, root: Path) -> None:
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".md", ".txt"} or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for para in re.split(r"\n\s*\n", text):
                para = para.strip()
                if len(para) < 20:
                    continue
                tokens = tokenize(para)
                if not tokens:
                    continue
                self.chunks.append(
                    {
                        "source": path.name,
                        "text": para,
                        "tf": _count(tokens),
                        "tokens": tokens,
                    }
                )
        for c in self.chunks:
            for term in c["tf"]:
                self._df[term] = self._df.get(term, 0) + 1

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        q = _count(tokenize(query))
        if not q or not self.chunks:
            return []
        scored = []
        for c in self.chunks:
            score = 0.0
            for term, qf in q.items():
                tf = c["tf"].get(term, 0)
                if tf:
                    idf = math.log(self._n / (1 + self._df.get(term, 0))) + 1
                    score += qf * tf * idf
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        return [{"source": c["source"], "text": c["text"], "score": round(s, 3)} for s, c in scored[:top_k]]


def _count(tokens: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tokens:
        out[t] = out.get(t, 0) + 1
    return out


class SearchDocsTool(Tool):
    name = "search_docs"
    description = (
        "检索项目知识库（设计文档、历史故障记录、运维手册），"
        "返回最相关的文档片段。适合查历史故障、约定规范。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索问题，如：优惠券 金额为负 历史故障"},
        },
        "required": ["query"],
    }

    def __init__(self, kb: DocKnowledgeBase) -> None:
        self.kb = kb

    def execute(self, **kwargs: Any) -> ToolResult:
        hits = self.kb.search(str(kwargs["query"]))
        if not hits:
            return ToolResult(ok=True, output="知识库无相关内容")
        blocks = [
            f"[{i+1}] 来源: {h['source']} (相关度 {h['score']})\n{h['text'][:500]}"
            for i, h in enumerate(hits)
        ]
        return ToolResult(ok=True, output="\n\n".join(blocks), meta={"hits": len(hits)})
