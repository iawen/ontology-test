"""
Schema Optimizer v3 - 文档分块与向量化模块
==========================================
改进点：
  1. 结构化分块：按文档类型（Word段落/Excel行/PDF页）智能分块
  2. Embedding 向量化：对每个分块生成向量，支持语义检索
  3. 按需检索：优化某资产时只检索相关文档片段，避免全量传输

设计原则：
  - 轻量级：不依赖外部向量数据库，使用内存 + numpy
  - 可插拔：Embedding 模型可替换（默认用 LLM text-embedding）
  - 容错：向量化失败时降级为关键词匹配
"""

import os
import re
import json
import hashlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import Optional, List, Dict, Tuple

import numpy as np


# ============================================================
# 文档解析
# ============================================================

def parse_document(path: Path) -> List[Dict]:
    """
    解析文档为结构化分块列表。

    Returns:
        [{"chunk_id": str, "content": str, "metadata": {"source": str, "page": int, "type": str}}]
    """
    ext = path.suffix.lower()
    if ext == ".docx":
        return _parse_docx(path)
    elif ext == ".pdf":
        return _parse_pdf(path)
    elif ext == ".xlsx":
        return _parse_xlsx(path)
    elif ext == ".txt":
        return _parse_txt(path)
    else:
        return []


def _parse_docx(path: Path) -> List[Dict]:
    """解析 Word 文档，按段落分块"""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    chunks = []
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        current_section = ""
        for para in root.findall(".//w:p", ns):
            texts = [t.text for t in para.findall(".//w:t", ns) if t.text]
            text = "".join(texts).strip()
            if not text:
                continue
            # 检测标题样式
            style = para.find(".//w:pStyle", ns)
            if style is not None and "Heading" in (style.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") or ""):
                current_section = text
            chunk_id = hashlib.md5(f"{path.name}:{text[:50]}".encode()).hexdigest()[:12]
            chunks.append({
                "chunk_id": chunk_id,
                "content": text,
                "metadata": {
                    "source": path.name,
                    "section": current_section,
                    "type": "paragraph",
                }
            })
    except Exception as e:
        print(f"[Warning] 解析 DOCX 失败: {e}")
    return chunks


def _parse_pdf(path: Path) -> List[Dict]:
    """解析 PDF 文档，按页分块"""
    chunks = []
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text().strip()
            if not text:
                continue
            # 长页进一步按段落切分
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            for i, para in enumerate(paragraphs):
                chunk_id = hashlib.md5(f"{path.name}:p{page_num}:{i}".encode()).hexdigest()[:12]
                chunks.append({
                    "chunk_id": chunk_id,
                    "content": para,
                    "metadata": {
                        "source": path.name,
                        "page": page_num + 1,
                        "type": "pdf_paragraph",
                    }
                })
        doc.close()
    except ImportError:
        # 降级：尝试 pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    text = (page.extract_text() or "").strip()
                    if not text:
                        continue
                    chunk_id = hashlib.md5(f"{path.name}:p{page_num}".encode()).hexdigest()[:12]
                    chunks.append({
                        "chunk_id": chunk_id,
                        "content": text,
                        "metadata": {
                            "source": path.name,
                            "page": page_num + 1,
                            "type": "pdf_page",
                        }
                    })
        except ImportError:
            print("[Warning] 需要安装 PyMuPDF 或 pdfplumber 来解析 PDF")
    except Exception as e:
        print(f"[Warning] 解析 PDF 失败: {e}")
    return chunks


def _parse_xlsx(path: Path) -> List[Dict]:
    """解析 Excel 文档，按 sheet+行组分块"""
    chunks = []
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            headers = [str(h) if h else "" for h in rows[0]]
            # 每 20 行打包一个 chunk
            batch_size = 20
            for i in range(1, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                lines = []
                for row in batch:
                    row_dict = {headers[j]: str(row[j]) if j < len(row) and row[j] else "" for j in range(len(headers))}
                    lines.append(json.dumps(row_dict, ensure_ascii=False))
                content = f"Sheet: {sheet_name}\nHeaders: {', '.join(headers)}\nRows:\n" + "\n".join(lines)
                chunk_id = hashlib.md5(f"{path.name}:{sheet_name}:{i}".encode()).hexdigest()[:12]
                chunks.append({
                    "chunk_id": chunk_id,
                    "content": content,
                    "metadata": {
                        "source": path.name,
                        "sheet": sheet_name,
                        "type": "excel_rows",
                    }
                })
        wb.close()
    except Exception as e:
        print(f"[Warning] 解析 XLSX 失败: {e}")
    return chunks


def _parse_txt(path: Path) -> List[Dict]:
    """解析纯文本，按段落分块"""
    chunks = []
    try:
        text = path.read_text(encoding="utf-8")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        for i, para in enumerate(paragraphs):
            chunk_id = hashlib.md5(f"{path.name}:{i}".encode()).hexdigest()[:12]
            chunks.append({
                "chunk_id": chunk_id,
                "content": para,
                "metadata": {
                    "source": path.name,
                    "type": "text_paragraph",
                }
            })
    except Exception as e:
        print(f"[Warning] 解析 TXT 失败: {e}")
    return chunks


# ============================================================
# 向量化与语义检索
# ============================================================

class DocumentIndex:
    """文档向量索引（内存级，轻量）"""

    def __init__(self, embedding_func=None):
        """
        Args:
            embedding_func: 文本向量化函数 (text) -> np.array
                            默认使用关键词哈希（降级方案）
        """
        self.chunks: List[Dict] = []
        self.vectors: Optional[np.ndarray] = None
        self.embedding_func = embedding_func or self._default_embedding

    def _default_embedding(self, text: str) -> np.ndarray:
        """降级方案：基于关键词的哈希向量（256维）"""
        vec = np.zeros(256)
        # 简单中文+英文分词
        words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]+|\d+", text.lower())
        for word in words:
            h = int(hashlib.md5(word.encode()).hexdigest(), 16) % 256
            vec[h] += 1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def add_chunks(self, chunks: List[Dict]):
        """添加文档分块并生成向量"""
        self.chunks.extend(chunks)
        new_vectors = np.array([self.embedding_func(c["content"]) for c in chunks])
        if self.vectors is None:
            self.vectors = new_vectors
        else:
            self.vectors = np.vstack([self.vectors, new_vectors])

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        语义检索：返回与 query 最相关的 top_k 个文档分块。

        Args:
            query: 查询文本（通常是资产描述）
            top_k: 返回数量

        Returns:
            [{"chunk_id": str, "content": str, "score": float, "metadata": dict}]
        """
        if self.vectors is None or len(self.chunks) == 0:
            return []

        query_vec = self.embedding_func(query)
        # 余弦相似度
        scores = self.vectors @ query_vec
        top_indices = np.argsort(scores)[-top_k:][::-1]

        results = []
        for idx in top_indices:
            if scores[idx] > 0.01:  # 过滤极低分
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


# ============================================================
# LLM Embedding 函数（可选，需配置）
# ============================================================

def create_llm_embedding_func(client, model: str = "text-embedding-v1"):
    """创建基于 LLM API 的 embedding 函数"""
    def embed(text: str) -> np.ndarray:
        try:
            resp = client.embeddings.create(model=model, input=text[:8000])
            return np.array(resp.data[0].embedding)
        except Exception:
            # 降级到默认哈希向量
            vec = np.zeros(256)
            words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z_]+|\d+", text.lower())
            for word in words:
                h = int(hashlib.md5(word.encode()).hexdigest(), 16) % 256
                vec[h] += 1.0
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
    return embed
