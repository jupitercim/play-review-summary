# App 评价周报 + 每日安装量（Google Play + App Store）

两个 GitHub Actions 定时任务，都推送到同一个 Telegram 群，并在运行页留存 Markdown 报告：

- **评价周报**：每周一拉取 Android（Google Play）和 iOS（App Store Connect）最近 7 天的评论，统计好评/差评数量、平均分，分平台列出差评内容。
- **每日安装量**：每天早上推送**前天（D-2）**的安装量。两个平台的数据都有约 2 天延迟，所以拿不到"前一天"的数据，详见下面「统计口径」。

## 统计口径

### 评价周报

- **好评**：≥ 4 星；**差评**：< 4 星。
- **只统计带文字内容的评论**。用户只打星、不写文字的纯评分，Google Play / App Store Connect 两边接口都不提供逐条数据，无法统计。
- Google Play 评论接口只保留最近约 7 天的数据，所以按周运行正好覆盖，不要停跑太久，否则中间的评论会取不到。App Store Connect 接口保留全量历史评论，按周期过滤即可，没有这个限制。

### 每日安装量

- **为什么是前天（D-2）**：
  - Google Play 没有安装量 API，只能读 Play Console 导出到 Cloud Storage 的月度 CSV。官方说明是"Data is captured daily and posted within 3 to 7 days"，实测通常滞后 2～3 天。
  - App Store 每日销售报告按**太平洋时间**切日，官方说明"generally available by 8 a.m. Pacific Time"，即北京时间约 23:00～次日 00:00 才有前一天的数据。北京早上 9 点跑任务时，最新可用的是前天。
- **Android 指标**（来自 `stats/installs/*_overview.csv`）：用户安装（Daily User Installs，按用户计）、设备安装（Daily Device Installs，含同一用户换机/重装）、设备卸载。如果前天的数据还没生成，报告会回退到最新可用的一天并标注日期。
- **iOS 指标**（来自 Sales and Trends 日报，按 Product Type Identifier 归类）：首次下载（1 / 1F / 1T / 1E / 1EP / 1EU / 1-B）、重新下载（3 / 3F）、更新（7 / 7F / 7T）。当天零下载时 Apple 返回 404 "There were no sales for the date specified"，报告按 0 处理；报告尚未生成时显示"获取失败"并附 Apple 的原始说明。
- 两个平台各自独立：其中一个平台配置有误、拉取失败或数据没出，不影响另一个平台正常展示。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `review_summary.py` | 周报入口：读取环境变量 → 分别拉取两平台评论 → 生成合并报告 → 推送 Telegram |
| `install_summary.py` | 日报入口：计算 D-2 → 分别拉取两平台安装量 → 生成报告 → 推送 Telegram |
| `google_play.py` | Android：评论拉取（Google Play Developer API）+ 安装量读取（Cloud Storage 报表桶） |
| `app_store.py` | iOS：评论拉取 + 每日销售报告拉取（App Store Connect API，JWT 鉴权） |
| `report.py` | 评价统计逻辑 + 周报 Telegram / Markdown 生成 |
| `install_report.py` | 安装量日报 Telegram / Markdown 生成 |
| `notify.py` | Telegram 推送、Markdown 报告落盘（两个入口共用） |
| `test_*.py` | 单元测试（`python3 -m unittest discover` 运行） |
| `.github/workflows/weekly-review-summary.yml` | 周报定时任务：每周一北京时间 09:00 执行，也可手动触发 |
| `.github/workflows/daily-install-summary.yml` | 日报定时任务：每天北京时间 09:00 执行，也可手动触发 |
| `requirements.txt` | Python 依赖 |

## 配置步骤

### 1. Android：创建 Google Service Account 并授权

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建（或选择）一个项目，启用 **Google Play Android Developer API**。安装量读取还会用到 **Cloud Storage JSON API**，新项目默认已启用，若被关掉需一并启用。
2. 「IAM 与管理 → 服务账号」创建一个 service account，创建 JSON 密钥并下载。
3. 打开 [Google Play Console](https://play.google.com/console/) →「用户和权限」→ 邀请用户，填入 service account 的邮箱地址（形如 `xxx@yyy.iam.gserviceaccount.com`）。
4. 评论接口需要应用级权限：至少勾选 **「查看应用信息（只读）」** 和 **「回复评价」**。
5. 安装量需要**账号级**权限：在该用户的「账号权限」里勾选 **「查看应用信息并下载批量报告（只读）」**。官方原文："To access bulk reports, your 'View app information' permission must be set to 'Global'"，只给应用级权限读不到报表桶。新授权可能要几小时到一天才生效。
6. 获取报表桶地址：Play Console →「下载报告」→「统计信息」→ 点击右上角「复制 Cloud Storage URI」，得到形如 `gs://pubsite_prod_rev_01234567890987654321` 的地址。

### 2. iOS：创建 App Store Connect API Key

1. 打开 [App Store Connect](https://appstoreconnect.apple.com/) →「用户和访问」→「密钥」（Keys）。
2. 创建一个 API Key。评论接口要能读取 Customer Reviews（如 **App Manager**）；**销售报告要求 Admin、Finance 或 Sales 角色**，App Manager 拿不到。两个任务共用一个 Key 的话，直接用 Admin 或 Sales 角色。
3. 记下 **Key ID** 和 **Issuer ID**，下载生成的 `.p8` 私钥文件（只能下载一次，务必妥善保存）。
4. 找到应用的数字 **App ID**：App Store Connect → 该 App →「App 信息」页面里的「Apple ID」（是一串数字，不是 bundle id）。
5. 找到 **Vendor Number**：App Store Connect →「付款和财务报告」，左上角显示的一串数字（一般以 8 开头）。

### 3. 创建 Telegram Bot

1. 在 Telegram 里找 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建 bot，拿到 **bot token**。
2. 把 bot 拉进接收报告的群（或直接私聊 bot 发一条消息）。
3. 获取 **chat id**：浏览器访问 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在返回 JSON 里找 `chat.id`（群的 id 一般是负数，形如 `-100xxxxxxxxxx`）。

### 4. 配置 GitHub Secrets

仓库「Settings → Secrets and variables → Actions → New repository secret」，添加以下 10 个：

| Secret 名 | 内容 | 周报 | 日报 |
| --- | --- | --- | --- |
| `PLAY_SERVICE_ACCOUNT_JSON` | service account JSON 文件的**完整内容**（整段粘贴） | ✅ | ✅ |
| `PLAY_PACKAGE_NAME` | 应用 package id，如 `com.example.app` | ✅ | ✅ |
| `PLAY_REPORTS_BUCKET` | 报表桶地址，`gs://pubsite_prod_rev_xxx` 或只填桶名都可以 | | ✅ |
| `APPSTORE_KEY_ID` | App Store Connect API Key 的 Key ID | ✅ | ✅ |
| `APPSTORE_ISSUER_ID` | Issuer ID | ✅ | ✅ |
| `APPSTORE_PRIVATE_KEY` | `.p8` 私钥文件的**完整内容**（整段粘贴） | ✅ | ✅ |
| `APPSTORE_APP_ID` | App Store Connect 里的数字 App ID（不是 bundle id） | ✅ | ✅ |
| `APPSTORE_VENDOR_NUMBER` | Vendor Number | | ✅ |
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 token | ✅ | ✅ |
| `TELEGRAM_CHAT_ID` | 接收报告的 chat id | ✅ | ✅ |

### 5. 验证

推送代码到 GitHub 后，在「Actions」里分别手动触发一次 **Weekly App Review Summary** 和 **Daily App Install Summary**（Run workflow），确认 Telegram 收到消息。之后周报每周一、日报每天北京时间 09:00 自动运行。

日报首次运行常见的两个失败：Android 报 403 说明 service account 还没有账号级的批量报告权限（或权限还没生效）；iOS 报 403 说明 API Key 角色不够。

## 修改定时时间

编辑对应 workflow 里的 cron 表达式（**UTC 时间**，北京时间减 8 小时）：

```yaml
# .github/workflows/weekly-review-summary.yml
- cron: "0 1 * * 1"   # 周一 01:00 UTC = 北京时间周一 09:00

# .github/workflows/daily-install-summary.yml
- cron: "0 1 * * *"   # 每天 01:00 UTC = 北京时间 09:00
```

日报如果改到北京时间 23:30 之后跑，iOS 可以拿到前一天的数据，但 Google Play 大概率还没有；要改口径的话同时改 `install_report.py` 里的 `DATA_LAG_DAYS`。

## 本地运行

```bash
pip install -r requirements.txt
python3 -m unittest discover -v      # 跑测试
export PLAY_SERVICE_ACCOUNT_JSON="$(cat service-account.json)"
export PLAY_PACKAGE_NAME="com.example.app"
export PLAY_REPORTS_BUCKET="gs://pubsite_prod_rev_01234567890987654321"
export APPSTORE_KEY_ID="ABC123DEFG"
export APPSTORE_ISSUER_ID="12345678-1234-1234-1234-123456789012"
export APPSTORE_PRIVATE_KEY="$(cat AuthKey_ABC123DEFG.p8)"
export APPSTORE_APP_ID="123456789"
export APPSTORE_VENDOR_NUMBER="80012345"
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
python3 review_summary.py    # 评价周报
python3 install_summary.py   # 每日安装量
```
