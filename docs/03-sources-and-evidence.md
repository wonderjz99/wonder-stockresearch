# 阶段 3：来源存档与证据定位

## 1. 本阶段解决什么问题

阶段 2 的来源只是草稿里的一串元数据，程序从不接触真实字节。这一阶段让程序真正取得材料：用户给出 URL 或本地文件，程序下载/读取原始字节、计算 SHA-256、提取可读文本、归档到 `data/stocks/<ticker>/sources/`，并返回可定位的片段（页码或行号）。之后 `validate`/`finish` 会核对已归档来源的字节是否存在且哈希一致。

时间边界仍然严格：`retrieved_at` 由程序生成，`sha256` 由程序计算，都不接受用户伪造；未知发布日期只作背景，不独立支撑关键结论。

## 2. 数据流与调用链

```text
source-add --input request.json
    │
    ▼
cli.py → research.source_add()
    │  1. sources.acquire(request)：URL 下载 或 读取本地文件（不持锁、不碰数据库）
    │  2. hashlib.sha256(bytes) + sources.extract_text(bytes)
    │  3. 取项目写锁 → 按哈希查重 → 写原始字节/文本 → 写 sources + run_sources
    ▼
data/stocks/<ticker>/sources/<sha256>.<ext> 与 <sha256>.txt

source-read RUN SOURCE [--page N | --start-line N]
    │
    ▼
读取 <sha256>.txt → 按 \f 分页 或 按行切片 → 返回 locator、truncated、next
```

阶段 1 的 `EvidenceRef.locator` 现在有了真实落点：PDF 用 `page N`，文本用 `lines a-b`。

## 3. 实际文件与入口

- `src/wonder_stockresearch/sources.py`（新）：
  - `fetch_url(url)`：只接受 HTTP(S)，手动逐跳重定向最多 5 次，每跳用 `_is_public_host` 拒绝回环/私网/保留地址；`_get_with_retries` 连接超时 10s、读取 90s，网络错误或 429/5xx 最多 2 次尝试并尊重 `Retry-After`（上限 60s）；`_read_limited` 流式读取并在 50 MiB 处拒绝。
  - `load_file(path)`：只读用户提供的文件，同样有 50 MiB 上限。
  - `extract_text(bytes, ext, content_type)`：PDF 用 `pypdf` 逐页提取并以 `\f` 连接；HTML 用标准库 `HTMLParser` 取可见文本、跳过 `script/style`，不执行脚本；文本类按 charset 解码。无法提取时返回 `extraction_unavailable`。
- `src/wonder_stockresearch/models.py`：新增 `SourceRequest`（`url` 与 `file` 恰好一个，`publication_precision` 与 `published_at` 一致）。
- `src/wonder_stockresearch/research.py`：新增 `source_add`、`list_sources`、`read_source`，以及 `_archive_issues`（只校验 `data/stocks/` 下的来源，教学元数据来源不受影响）。
- `src/wonder_stockresearch/storage.py`：新增 `write_bytes_atomic`、`find_source_by_hash`、`get_source`、`is_source_linked`。
- `src/wonder_stockresearch/cli.py`：新增 `source-add`、`sources`、`source-read`。
- `pyproject.toml`：新增 `requests`、`pypdf` 依赖。
- `tests/test_sources.py`（新，12 个测试）。

## 4. 最小可运行示例

```bash
uv run wonder-research --root "$DEMO" start SYNTHETIC          # 记下 run_id
# request.json: {"kind":"primary","title":"本地教学文档","file":"/path/report.txt"}
uv run wonder-research --root "$DEMO" source-add "$RUN" --input request.json
uv run wonder-research --root "$DEMO" sources "$RUN"
uv run wonder-research --root "$DEMO" source-read "$RUN" <source_id> --limit-lines 2
```

实跑结果（阶段 3 实现后）：

```text
source-add → source_id=f1f8f3cb-…、sha256=70904290…、extraction_status=available、
             raw_path=data/stocks/SYNTHETIC/sources/70904290….txt、deduplicated=false
sources    → origin=report.txt、published_at=null、retrieved_at=2026-09-20T10:26:07+00:00
source-read→ locator="lines 1-2"、truncated=true、next={"start_line":3}
URL 导入    → https://example.com/ 成功，extension=html、extraction_status=available
私网拒绝    → http://127.0.0.1/x → blocked_host（退出码 2）
```

## 5. 为什么这样实现

- **先取字节、后持锁**：网络下载在锁外完成，避免长时间占用项目写锁；下载完成后再登记。
- **SSRF 防护**：手动处理重定向并逐跳解析主机，拒绝回环/私网/保留地址，防止公开 URL 跳到内网。
- **字节即事实源**：原文文件按 SHA-256 命名保存；相同字节复用同一文件，但每次都新建一条 `Source` 记录并记录本次 `retrieved_at`，不合并不同取得事件。
- **提取失败不丢原文**：扫描 PDF 等无法提取时保留原始字节，`extraction_status=extraction_unavailable`、`text_path=null`，提示人工补文本，不自动 OCR。
- **时间不可伪造**：`SourceRequest` 不接受 `sha256`/`retrieved_at`；发布日期只能来自用户提供的元数据，未知即 `unknown`，只作背景。
- **归档校验只在有字节时生效**：`validate`/`finish` 对 `data/stocks/` 下的来源检查文件存在与哈希；阶段 2 的教学元数据来源（`data/teaching/…`）不因此被误判。

## 6. 测试覆盖与不能证明的事

`tests/test_sources.py` 覆盖：本地文件归档字节/哈希/文本、相同字节去重但保留两条来源、空白 PDF 报 `extraction_unavailable`、HTML 忽略脚本、按行读取的截断与 `next`、按 `\f` 分页与越界、未来发布日期被拒绝、私网 URL 拒绝、超大文件拒绝、重定向与 content-type 映射（fake 传输）、`source-add/sources/source-read` 的 CLI 契约，以及归档文件缺失时 `validate`/`finish` 拒绝。

这些测试**不能**证明：下载内容真实、引用语义正确、证据足以支撑结论，或公司值得投资。校验只覆盖结构、时间与字节一致性；"校验通过≠结论正确"，分析者仍须读原文。测试不访问网络，URL 路径用 fake 传输；唯一一次真实请求是实施时的 `https://example.com/` smoke。

## 7. 小练习

1. 用 `source-read RUN SOURCE --page 1` 读取一个 PDF；再试 `--page 99`，预期 `page_out_of_range`（退出码 2）。
2. 对同一个文件连续运行两次 `source-add`。预期：第二次 `deduplicated=true`、`source_id` 不同、`raw_path` 相同，`sources` 列出两条。
3. 手动删除 `data/stocks/.../sources/<sha>.txt`（保留原始字节），再 `validate RUN`。预期：仍可通过（文本是视图）；改为删除原始字节文件，预期 `missing_archived_file`。

## 8. 已知限制与下一阶段

- 不执行网页脚本、不点开交互内容、不做 OCR；扫描件需人工补文本。
- 没有真实公告自动发现、富途行情/财务数据、SEC/HKEX 检索（阶段 4）。
- `source-read` 的页码只在文本含 `\f` 页边界时有意义（PDF 提取会保留），纯文本通常只有一页。
- 未实现附记正文、专题报告与估值（估值位置仍只在附记预留）。
