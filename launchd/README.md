# launchd 配置说明（macOS 本地运行）

## 快速开始

### 1. 获取 Telegram chat_id

向 https://t.me/userinfobot 发任意消息，它会回复你的 chat_id。

### 2. 替换占位符

编辑两个 .plist 文件，替换所有 `REPLACE_WITH_*`：

| 占位符 | 替换为 |
|---|---|
| `REPLACE_WITH_FULL_PATH` | 仓库绝对路径，如 `/Users/yourname/workspace/ai-newsday` |
| `REPLACE_WITH_TOKEN` | Telegram Bot Token（从 @BotFather 获取） |
| `REPLACE_WITH_CHAT_ID` | 你的 Telegram chat_id |
| `REPLACE_WITH_API_KEY` | ModelScope API Key |

### 3. 创建日志目录

```bash
mkdir -p /path/to/repo/logs
```

### 4. 加载服务

```bash
REPO=/path/to/repo
cp $REPO/launchd/ai-newsday-collect.plist ~/Library/LaunchAgents/
cp $REPO/launchd/ai-newsday-finalize.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai-newsday-collect.plist
launchctl load ~/Library/LaunchAgents/ai-newsday-finalize.plist
```

### 5. 手动测试（不等定时触发）

```bash
# 测试 collect tick（会推 Telegram 审稿卡片）
launchctl start ai.newsday.collect

# 测试 finalize tick（会定稿并推最终日报）
launchctl start ai.newsday.finalize
```

### 6. 查看日志

```bash
tail -f /path/to/repo/logs/collect.log
tail -f /path/to/repo/logs/finalize.log
```

### 7. 停用服务

```bash
launchctl unload ~/Library/LaunchAgents/ai-newsday-collect.plist
launchctl unload ~/Library/LaunchAgents/ai-newsday-finalize.plist
```

## 或者直接手动跑（不用 launchd）

```bash
cd /path/to/repo
source .env  # 或 export 环境变量
uv run python -m src.cli --tick collect
uv run python -m src.cli --tick finalize
```
