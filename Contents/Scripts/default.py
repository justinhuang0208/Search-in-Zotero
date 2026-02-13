#!/opt/miniconda3/envs/semsearch/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import unquote

SCRIPT_DIR = Path(__file__).resolve().parent
ACTION_CONTENTS_DIR = SCRIPT_DIR.parent
CONFIG_PATH = ACTION_CONTENTS_DIR / "config.toml"

ACTION_TITLE = "Search in Zotero"
SEMSEARCH_PROJECT = Path("/Users/justin/Script/Semantic Search")
SEM_DB_PATH = Path("/Users/justin/Script/Semantic Search/data_index/zotero_pdf_v1.db")
SEM_FAISS_PATH = Path("/Users/justin/Script/Semantic Search/data_index/zotero_pdf_v1.faiss")
USE_LOCAL_EMBEDDING = True
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
SEMANTIC_PREFIX = "`"
SEARCH_TOP_K = 20
SEMANTIC_MAX_DOCS = 8

ZOTERO_DB_SRC = Path("/Users/justin/Zotero/zotero.sqlite")
ZOTERO_STORAGE_DIR = Path("/Users/justin/Zotero/storage")

ENABLE_FZF = True
FZF_MAX_RESULTS = 50
FZF_MAX_CANDIDATES = 1000
FZF_HELPER = SCRIPT_DIR / "default.sh"


def _emit(items: list[dict]) -> None:
    sys.stdout.write(json.dumps(items, ensure_ascii=False))


def _item_error(title: str, subtitle: str) -> dict[str, str]:
    return {"title": title, "subtitle": subtitle, "badge": "Error"}


def _item_info(title: str, subtitle: str) -> dict[str, str]:
    return {"title": title, "subtitle": subtitle}


def _expand_path(value: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    if not expanded:
        return Path("")
    return Path(expanded).resolve()


def _require_table(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"設定檔格式錯誤：缺少 [{key}] 區塊")
    return value


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"設定檔格式錯誤：{key} 必須是非空字串")
    return value.strip()


def _require_int(data: dict, key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"設定檔格式錯誤：{key} 必須是整數")
    return value


def _require_bool(data: dict, key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"設定檔格式錯誤：{key} 必須是布林值")
    return value


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise ValueError(f"找不到設定檔: {CONFIG_PATH}")

    try:
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"讀取設定檔失敗: {exc}")

    if not isinstance(data, dict):
        raise ValueError("設定檔格式錯誤：根節點必須是 table")

    action = _require_table(data, "action")
    paths = _require_table(data, "paths")
    embedding = _require_table(data, "embedding")
    search = _require_table(data, "search")
    fzf = _require_table(data, "fzf")

    return {
        "action_title": _require_str(action, "title"),
        "semsearch_project": _expand_path(_require_str(paths, "semsearch_project")),
        "db_path": _expand_path(_require_str(paths, "db_path")),
        "faiss_path": _expand_path(_require_str(paths, "faiss_path")),
        "zotero_db_path": _expand_path(_require_str(paths, "zotero_db_path")),
        "zotero_storage_dir": _expand_path(_require_str(paths, "zotero_storage_dir")),
        "use_local_embedding": _require_bool(embedding, "use_local"),
        "embedding_model": _require_str(embedding, "model"),
        "semantic_prefix": _require_str(search, "semantic_prefix"),
        "top_k": _require_int(search, "top_k"),
        "semantic_max_docs": _require_int(search, "semantic_max_docs"),
        "fzf_enable": _require_bool(fzf, "enable"),
        "fzf_max_results": _require_int(fzf, "max_results"),
        "fzf_max_candidates": _require_int(fzf, "max_candidates"),
    }


def _apply_config(config: dict) -> None:
    global ACTION_TITLE
    global SEMSEARCH_PROJECT
    global SEM_DB_PATH
    global SEM_FAISS_PATH
    global ZOTERO_DB_SRC
    global ZOTERO_STORAGE_DIR
    global USE_LOCAL_EMBEDDING
    global EMBEDDING_MODEL
    global SEMANTIC_PREFIX
    global SEARCH_TOP_K
    global SEMANTIC_MAX_DOCS
    global ENABLE_FZF
    global FZF_MAX_RESULTS
    global FZF_MAX_CANDIDATES

    ACTION_TITLE = config["action_title"]
    SEMSEARCH_PROJECT = config["semsearch_project"]
    SEM_DB_PATH = config["db_path"]
    SEM_FAISS_PATH = config["faiss_path"]
    ZOTERO_DB_SRC = config["zotero_db_path"]
    ZOTERO_STORAGE_DIR = config["zotero_storage_dir"]
    USE_LOCAL_EMBEDDING = config["use_local_embedding"]
    EMBEDDING_MODEL = config["embedding_model"]
    SEMANTIC_PREFIX = config["semantic_prefix"]
    SEARCH_TOP_K = config["top_k"]
    SEMANTIC_MAX_DOCS = config["semantic_max_docs"]
    ENABLE_FZF = config["fzf_enable"]
    FZF_MAX_RESULTS = config["fzf_max_results"]
    FZF_MAX_CANDIDATES = config["fzf_max_candidates"]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _resolve_api_key() -> str:
    env_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key

    shell_files = [Path.home() / ".zshrc", Path.home() / ".zprofile", Path.home() / ".zshenv"]
    pattern = re.compile(r"^\s*export\s+OPENROUTER_API_KEY\s*=\s*(.+?)\s*$")

    for file_path in shell_files:
        if not file_path.exists():
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for line in lines:
            match = pattern.match(line)
            if not match:
                continue
            raw = _strip_quotes(match.group(1).strip())
            if not raw or raw.startswith("$"):
                continue
            return raw

    return ""


def _resolve_fzf_binary() -> str:
    from_path = shutil.which("fzf")
    if from_path:
        return from_path

    candidates = [
        "/usr/local/bin/fzf",
        "/opt/homebrew/bin/fzf",
        "/bin/fzf",
        "/usr/bin/fzf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return ""


def _validate_semantic_paths() -> str | None:
    if not SEMSEARCH_PROJECT.exists():
        return "找不到 semsearch 專案資料夾。"
    if not SEM_DB_PATH.exists():
        return "找不到資料庫檔案 zotero_pdf_v1.db。"
    if not SEM_FAISS_PATH.exists():
        return "找不到索引檔案 zotero_pdf_v1.faiss。"
    if not ZOTERO_DB_SRC.exists():
        return "找不到 Zotero 資料庫 zotero.sqlite。"
    return None


def _ensure_semsearch_import():
    if str(SEMSEARCH_PROJECT) not in sys.path:
        sys.path.insert(0, str(SEMSEARCH_PROJECT))

    from semsearch.pipeline import search  # type: ignore

    return search


def _run_fzf_mode(fzf_query: str) -> list[dict]:
    if not FZF_HELPER.exists():
        return [_item_error("找不到 fzf helper", f"缺少腳本：{FZF_HELPER}")]

    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    env["ENABLE_FZF"] = "1" if ENABLE_FZF and bool(_resolve_fzf_binary()) else "0"
    env["LB_ZOTERO_STORAGE_DIR"] = str(ZOTERO_STORAGE_DIR)
    env["LB_ZOTERO_DB_PATH"] = str(ZOTERO_DB_SRC)
    env["LB_FZF_MAX_RESULTS"] = str(FZF_MAX_RESULTS)
    env["LB_FZF_MAX_CANDIDATES"] = str(FZF_MAX_CANDIDATES)

    cmd = [str(FZF_HELPER)]
    if fzf_query:
        cmd.append(fzf_query)

    proc = subprocess.run(cmd, text=True, capture_output=True, check=False, env=env)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        return [_item_error("fzf 搜尋失敗", err)]

    output = proc.stdout.strip()
    if not output:
        return [_item_info("No matches", fzf_query)]

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return [_item_error("fzf 回傳格式錯誤", "helper script 輸出不是合法 JSON。")]

    if not isinstance(parsed, list):
        return [_item_error("fzf 回傳格式錯誤", "helper script 輸出不是陣列。")]
    return parsed


def _create_db_snapshot(src_path: Path) -> Path:
    fd, tmp_name = tempfile.mkstemp(prefix="zotero_", suffix=".sqlite")
    os.close(fd)
    tmp_path = Path(tmp_name)

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        src_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(str(tmp_path))
        src_conn.backup(dst_conn)
        dst_conn.commit()
        return tmp_path
    except sqlite3.Error:
        try:
            shutil.copy2(src_path, tmp_path)
            return tmp_path
        except OSError as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise RuntimeError(f"無法建立 Zotero 資料庫快照：{exc}") from exc
    finally:
        if src_conn is not None:
            src_conn.close()
        if dst_conn is not None:
            dst_conn.close()


def _resolve_pdf_path(raw_path: str, attachment_key: str) -> str:
    raw = (raw_path or "").strip()

    if raw.startswith("storage:"):
        rel = raw[len("storage:") :].lstrip("/")
        attachment_dir = ZOTERO_STORAGE_DIR / attachment_key
        if rel:
            candidate = attachment_dir / rel
            if candidate.exists():
                return str(candidate)
        if attachment_dir.is_dir():
            pdfs = sorted(p for p in attachment_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
            if pdfs:
                return str(pdfs[0])
        return ""

    if raw.startswith("file:"):
        file_path = re.sub(r"^file:(//)?", "", raw)
        file_path = unquote(file_path)
        if file_path and not file_path.startswith("/"):
            file_path = "/" + file_path
        return file_path

    if raw:
        return raw

    attachment_dir = ZOTERO_STORAGE_DIR / attachment_key
    if attachment_dir.is_dir():
        pdfs = sorted(p for p in attachment_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        if pdfs:
            return str(pdfs[0])
    return ""


def _load_attachment_meta(attachment_keys: list[str]) -> dict[str, dict[str, str]]:
    keys = [key for key in attachment_keys if key]
    if not keys:
        return {}

    snapshot = _create_db_snapshot(ZOTERO_DB_SRC)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(snapshot))
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in keys)

        sql = f"""
            SELECT
                ai.key AS attachment_key,
                COALESCE(parentTitle.value, '(no title)') AS parent_title,
                COALESCE(authors.value, '') AS authors,
                COALESCE(date.value, '') AS date,
                COALESCE(pub.value, '') AS publication,
                COALESCE(ia.path, '') AS raw_path
            FROM itemAttachments ia
            JOIN items ai ON ai.itemID = ia.itemID
            JOIN items ip ON ip.itemID = ia.parentItemID
            LEFT JOIN itemData idParentTitle ON idParentTitle.itemID = ip.itemID AND idParentTitle.fieldID = 1
            LEFT JOIN itemDataValues parentTitle ON parentTitle.valueID = idParentTitle.valueID
            LEFT JOIN itemData idPub ON idPub.itemID = ip.itemID AND idPub.fieldID = 38
            LEFT JOIN itemDataValues pub ON pub.valueID = idPub.valueID
            LEFT JOIN itemData idDate ON idDate.itemID = ip.itemID AND idDate.fieldID = 6
            LEFT JOIN itemDataValues date ON date.valueID = idDate.valueID
            LEFT JOIN (
                SELECT
                    ic.itemID,
                    GROUP_CONCAT(
                        CASE
                            WHEN c.lastName IS NOT NULL AND c.lastName != '' AND c.firstName IS NOT NULL AND c.firstName != '' THEN c.lastName || ' ' || c.firstName
                            WHEN c.lastName IS NOT NULL AND c.lastName != '' THEN c.lastName
                            ELSE c.firstName
                        END,
                        ', '
                    ) AS value
                FROM itemCreators ic
                JOIN creators c ON c.creatorID = ic.creatorID
                JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
                WHERE ct.creatorType = 'author'
                GROUP BY ic.itemID
            ) authors ON authors.itemID = ip.itemID
            WHERE ai.key IN ({placeholders})
              AND ia.parentItemID IS NOT NULL
        """

        rows = conn.execute(sql, keys).fetchall()
        output: dict[str, dict[str, str]] = {}
        for row in rows:
            key = str(row["attachment_key"]).strip()
            if not key:
                continue
            output[key] = {
                "parent_title": str(row["parent_title"] or "").strip(),
                "authors": str(row["authors"] or "").strip(),
                "date": str(row["date"] or "").strip(),
                "publication": str(row["publication"] or "").strip(),
                "pdf_path": _resolve_pdf_path(str(row["raw_path"] or ""), key),
            }
        return output
    finally:
        if conn is not None:
            conn.close()
        snapshot.unlink(missing_ok=True)


def _parse_md_meta(source_path: str) -> dict[str, str]:
    path = Path(source_path)
    if not path.exists():
        return {}

    output: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return output

    patterns = {
        "attachment_key": re.compile(r"^- Attachment Key:\s*(.+)\s*$"),
        "source_pdf": re.compile(r"^- Source PDF:\s*(.+)\s*$"),
        "zotero_link": re.compile(r"^- Zotero Link:\s*(.+)\s*$"),
    }

    for line in lines[:40]:
        for field, pattern in patterns.items():
            match = pattern.match(line.strip())
            if match:
                output[field] = match.group(1).strip()
        if len(output) == 3:
            break

    return output


def _build_semantic_items(query: str) -> list[dict]:
    path_error = _validate_semantic_paths()
    if path_error:
        return [_item_error("索引設定錯誤", path_error)]

    api_key: str | None = None
    if not USE_LOCAL_EMBEDDING:
        api_key = _resolve_api_key()
        if not api_key:
            return [_item_error("找不到 OPENROUTER_API_KEY", "請先設定 API Key，再重新搜尋。")]

    try:
        search = _ensure_semsearch_import()
    except Exception as exc:
        return [_item_error("無法載入 semsearch 模組", str(exc))]

    try:
        raw_results = search(
            query=query,
            db_path=SEM_DB_PATH,
            faiss_path=SEM_FAISS_PATH,
            api_key=api_key,
            model=EMBEDDING_MODEL,
            top_k=SEARCH_TOP_K,
            use_local_embedding=USE_LOCAL_EMBEDDING,
        )
    except Exception as exc:
        if USE_LOCAL_EMBEDDING:
            fallback_items = _run_fzf_mode(query)
            has_error = any(item.get("badge") == "Error" for item in fallback_items)
            if not has_error:
                fallback_items.insert(
                    0,
                    _item_info("本地語義搜尋暫時不可用，已改用本地關鍵字搜尋。", str(exc)),
                )
                return fallback_items
        return [_item_error("搜尋失敗", str(exc))]

    best_by_source: dict[str, object] = {}
    for result in raw_results:
        source_path = str(getattr(result, "source_path", "")).strip()
        if not source_path:
            continue
        current = best_by_source.get(source_path)
        if current is None or float(getattr(result, "fusion_score", 0.0)) > float(
            getattr(current, "fusion_score", 0.0)
        ):
            best_by_source[source_path] = result

    unique_results = sorted(
        best_by_source.values(),
        key=lambda item: float(getattr(item, "fusion_score", 0.0)),
        reverse=True,
    )[:SEMANTIC_MAX_DOCS]

    if not unique_results:
        return [_item_info("找不到符合結果", "請嘗試不同關鍵字。")]

    attachment_keys: list[str] = []
    for result in unique_results:
        key = str(getattr(result, "doc_id", "")).strip()
        if not key:
            key = Path(str(getattr(result, "source_path", "")).strip()).stem
        if key:
            attachment_keys.append(key)

    meta_by_key = _load_attachment_meta(attachment_keys)

    items: list[dict] = []
    for rank, result in enumerate(unique_results, start=1):
        source_path = str(getattr(result, "source_path", "")).strip()
        title = str(getattr(result, "title", "")).strip() or Path(source_path).stem or "(untitled)"
        score = float(getattr(result, "fusion_score", 0.0))

        attachment_key = str(getattr(result, "doc_id", "")).strip()
        if not attachment_key:
            attachment_key = Path(source_path).stem

        md_meta = _parse_md_meta(source_path) if source_path else {}
        if not attachment_key:
            attachment_key = md_meta.get("attachment_key", "").strip()

        meta = meta_by_key.get(attachment_key, {})
        authors = meta.get("authors", "")
        date = meta.get("date", "")
        publication = meta.get("publication", "")
        paper_title = meta.get("parent_title", "") or title

        meta_parts = [part for part in [authors, date, publication] if part]
        meta_text = " · ".join(meta_parts)
        score_text = f"score={score:.4f}"
        subtitle_parts = [paper_title, score_text]
        if meta_text:
            subtitle_parts.append(meta_text)
        base_subtitle = " | ".join(subtitle_parts)

        pdf_path = meta.get("pdf_path", "").strip()
        if not pdf_path:
            pdf_path = md_meta.get("source_pdf", "").strip()
        file_title = Path(pdf_path).name.strip() if pdf_path else ""
        if not file_title:
            file_title = title

        full_item: dict[str, str] = {
            "title": file_title,
            "subtitle": f"Full PDF | {base_subtitle}",
            "alwaysShowsSubtitle": True,
            "label": str(rank),
        }
        if pdf_path:
            full_item["path"] = pdf_path
            full_item["quickLookURL"] = pdf_path
        else:
            full_item["subtitle"] = f"Full PDF | {base_subtitle} | 找不到 PDF 路徑"
        items.append(full_item)

        zotero_url = f"zotero://open-pdf/library/items/{attachment_key}" if attachment_key else ""
        if not zotero_url:
            zotero_url = md_meta.get("zotero_link", "").strip()

        zotero_item: dict[str, str] = {
            "title": file_title,
            "subtitle": f"Open in Zotero | {base_subtitle}",
            "alwaysShowsSubtitle": True,
        }
        if zotero_url:
            zotero_item["url"] = zotero_url
        else:
            zotero_item["subtitle"] = f"Open in Zotero | {base_subtitle} | 找不到 Zotero 連結"
        items.append(zotero_item)

    return items


def main() -> int:
    try:
        _apply_config(_load_config())
    except ValueError as exc:
        _emit([_item_error("設定檔錯誤", str(exc))])
        return 0

    query = " ".join(part.strip() for part in sys.argv[1:] if part.strip()).strip()
    if not query:
        _emit(
            [
                _item_info(
                    "請輸入搜尋關鍵字",
                    f"一般模式=Zotero 本地關鍵字搜尋（fzf）；前綴 {SEMANTIC_PREFIX} 可切換為語義搜尋。",
                )
            ]
        )
        return 0

    if query.startswith(SEMANTIC_PREFIX):
        query = query[len(SEMANTIC_PREFIX) :].strip()
        if not query:
            _emit([_item_info("請輸入語義搜尋關鍵字", f"請在前綴 {SEMANTIC_PREFIX} 後面輸入查詢內容。")])
            return 0
        _emit(_build_semantic_items(query))
        return 0

    _emit(_run_fzf_mode(query))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
