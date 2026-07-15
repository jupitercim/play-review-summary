# Google Play 评价周报

每周一自动拉取 Google Play 应用最近 7 天的评论，统计好评/差评数量，列出差评内容，推送到 Telegram，并在 GitHub Actions 运行页留存 Markdown 报告。

## 统计口径

- **好评**：≥ 4 星；**差评**：< 4 星。
- **只统计带文字内容的评论**。用户只打星、不写文字的纯评分，Google Play Developer API 不提供逐条数据，无法统计。
- Google 的评论接口只保留最近约 7 天的数据，所以按周运行正好覆盖，不要停跑太久，否则中间的评论会取不到。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `play_review_summary.py` | 主脚本：拉评论 → 统计 → 生成报告 → 推送 Telegram |
| `test_play_review_summary.py` | 单元测试（`python3 -m unittest` 运行） |
| `.github/workflows/weekly-review-summary.yml` | 定时任务：每周一北京时间 09:00 执行，也可手动触发 |
| `requirements.txt` | Python 依赖 |

## 配置步骤

### 1. 创建 Google Service Account 并授权

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建（或选择）一个项目，启用 **Google Play Android Developer API**。
2. 「IAM 与管理 → 服务账号」创建一个 service account，创建 JSON 密钥并下载。
3. 打开 [Google Play Console](https://play.google.com/console/) →「用户和权限」→ 邀请用户，填入 service account 的邮箱地址（形如 `xxx@yyy.iam.gserviceaccount.com`）。
4. 授予应用级权限：至少勾选 **「查看应用信息（只读）」** 和 **「回复评价」**（评论接口需要）。

### 2. 创建 Telegram Bot

1. 在 Telegram 里找 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建 bot，拿到 **bot token**。
2. 把 bot 拉进接收报告的群（或直接私聊 bot 发一条消息）。
3. 获取 **chat id**：浏览器访问 `https://api.telegram.org/bot<TOKEN>/getUpdates`，在返回 JSON 里找 `chat.id`（群的 id 一般是负数，形如 `-100xxxxxxxxxx`）。

### 3. 配置 GitHub Secrets

仓库「Settings → Secrets and variables → Actions → New repository secret」，添加以下 4 个：

| Secret 名 | 内容 |
| --- | --- |
| `PLAY_SERVICE_ACCOUNT_JSON` | service account JSON 文件的**完整内容**（整段粘贴） |
| `PLAY_PACKAGE_NAME` | 应用 package id，如 `com.example.app` |
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 token |
| `TELEGRAM_CHAT_ID` | 接收报告的 chat id |

### 4. 验证

推送代码到 GitHub 后，在「Actions → Weekly Play Review Summary → Run workflow」手动触发一次，确认 Telegram 收到消息。之后每周一北京时间 09:00 自动运行。

## 修改定时时间

编辑 `.github/workflows/weekly-review-summary.yml` 里的 cron 表达式（**UTC 时间**，北京时间减 8 小时）：

```yaml
- cron: "0 1 * * 1"   # 周一 01:00 UTC = 北京时间周一 09:00
```

## 本地运行

```bash
pip install -r requirements.txt
python3 -m unittest            # 跑测试
export PLAY_SERVICE_ACCOUNT_JSON="$(cat service-account.json)"
export PLAY_PACKAGE_NAME="com.example.app"
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
python3 play_review_summary.py
```
