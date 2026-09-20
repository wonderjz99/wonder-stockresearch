# 框架出处与设计依据

## 公司研究框架

本版研究问题、门闸与维度依据用户既有 eco-moat-ai Standard 框架的概念整理。交接阶段核对了旧项目中该 skill 的公司问题、门闸和 M1–M6；本项目的中文研究规格是重新组织的需求说明，不复制旧版可执行 skill、不携带股东信、全文案例或代码。

旧 skill 内的“Source repo”只指向其本地目录，未给出可确认的上游仓库；在该 skill 目录未找到 LICENSE 文件。故本项目不声称已获得某个开源许可证，也不替原材料添加 MIT 等许可。若以后要复制/分发其原始文件，应先核对真正来源和适用许可。当前交接包已包含实施所需的公司问题与规则，不需访问旧目录。

研究规格保留 Q01–Q24、G1/G2/G3/G5/G6/G7、M1–M6 及 0–60 加总，并按用户要求把估值 Q25–Q28/G4 分离。新设的 insufficient/null、评分锚点与不自动生成投资动作均在规格中明示为本版行为。

## 架构依据（2026-09-10 核对）

- SQLite 适用于本地应用与低并发存储：[官方说明](https://www.sqlite.org/whentouse.html)。
- OpenCode 支持工具与权限配置：[工具文档](https://opencode.ai/docs/tools/)。先通过 CLI 使用 Python，后续必要时才接 MCP。
- Pi Coding Agent 支持 skills、会话与扩展，推荐 CLI 或扩展接入外部能力：[官方仓库](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)。本版未对 OpenCode/Pi 做实际同条件性能对比，不宣称哪一个更省 token 或更准确。
- 富途 SDK：[官方仓库](https://github.com/FutunnOpen/py-futu-api)。2026-09-20 实测锁定 `futu-api==10.11.7108`：`OpenQuoteContext.get_market_snapshot` 与 `get_financials_statements`（statement_type 1–4，financial_type=ANNUAL）在本机 OpenD 返回真实数据；仅使用行情上下文，不调用账户/交易接口。
- SEC：[开发者入口](https://www.sec.gov/about/developer-resources)。2026-09-20 实测 `https://www.sec.gov/files/company_tickers.json` 在缺少带联系方式 User-Agent 时返回 403，故 SEC 通道需 `WONDER_SEC_USER_AGENT`，本环境未配置、标 blocked。
- HKEX：[披露入口](https://www.hkexnews.hk/)。2026-09-20 实测官方检索 `https://www1.hkexnews.hk/search/prefix.do`（查 stockId）与 `titleSearchServlet.do`（公告列表）返回真实数据。公司 IR 使用各公司正式网站。

这些外部链接是出处与实施核对入口，不是本地规格缺失内容的替代品。
