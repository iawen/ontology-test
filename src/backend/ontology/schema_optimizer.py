import hashlib
import inspect
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from configs.global_config import Cfg, client
from tools.db import get_db


_ALLOWED_EXTENSIONS = {".doc", ".docx", ".pdf", ".xlsx"}
_DOC_CONTEXT_LIMIT = 45000


async def _emit_progress(progress_callback, **status):
    if not progress_callback:
        return
    result = progress_callback(status)
    if inspect.isawaitable(result):
        await result


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _json_obj(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "document"
    return re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name)


def _optimization_dir(scenario_id: str) -> Path:
    return Path(Cfg.scenarios_root) / scenario_id / "optimization_docs"


def _read_document_text(path: Path, ext: str) -> str:
    if ext == ".docx":
        return _read_docx_text(path)
    if ext == ".doc":
        return _read_legacy_doc_text(path)
    if ext == ".pdf":
        return _read_pdf_text(path)
    if ext == ".xlsx":
        return _read_xlsx_text(path)
    raise ValueError(f"不支持的文件类型: {ext}")


def _read_docx_text(path: Path) -> str:
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespaces):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespaces)).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)
    except Exception as exc:
        raise ValueError(f"DOCX 解析失败: {exc}") from exc


def _read_legacy_doc_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore") or raw.decode("gb18030", errors="ignore")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", text)
        return text.strip()
    except Exception as exc:
        raise ValueError(f"DOC 解析失败: {exc}") from exc


def _read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF 解析依赖 pypdf 未安装，请先安装后端依赖") from exc
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception as exc:
        raise ValueError(f"PDF 解析失败: {exc}") from exc


def _read_xlsx_text(path: Path) -> str:
    namespaces = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.findall(".//main:si", namespaces):
                    shared_strings.append("".join(node.text or "" for node in item.findall(".//main:t", namespaces)))

            lines = []
            sheet_names = sorted(name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", name))
            for sheet_name in sheet_names:
                root = ElementTree.fromstring(archive.read(sheet_name))
                lines.append(f"[{Path(sheet_name).stem}]")
                for row in root.findall(".//main:row", namespaces):
                    values = []
                    for cell in row.findall("main:c", namespaces):
                        cell_value = cell.find("main:v", namespaces)
                        value = cell_value.text if cell_value is not None else ""
                        if cell.get("t") == "s" and value.isdigit():
                            index = int(value)
                            value = shared_strings[index] if index < len(shared_strings) else value
                        values.append(value)
                    if any(values):
                        lines.append("\t".join(values))
            return "\n".join(lines).strip()
    except Exception as exc:
        raise ValueError(f"XLSX 解析失败: {exc}") from exc


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _load_unreviewed_schema(scenario_id: str) -> dict:
    conn = get_db()
    classes = conn.execute(
        """SELECT id, scenario_id, name_cn, description, properties, csv_file, primary_key, is_reviewed
           FROM schema_classes WHERE scenario_id=? AND COALESCE(is_reviewed, 0)=0 ORDER BY id""",
        (scenario_id,),
    ).fetchall()
    relationships = conn.execute(
        "SELECT * FROM schema_relationships WHERE scenario_id=? AND COALESCE(is_reviewed, 0)=0 ORDER BY id",
        (scenario_id,),
    ).fetchall()
    metrics = conn.execute(
        "SELECT * FROM metrics WHERE scenario_id=? AND COALESCE(is_reviewed, 0)=0 ORDER BY sort_order, id",
        (scenario_id,),
    ).fetchall()
    concepts = conn.execute(
        "SELECT * FROM concepts WHERE scenario_id=? AND COALESCE(is_reviewed, 0)=0 ORDER BY level, sort_order, id",
        (scenario_id,),
    ).fetchall()
    conn.close()

    return {
        "classes": [
            {
                **_row_to_dict(row),
                "properties": _json_list(row.get("properties", "[]")),
            }
            for row in classes
        ],
        "relationships": [_row_to_dict(row) for row in relationships],
        "metrics": [
            {
                **_row_to_dict(row),
                "dimensions": _json_list(row.get("dimensions", "[]")),
                "required_dimensions": _json_list(row.get("required_dimensions", "[]")),
            }
            for row in metrics
        ],
        "concepts": [_row_to_dict(row) for row in concepts],
    }


def _load_documents(scenario_id: str, file_ids: list[str] | None) -> list[dict]:
    conn = get_db()
    if file_ids:
        placeholders = ",".join("?" for _ in file_ids)
        rows = conn.execute(
            f"SELECT * FROM schema_optimization_files WHERE scenario_id=? AND id IN ({placeholders}) ORDER BY uploaded_at DESC",
            (scenario_id, *file_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM schema_optimization_files WHERE scenario_id=? ORDER BY uploaded_at DESC",
            (scenario_id,),
        ).fetchall()
    conn.close()
    return [_row_to_dict(row) for row in rows]


def _build_document_context(docs: list[dict]) -> str:
    parts = []
    used = 0
    for doc in docs:
        content = (doc.get("content_text") or "").strip()
        if not content:
            continue
        header = f"\n\n--- 文档: {doc.get('original_filename') or doc.get('filename')} ---\n"
        remaining = _DOC_CONTEXT_LIMIT - used - len(header)
        if remaining <= 0:
            break
        parts.append(header + content[:remaining])
        used += len(header) + min(len(content), remaining)
    return "".join(parts).strip()


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _build_prompt(schema_data: dict, doc_context: str) -> str:
    return f"""
你是企业本体与指标体系建模专家。请根据用户上传的业务文档，优化当前尚未人工审核通过的 schema 对象。

硬性规则：
1. 只允许优化 is_reviewed=0 的 classes、relationships、metrics、concepts。
2. classes 中不得输出 fields 字段，也不要基于文档生成或修改 fields。
3. 不要输出已审核对象，不要删除对象。
4. 如果优化已有对象，请保留它的 id；如果新增对象，请给出稳定英文 id。
5. 所有返回对象的 is_reviewed 必须为 false，等待用户人工审核。
6. 只返回 JSON，不要 markdown，不要解释文字。

返回 JSON 结构：
{{
  "summary": "本次优化摘要",
  "classes": [{{"id":"...","name_cn":"...","description":"...","properties":["..."],"csv_file":"...","primary_key":"...","is_reviewed":false}}],
  "relationships": [{{"id":1,"source":"...","target":"...","type":"...","join_key":"...","description":"...","is_reviewed":false}}],
  "metrics": [{{"id":"...","name":"...","description":"...","category":"...","target_class":"...","calculation":"...","formula":"...","dimensions":["..."],"required_dimensions":["..."],"filters_hint":"...","chart_type":"bar","sort_order":0,"is_reviewed":false}}],
  "concepts": [{{"id":"...","name":"...","description":"...","parent_id":"","level":0,"concept_type":"entity","related_class":"...","sort_order":0,"is_reviewed":false}}]
}}

当前未审核 schema 对象（classes 已刻意移除 fields）：
{json.dumps(schema_data, ensure_ascii=False, indent=2)}

业务文档内容：
{doc_context}
""".strip()


async def _call_optimizer_llm(schema_data: dict, doc_context: str) -> dict:
    response = await client.chat.completions.create(
        model=Cfg.model_name,
        messages=[
            {"role": "system", "content": "你只输出可解析 JSON。"},
            {"role": "user", "content": _build_prompt(schema_data, doc_context)},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content or "{}"
    return _extract_json(content)


def _upsert_class(conn, scenario_id: str, item: dict):
    class_id = str(item.get("id", "")).strip()
    if not class_id:
        return False
    exists = conn.execute("SELECT id FROM schema_classes WHERE id=? AND scenario_id=?", (class_id, scenario_id)).fetchone()
    properties = json.dumps(_json_list(item.get("properties", [])), ensure_ascii=False)
    if exists:
        conn.execute(
            """UPDATE schema_classes
                    SET name_cn=?, description=?, properties=?, csv_file=?, primary_key=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=? AND COALESCE(is_reviewed, 0)=0""",
            (
                item.get("name_cn", ""),
                item.get("description", ""),
                properties,
                item.get("csv_file", ""),
                item.get("primary_key", ""),
                class_id,
                scenario_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO schema_classes
                    (id, scenario_id, name_cn, description, properties, fields, csv_file, primary_key, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (
                class_id,
                scenario_id,
                item.get("name_cn", ""),
                item.get("description", ""),
                properties,
                "[]",
                item.get("csv_file", ""),
                item.get("primary_key", ""),
            ),
        )
    return True


def _upsert_relationship(conn, scenario_id: str, item: dict):
    rel_id = item.get("id")
    values = (
        item.get("source", ""),
        item.get("target", ""),
        item.get("type", ""),
        item.get("join_key", ""),
        item.get("description", ""),
    )
    if rel_id:
        conn.execute(
            """UPDATE schema_relationships
                    SET source=?, target=?, type=?, join_key=?, description=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=? AND COALESCE(is_reviewed, 0)=0""",
            (*values, rel_id, scenario_id),
        )
    else:
        conn.execute(
            """INSERT INTO schema_relationships
                    (scenario_id, source, target, type, join_key, description, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (scenario_id, *values),
        )
    return True


def _upsert_metric(conn, scenario_id: str, item: dict):
    metric_id = str(item.get("id", "")).strip()
    if not metric_id:
        return False
    exists = conn.execute("SELECT id FROM metrics WHERE id=? AND scenario_id=?", (metric_id, scenario_id)).fetchone()
    values = (
        item.get("name", ""),
        item.get("description", ""),
        item.get("category", ""),
        item.get("target_class", ""),
        item.get("calculation", ""),
        item.get("formula", ""),
        json.dumps(_json_list(item.get("dimensions", [])), ensure_ascii=False),
        json.dumps(_json_list(item.get("required_dimensions", [])), ensure_ascii=False),
        item.get("filters_hint", ""),
        item.get("chart_type", "bar") or "bar",
        int(item.get("sort_order", 0) or 0),
    )
    if exists:
        conn.execute(
            """UPDATE metrics
               SET name=?, description=?, category=?, target_class=?, calculation=?, formula=?,
                   dimensions=?, required_dimensions=?, filters_hint=?, chart_type=?, sort_order=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=? AND COALESCE(is_reviewed, 0)=0""",
            (*values, metric_id, scenario_id),
        )
    else:
        conn.execute(
            """INSERT INTO metrics
               (id, scenario_id, name, description, category, target_class, calculation, formula,
                     dimensions, required_dimensions, filters_hint, chart_type, sort_order, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (metric_id, scenario_id, *values),
        )
    return True


def _upsert_concept(conn, scenario_id: str, item: dict):
    concept_id = str(item.get("id", "")).strip()
    if not concept_id:
        return False
    exists = conn.execute("SELECT id FROM concepts WHERE id=? AND scenario_id=?", (concept_id, scenario_id)).fetchone()
    values = (
        item.get("name", ""),
        item.get("description", ""),
        item.get("parent_id", ""),
        int(item.get("level", 0) or 0),
        item.get("concept_type", "entity") or "entity",
        item.get("related_class", ""),
        int(item.get("sort_order", 0) or 0),
    )
    if exists:
        conn.execute(
            """UPDATE concepts
                    SET name=?, description=?, parent_id=?, level=?, concept_type=?, related_class=?, sort_order=?, is_reviewed=0, updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=? AND COALESCE(is_reviewed, 0)=0""",
            (*values, concept_id, scenario_id),
        )
    else:
        conn.execute(
            """INSERT INTO concepts
                    (id, scenario_id, name, description, parent_id, level, concept_type, related_class, sort_order, is_reviewed, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (concept_id, scenario_id, *values),
        )
    return True


def _apply_optimization(scenario_id: str, result: dict) -> dict:
    conn = get_db()
    counts = {"classes": 0, "relationships": 0, "metrics": 0, "concepts": 0}
    try:
        for item in result.get("classes", []) or []:
            if _upsert_class(conn, scenario_id, item):
                counts["classes"] += 1
        for item in result.get("relationships", []) or []:
            if _upsert_relationship(conn, scenario_id, item):
                counts["relationships"] += 1
        for item in result.get("metrics", []) or []:
            if _upsert_metric(conn, scenario_id, item):
                counts["metrics"] += 1
        for item in result.get("concepts", []) or []:
            if _upsert_concept(conn, scenario_id, item):
                counts["concepts"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        from modules.schema import _sync_schema_files

        _sync_schema_files(scenario_id)
    except Exception:
        pass
    return counts


def list_optimization_files(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        """SELECT id, scenario_id, filename, original_filename, file_ext, content_hash, size, uploaded_at
           FROM schema_optimization_files WHERE scenario_id=? ORDER BY uploaded_at DESC""",
        (scenario_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_optimization_files(scenario_id: str, files: list):
    target_dir = _optimization_dir(scenario_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    conn = get_db()
    try:
        for file in files:
            original = _safe_filename(file.filename or "document")
            ext = Path(original).suffix.lower()
            if ext not in _ALLOWED_EXTENSIONS:
                raise ValueError(f"暂不支持的文件类型: {original}")
            file_id = uuid.uuid4().hex
            stored_name = f"{file_id}_{original}"
            path = target_dir / stored_name
            with path.open("wb") as handle:
                shutil.copyfileobj(file.file, handle)
            content = _read_document_text(path, ext)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT INTO schema_optimization_files
                   (id, scenario_id, filename, original_filename, file_ext, file_path, content_text, content_hash, size)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (file_id, scenario_id, stored_name, original, ext, str(path), content, content_hash, path.stat().st_size),
            )
            saved.append({"id": file_id, "filename": stored_name, "original_filename": original, "file_ext": ext, "size": path.stat().st_size})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"status": "ok", "files": saved}


def delete_optimization_file(scenario_id: str, file_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT file_path FROM schema_optimization_files WHERE id=? AND scenario_id=?",
        (file_id, scenario_id),
    ).fetchone()
    if not row:
        conn.close()
        raise FileNotFoundError("文件不存在")
    file_path = row["file_path"]
    conn.execute("DELETE FROM schema_optimization_files WHERE id=? AND scenario_id=?", (file_id, scenario_id))
    conn.commit()
    conn.close()
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
    return {"status": "ok"}


def list_optimization_runs(scenario_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schema_optimization_runs WHERE scenario_id=? ORDER BY created_at DESC LIMIT 20",
        (scenario_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        data = dict(row)
        data["file_ids"] = _json_list(data.get("file_ids", "[]"))
        data["changes_json"] = _json_obj(data.get("changes_json", "{}"))
        result.append(data)
    return result


def create_optimization_run(scenario_id: str, file_ids: list[str] | None = None) -> str:
    run_id = uuid.uuid4().hex
    conn = get_db()
    conn.execute(
        """INSERT INTO schema_optimization_runs (id, scenario_id, file_ids, status)
           VALUES (?,?,?,?)""",
        (run_id, scenario_id, json.dumps(_json_list(file_ids), ensure_ascii=False), "running"),
    )
    conn.commit()
    conn.close()
    return run_id


async def run_schema_optimization(scenario_id: str, file_ids: list[str] | None = None, run_id: str | None = None, progress=None):
    file_ids = _json_list(file_ids)
    run_id = run_id or create_optimization_run(scenario_id, file_ids)
    await _emit_progress(progress, running=True, phase="documents", progress=5, total=100, message="正在读取优化文档", run_id=run_id)
    docs = _load_documents(scenario_id, file_ids or None)
    if not docs:
        raise ValueError("请先上传用于 Schema 优化的文档")
    doc_context = _build_document_context(docs)
    if not doc_context:
        raise ValueError("已上传文档没有可读取内容")

    await _emit_progress(progress, running=True, phase="schema", progress=20, total=100, message="正在读取未审核 Schema 对象", run_id=run_id)
    schema_data = _load_unreviewed_schema(scenario_id)
    if not any(schema_data.values()):
        raise ValueError("当前没有 is_reviewed=0 的对象可优化")

    conn = get_db()
    conn.execute(
        "UPDATE schema_optimization_runs SET file_ids=? WHERE id=? AND scenario_id=?",
        (json.dumps([doc["id"] for doc in docs], ensure_ascii=False), run_id, scenario_id),
    )
    conn.commit()
    conn.close()

    try:
        await _emit_progress(progress, running=True, phase="llm", progress=40, total=100, message="正在调用大模型生成优化建议", run_id=run_id)
        llm_result = await _call_optimizer_llm(schema_data, doc_context)
        await _emit_progress(progress, running=True, phase="apply", progress=75, total=100, message="正在写回优化结果", run_id=run_id)
        counts = _apply_optimization(scenario_id, llm_result)
        changes = {"applied": counts, "llm_result": llm_result}
        conn = get_db()
        conn.execute(
            """UPDATE schema_optimization_runs
               SET status='success', summary=?, changes_json=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=?""",
            (llm_result.get("summary", ""), json.dumps(changes, ensure_ascii=False), run_id, scenario_id),
        )
        conn.commit()
        conn.close()
        await _emit_progress(progress, running=False, phase="done", progress=100, total=100, message="Schema 优化完成", run_id=run_id, result={"summary": llm_result.get("summary", ""), "applied": counts})
        return {"status": "success", "run_id": run_id, "summary": llm_result.get("summary", ""), "applied": counts}
    except Exception as exc:
        conn = get_db()
        conn.execute(
            """UPDATE schema_optimization_runs
               SET status='failed', error=?, finished_at=CURRENT_TIMESTAMP
               WHERE id=? AND scenario_id=?""",
            (str(exc), run_id, scenario_id),
        )
        conn.commit()
        conn.close()
        await _emit_progress(progress, running=False, phase="error", progress=100, total=100, message=f"Schema 优化失败: {exc}", run_id=run_id)
        raise RuntimeError(f"Schema 优化失败: {exc}") from exc