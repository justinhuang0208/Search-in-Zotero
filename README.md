# Search in Zotero

在 LaunchBar 內搜尋 Zotero，支援一般關鍵字（SQLite/fzf）與語義搜尋。

## 依賴
- 這個 Action 依賴 `Semantic Search` 專案。
- 預設專案路徑：`/Users/justin/Script/Semantic Search`
- 由 `Contents/config.toml` 的 `paths.semsearch_project` 控制。
- 一般關鍵字模式依賴系統工具：
  - `sqlite3`
  - `jq`
  - `fzf`（若啟用 fzf）

## 設定檔
- 路徑：`Contents/config.toml`
- 主要欄位：
  - `paths.db_path` / `paths.faiss_path`：語義搜尋索引
  - `paths.zotero_db_path` / `paths.zotero_storage_dir`：Zotero 來源
  - `embedding.use_local` / `embedding.model`：嵌入設定
  - `search.semantic_prefix`：語義搜尋前綴字元（預設 `` ` ``）
  - `search.top_k`：語義搜尋召回數
  - `search.semantic_max_docs`：語義搜尋最終文件上限
  - `fzf.enable`：是否啟用一般關鍵字模式
  - `fzf.max_results` / `fzf.max_candidates`：一般模式上限

## 搜尋模式
- 一般模式：直接輸入查詢，走 `default.sh`（SQLite + fzf）。
- 語義模式：輸入 `semantic_prefix + 查詢內容`（例如 `` ` transformer attention ``）。

## 本地/遠端模式
- `embedding.use_local = true`：本地嵌入。
- `embedding.use_local = false`：需要 `OPENROUTER_API_KEY`。

## 注意事項
- 設定檔缺失或格式錯誤時，Action 會回傳錯誤項目，不會繼續搜尋。
