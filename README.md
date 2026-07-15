# App 评价周报（Google Play + App Store）

每周一自动拉取 Android（Google Play）和 iOS（App Store Connect）应用最近 7 天的评论，统计好评/差评数量、平均分，分平台列出差评内容，合并推送到 Telegram，并在 GitHub Actions 运行页留存 Markdown 报告。

## 统计口径

- **好评**：≥ 4 星；**差评**：< 4 星。
- **只统计带文字内容的评论**。用户只打星、不写文字的纯评分，Google Play / App Store Connect 两边接口都不提供逐条数据，无法统计。
- Google Play 评论接口只保留最近约 7 天的数据，所以按周运行正好覆盖，不要停跑太久，否则中间的评论会取不到。App Store Connect 接口保留全量历史评论，按周期过滤即可，没有这个限制。
- 两个平台的拉取各自独立：其中一个平台配置有误或拉取失败，不影响另一个平台正常统计和推送，失败的那部分会在报告里标注"获取失败"。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `review_summary.py` | 主入口：读取环境变量 → 分别拉取两平台评论 → 生成合并报告 → 推送 Telegram |
| `google_play.py` | Android 评论拉取（Google Play Developer API） |
| `app_store.py` | iOS 评论拉取（App Store Connect API，JWT 鉴权） |
| `report.py` | 统计逻辑 + Telegram / Markdown 合并报告生成 |
| `test_google_play.py` / `test_app_store.py` / `test_report.py` | 单元测试（`python3 -m unittest discover` 运行） |
| `.github/workflows/weekly-review-summary.yml` | 定时任务：每周一北京时间 09:00 执行，也可手动触发 |
| `requirements.txt` | Python 依赖 |

## 配置步骤

### 1. Android：创建 Google Service Account 并授权

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建（或选择）一个项目，启用 **Google Play Android Developer API**。
2. 「IAM 与管理 → 服务账号」创建一个 service account，创建 JSON 密钥并下载。
3. 打开 [Google Play Console](https://play.google.com/console/) →「用户和权限」→ 邀请用户，填入 service account 的邮箱地址（形如 `xxx@yyy.iam.gserviceaccount.com`）。
4. 授予应用级权限：至少勾选 **「查看应用信息（只读）」** 和 **「回复评价」**（评论接口需要）。

### 2. iOS：创建 App Store Connect API Key

1. 打开 [App Store Connect](https://appstoreconnect.apple.com/) →「用户和访问」→「密钥」（Keys）。
2. 创建一个 API Key，角色至少需要能读取 Customer Reviews（如 **App Manager**，或自定义角色勾选「客户评论」权限）。
3. 记下 **Key ID** 和 **Issuer ID**，下载生成的 `.p8` 私钥文件（只能下载一次，务必妥善保存）。
4. 找到应用的数字 **App ID**：App Store Connect → 该 App →「App 信息」页面里的「Apple ID」（是一串数字，不是 bundle id）。

### 3. 创建 Telegram Bot

1. 在 Telegram 里找 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建 bot，拿到 **bot token**。
2. 把 bot 拉进接收报告的群（或直接私聊 bot 发一条消息）。
3. 获取 **chat id**：浏览器访问 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在返回 JSON 里找 `chat.id`（群的 id 一般是负数，形如 `-100xxxxxxxxxx`）。

### 4. 配置 GitHub Secrets

仓库「Settings → Secrets and variables → Actions → New repository secret」，添加以下 8 个：

| Secret 名 | 内容 |
| --- | --- |
| `PLAY_SERVICE_ACCOUNT_JSON` | service account JSON 文件的**完整内容**（整段粘贴） |
| `PLAY_PACKAGE_NAME` | 应用 package id，如 `com.example.app` |
| `APPSTORE_KEY_ID` | App Store Connect API Key 的 Key ID |
| `APPSTORE_ISSUER_ID` | Issuer ID |
| `APPSTORE_PRIVATE_KEY` | `.p8` 私钥文件的**完整内容**（整段粘贴） |
| `APPSTORE_APP_ID` | App Store Connect 里的数字 App ID（不是 bundle id） |
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 token |
| `TELEGRAM_CHAT_ID` | 接收报告的 chat id |

### 5. 验证

推送代码到 GitHub 后，在「Actions → Weekly App Review Summary → Run workflow」手动触发一次，确认 Telegram 收到消息。之后每周一北京时间 09:00 自动运行。

## 修改定时时间

编辑 `.github/workflows/weekly-review-summary.yml` 里的 cron 表达式（**UTC 时间**，北京时间减 8 小时）：

```yaml
- cron: "0 1 * * 1"   # 周一 01:00 UTC = 北京时间周一 09:00
```

## 本地运行

```bash
pip install -r requirements.txt
python3 -m unittest discover -v      # 跑测试
export PLAY_SERVICE_ACCOUNT_JSON="$(cat service-account.json)"
export PLAY_PACKAGE_NAME="com.example.app"
export APPSTORE_KEY_ID="ABC123DEFG"
export APPSTORE_ISSUER_ID="12345678-1234-1234-1234-123456789012"
export APPSTORE_PRIVATE_KEY="$(cat AuthKey_ABC123DEFG.p8)"
export APPSTORE_APP_ID="123456789"
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
python3 review_summary.py
```
