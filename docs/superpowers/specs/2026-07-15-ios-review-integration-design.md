# iOS（App Store Connect）评价接入设计

## 背景

现有工具 `play_review_summary.py` 每周拉取 Google Play 评论、统计好评/差评，推送到 Telegram 并留存 Markdown 报告。本设计给同一个工具加上 iOS（App Store Connect API）的等价能力，并把两边的结果整合进同一条 Telegram 消息 / 同一份 Markdown 报告，而不是分开两次推送。

## 目标

- 用 App Store Connect API 拉取 iOS 应用最近 7 天的用户评论（rating + 文字）。
- 和 Android 的统计结果一起，在一条 Telegram 消息、一份 Markdown 报告里分平台展示。
- 单一平台拉取失败不应影响另一平台正常发送。

## 非目标

- 不做评分的星级分布图表，只延续现有"好评数/差评数/平均分/差评列表"统计口径。
- 不尝试补齐"纯星级无文字"的评分（两边 API 都不提供逐条数据，维持现状说明）。
- 不引入数据库/持久化存储，仍是无状态的单次运行脚本（由 GitHub Actions 定时触发）。

## 文件结构

```
google_play.py     # Android 拉取逻辑：fetch_reviews() + extract_entries()（从现有脚本原样迁移，逻辑不变）
app_store.py       # iOS 拉取逻辑（新增）：JWT 签名 + 分页拉取 customerReviews + extract_entries()
report.py          # 合并报告生成：build_telegram_report() / build_markdown_report() / chunk_message()
review_summary.py  # 新入口脚本（替代 play_review_summary.py）：读取环境变量 → 分别拉取两平台（各自容错）→ summarize() → 合并报告 → 写文件 → 发 Telegram
```

`summarize()`（好评/差评数量、平均分统计）保持现有实现不变，直接复用——它只依赖 entry 字典里的 `rating` 字段，天然平台无关，放在 `review_summary.py` 里或独立小模块均可，实现阶段再定。

原 `play_review_summary.py` 及 `test_play_review_summary.py` 在迁移后删除/改名，避免新旧两个入口并存造成混淆。

## 数据模型

两平台的 entry 字典字段不完全对齐，各自保留平台特有信息，不强行统一：

| 字段 | Android | iOS |
| --- | --- | --- |
| `author` | ✓（`authorName`，无则"匿名用户"） | ✓（`reviewerNickname`，无则"匿名用户"） |
| `rating` | ✓（1-5） | ✓（1-5） |
| `text` | ✓（`userComment.text`） | ✓（`title` + `body` 拼接，title 为空则只用 body） |
| `app_version` | ✓（`appVersionName`） | ✗（Apple 不提供） |
| `territory` | ✗（Google 无对应字段） | ✓（ISO 3166-1 alpha-3，如 `CHN`/`USA`） |
| `modified_at` | ✓（`lastModified`） | ✓（`createdDate`） |

**已确认**：Google Play 的 `reviewerLanguage` 不是精确的国家/地区字段（是语言代码），因此不用它冒充"地区"；Android 差评列表维持原样只展示应用版本号，不新增地区列。iOS 差评列表新增"地区"列，展示 `territory`。

## Apple 鉴权与拉取流程

1. **JWT 构造**：
   - Header：`{"alg": "ES256", "kid": APPSTORE_KEY_ID, "typ": "JWT"}`
   - Payload：`{"iss": APPSTORE_ISSUER_ID, "iat": <now>, "exp": <now + 20分钟>, "aud": "appstoreconnect-v1"}`
   - 用 `.p8` 私钥（`APPSTORE_PRIVATE_KEY` 环境变量，PEM 全文）以 ES256 签名，依赖 `PyJWT[crypto]`（内部用到 `cryptography`）。
   - 每次运行只生成一次 token（20 分钟有效期足够覆盖一次周报的拉取耗时）。
2. **请求**：`GET https://api.appstoreconnect.apple.com/v1/apps/{APPSTORE_APP_ID}/customerReviews?sort=-createdDate&limit=200`，header 带 `Authorization: Bearer <jwt>`。
3. **分页**：跟随响应体 `links.next` 翻页；由于按 `-createdDate` 倒序返回，一旦当页最旧一条评论的 `createdDate` 早于 7 天窗口起点，即可提前停止翻页（苹果接口是全量历史数据，不像 Google 只保留约 7 天，因此需要主动截断，而不是依赖接口自然到底）。
4. **字段映射**：`attributes.rating` → `rating`；`attributes.title` + `attributes.body` → `text`；`attributes.reviewerNickname` → `author`；`attributes.territory` → `territory`；`attributes.createdDate`（ISO8601）→ `modified_at`。

## 报告合并

Telegram 消息与 Markdown 报告均按平台分两段展示，不合并数字统计：

```
📊 应用评价周报
周期：2026-07-08 ~ 2026-07-15

📱 Android
评论总数 / 好评 / 差评 / 平均分
⚠️ 差评列表（含版本号列）

🍎 iOS
评论总数 / 好评 / 差评 / 平均分
⚠️ 差评列表（含地区列）
```

两段各自独立统计，互不相加，也互不影响对方的展示。

## 错误处理

- Android、iOS 各自的拉取逻辑分别包一层 `try/except`。
- 单一平台拉取失败：对应段落展示 `⚠️ 获取失败：<错误摘要>`，另一平台正常统计、正常展示，脚本整体不因单一平台失败而 `sys.exit`。
- 两个平台都失败：仍然发送一条注明"两边都获取失败"的 Telegram 消息（而不是静默不发任何东西），便于第一时间发现问题（比如 secrets 配置错了）。
- 必需的环境变量（`APPSTORE_KEY_ID` / `APPSTORE_ISSUER_ID` / `APPSTORE_PRIVATE_KEY` / `APPSTORE_APP_ID` 等）缺失时，视为该平台拉取失败，走上面同一套容错逻辑，而不是让整个脚本因为 `require_env` 直接退出——避免一个平台的配置问题连累另一个平台的周报。

## Secrets / 配置

新增 4 个 GitHub Secrets：

| Secret 名 | 内容 |
| --- | --- |
| `APPSTORE_KEY_ID` | App Store Connect API Key 的 Key ID |
| `APPSTORE_ISSUER_ID` | Issuer ID |
| `APPSTORE_PRIVATE_KEY` | `.p8` 私钥文件完整内容 |
| `APPSTORE_APP_ID` | App Store Connect 里的数字 App ID（不是 bundle id） |

README 新增一节，指导用户在 App Store Connect →「用户和访问」→「密钥」创建 API Key（需要 Customer Reviews 读取权限，或 App Manager 角色），以及如何找到应用的数字 App ID。

`.github/workflows/weekly-review-summary.yml` 沿用现有 cron（每周一北京时间 09:00），改为调用 `review_summary.py`，新增上述 4 个 secret 的环境变量注入。

## 测试计划

- `test_google_play.py`：现有 `test_play_review_summary.py` 中和 Android 抓取/解析相关的用例原样迁移。
- `test_app_store.py`（新增）：
  - JWT 构造的 header/payload 内容正确性（不依赖真实网络）。
  - `extract_entries()` 对 Apple `customerReviews` JSON:API 响应形状的解析，含 7 天窗口过滤、title+body 拼接、territory 提取。
- `test_report.py`（新增，从原测试文件里的报告相关用例迁移+扩展）：
  - 两平台都正常时的合并报告输出。
  - 单一平台失败时对应段落的"获取失败"展示。
  - `chunk_message()` 长消息切分逻辑（不变，原样迁移测试）。

## 开放问题

无。以上设计已与用户逐项确认（模块拆分方式、地区字段处理、失败容错策略）。
