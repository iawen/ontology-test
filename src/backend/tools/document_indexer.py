"""
Schema Optimizer v4 - 文档分块与 BM25 索引模块
===============================================
改进点（基于建议1）：
  1. 使用 BM25 (关键词统计) 替代 Embedding 向量检索
  2. 保留结构化分块（Word/PDF/Excel/TXT）
  3. 保留分块重叠机制，避免边界信息丢失
  4. 保留降级解析链（fitz → pdfplumber → 纯文本兜底）
"""

import os
import re
import json
import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import List, Dict, Optional, Tuple

# BM25 库
try:
    from rank_bm25 import BM25Okapi
except ImportError:
    raise ImportError("请安装 rank_bm25: pip install rank_bm25")

import numpy as np


# ============================================================
# 文档解析（与 v3.1 相同，保留降级链和重叠分块）
# ============================================================

def parse_document(path: Path, chunk_size: int = 800, chunk_overlap: int = 150) -> List[Dict]:
    """
    解析文档为结构化分块列表。

    Args:
        path: 文档路径
        chunk_size: 每块目标字符数
        chunk_overlap: 块间重叠字符数

    Returns:
        [{"chunk_id": str, "content": str, "metadata": {"source": str, ...}}]
    """
    ext = path.suffix.lower()
    if ext == ".docx":
        return _parse_docx(path, chunk_size, chunk_overlap)
    elif ext == ".pdf":
        return _parse_pdf_with_fallback(path, chunk_size, chunk_overlap)
    elif ext == ".xlsx":
        return _parse_xlsx(path, chunk_size, chunk_overlap)
    elif ext == ".txt":
        return _parse_txt(path, chunk_size, chunk_overlap)
    else:
        return []


def _split_text_with_overlap(text: str, chunk_size: int, chunk_overlap: int, metadata: dict) -> List[Dict]:
    """按字符数切分文本，支持重叠"""
    if len(text) <= chunk_size:
        return [{
            "chunk_id": hashlib.md5(f"{metadata.get('source','')}:{text[:50]}".encode()).hexdigest()[:12],
            "content": text,
            "metadata": metadata.copy()
        }]

    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for sep in ["。", "！", "？", "\n\n", "\n", ". ", "! ", "? "]:
                pos = text.rfind(sep, start, end)
                if pos != -1 and pos > start + chunk_size // 2:
                    end = pos + len(sep)
                    break
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunk_id = hashlib.md5(f"{metadata.get('source','')}:{idx}:{chunk_text[:30]}".encode()).hexdigest()[:12]
            chunks.append({
                "chunk_id": chunk_id,
                "content": chunk_text,
                "metadata": metadata.copy()
            })
        start = end - chunk_overlap if end < len(text) else end
        idx += 1
    return chunks


def _parse_docx(path: Path, chunk_size: int, chunk_overlap: int) -> List[Dict]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        paragraphs = []
        current_section = ""
        for para in root.findall(".//w:p", ns):
            texts = [t.text for t in para.findall(".//w:t", ns) if t.text]
            text = "".join(texts).strip()
            if not text:
                continue
            style_elems = para.findall(".//w:pStyle", ns)
            style = style_elems[0].get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") if style_elems else ""
            if "Heading" in style:
                current_section = text
            paragraphs.append(text)
        full_text = "\n".join(paragraphs)
        metadata = {"source": path.name, "type": "docx", "section": current_section}
        return _split_text_with_overlap(full_text, chunk_size, chunk_overlap, metadata)
    except Exception as e:
        print(f"[Warning] 解析 DOCX 失败: {e}")
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            metadata = {"source": path.name, "type": "docx_fallback"}
            return _split_text_with_overlap(text, chunk_size, chunk_overlap, metadata)
        except:
            return []


def _parse_pdf_with_fallback(path: Path, chunk_size: int, chunk_overlap: int) -> List[Dict]:
    # 尝试 PyMuPDF
    try:
        import fitz
        doc = fitz.open(str(path))
        full_text = ""
        for page_num in range(len(doc)):
            text = doc[page_num].get_text().strip()
            if text:
                full_text += f"\n--- Page {page_num+1} ---\n{text}"
        doc.close()
        if full_text.strip():
            metadata = {"source": path.name, "type": "pdf_fitz"}
            return _split_text_with_overlap(full_text, chunk_size, chunk_overlap, metadata)
    except:
        pass

    # 尝试 pdfplumber
    try:
        import pdfplumber
        full_text = ""
        with pdfplumber.open(str(path)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = (page.extract_text() or "").strip()
                if text:
                    full_text += f"\n--- Page {page_num+1} ---\n{text}"
        if full_text.strip():
            metadata = {"source": path.name, "type": "pdf_plumber"}
            return _split_text_with_overlap(full_text, chunk_size, chunk_overlap, metadata)
    except:
        pass

    # 纯文本兜底
    try:
        raw = path.read_bytes()
        for enc in ["utf-8", "gb18030", "latin-1"]:
            try:
                text = raw.decode(enc, errors="ignore")
                text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
                text = re.sub(r"\s+", " ", text)
                if len(text.strip()) > 100:
                    metadata = {"source": path.name, "type": "pdf_raw_text"}
                    return _split_text_with_overlap(text, chunk_size, chunk_overlap, metadata)
            except:
                continue
    except:
        pass
    print(f"[Warning] 所有 PDF 解析方式均失败: {path.name}")
    return []


def _parse_xlsx(path: Path, chunk_size: int, chunk_overlap: int) -> List[Dict]:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        chunks = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            headers = [str(h) if h else f"col_{j}" for j, h in enumerate(rows[0])]
            lines = []
            for row in rows[1:]:
                row_dict = {headers[j]: str(row[j]) if j < len(row) and row[j] else "" for j in range(len(headers))}
                lines.append(json.dumps(row_dict, ensure_ascii=False))
            full_text = f"Sheet: {sheet_name}\nHeaders: {', '.join(headers)}\n" + "\n".join(lines)
            metadata = {"source": path.name, "sheet": sheet_name, "type": "excel"}
            chunks.extend(_split_text_with_overlap(full_text, chunk_size, chunk_overlap, metadata))
        wb.close()
        return chunks
    except Exception as e:
        print(f"[Warning] 解析 XLSX 失败: {e}")
        return []


def _parse_txt(path: Path, chunk_size: int, chunk_overlap: int) -> List[Dict]:
    try:
        text = path.read_text(encoding="utf-8")
        metadata = {"source": path.name, "type": "text"}
        return _split_text_with_overlap(text, chunk_size, chunk_overlap, metadata)
    except Exception as e:
        print(f"[Warning] 解析 TXT 失败: {e}")
        return []


# ============================================================
# BM25 索引（替代 Embedding 检索）
# ============================================================

class DocumentIndex:
    """
    文档索引（基于 BM25 关键词检索）
    轻量级，无需向量库和 GPU，适合术语密集的 Schema 优化场景。
    """

    def __init__(self, tokenizer=None):
        """
        Args:
            tokenizer: 分词函数，接收字符串返回词列表。若未提供，使用默认中文+英文分词。
        """
        self.chunks: List[Dict] = []
        self.bm25: Optional[BM25Okapi] = None
        self.tokenizer = tokenizer or self._default_tokenizer

    def _default_tokenizer(self, text: str) -> List[str]:
        """
        默认分词：提取中文词语、英文单词、数字，并转为小写。
        针对 Schema 优化场景，保留专有名词（如表名、字段名）。
        """
        # 匹配中文连续字符、英文单词、数字
        tokens = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]+|\d+", text.lower())
        return tokens

    def add_chunks(self, chunks: List[Dict]):
        """添加文档分块并构建 BM25 索引"""
        if not chunks:
            return
        self.chunks.extend(chunks)
        # 重新构建 BM25 索引（所有块）
        tokenized_corpus = [self.tokenizer(c["content"]) for c in self.chunks]
        self.bm25 = BM25Okapi(tokenized_corpus)

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        BM25 检索：返回与 query 最相关的 top_k 个文档分块。

        Args:
            query: 查询文本（通常是资产描述）
            top_k: 返回数量

        Returns:
            [{"chunk_id": str, "content": str, "score": float, "metadata": dict}]
        """
        if self.bm25 is None or len(self.chunks) == 0:
            return []

        tokenized_query = self.tokenizer(query)
        scores = self.bm25.get_scores(tokenized_query)
        # 获取 top_k 索引
        top_indices = np.argsort(scores)[-top_k:][::-1]

        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                chunk = self.chunks[idx]
                results.append({
                    "chunk_id": chunk["chunk_id"],
                    "content": chunk["content"],
                    "score": float(scores[idx]),
                    "metadata": chunk.get("metadata", {}),
                })
        return results

    def build_context(self, query: str, top_k: int = 5, max_chars: int = 8000) -> str:
        """
        构建优化上下文：检索相关文档片段并拼接。

        Args:
            query: 查询文本
            top_k: 检索数量
            max_chars: 最大字符数

        Returns:
            拼接后的文档上下文字符串
        """
        results = self.search(query, top_k)
        parts = []
        used = 0
        for r in results:
            content = r["content"]
            header = f"\n--- 文档片段 (来源: {r['metadata'].get('source', '?')}, 相关度: {r['score']:.2f}) ---\n"
            remaining = max_chars - used - len(header)
            if remaining <= 0:
                break
            truncated = content[:remaining]
            parts.append(header + truncated)
            used += len(header) + len(truncated)
        return "".join(parts)