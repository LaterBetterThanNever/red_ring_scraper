## 文件结构

```
red_ring_scraper/
├── scraper.py              # 主爬虫脚本
├── daily_scrape.sh         # 定时任务脚本
├── com.redring.scraper.plist  # macOS launchd 定时配置
├── config.json             # 首次运行自动生成，需填写 Cookie
└── articles/               # 文章输出目录（按日期分目录）
    └── 2026-04-04/
        └── 2026-04-04_文章标题.md
```

## 使用步骤

### 第 1 步：获取 Cookie

1. 在浏览器中打开 [https://www.red-ring.cn](https://www.red-ring.cn) 并**登录你的账号**
2. 按 `F12` 打开开发者工具 → Network 标签
3. 刷新页面，随便点一个请求，从 Headers 中复制完整的 **Cookie** 值

### 第 2 步：配置

首次运行脚本会自动生成 `config.json`，把 Cookie 粘贴进去：

```json
{
  "cookie": "粘贴你的完整Cookie",
  "group_id": 27593
}
```

### 第 3 步：运行

```bash
# 抓取最新文章（默认5页）
python3 scraper.py

# 抓取单篇文章
python3 scraper.py --single 1919853

# 指定翻页数
python3 scraper.py --pages 10

# 一次性爬取所有文章
python3 scraper.py --all

# 按日期爬取（如2026-04-01当天发布的文章）
python3 scraper.py --date 2026-04-01

# 包含评论和附件
python3 scraper.py --comments --attachments

# 同时使用多个参数
python3 scraper.py --date 2026-04-01 --comments --attachments
```

### 第 4 步：设置每日自动运行（macOS 推荐 launchd）

```bash
# 将 plist 文件复制到 LaunchAgents 目录
cp "/Users/shenwuyue/Documents/Code Projects/red_ring_scraper/com.redring.scraper.plist" ~/Library/LaunchAgents/

# 加载定时任务（每天早上 8:00 自动运行）
launchctl load ~/Library/LaunchAgents/com.redring.scraper.plist
```

停止定时任务：`launchctl unload ~/Library/LaunchAgents/com.redring.scraper.plist`

## 注意事项

- **Cookie 会过期**，如果爬取失败，需要重新登录获取新 Cookie
- 脚本会自动记录已爬取的文章 ID（`scraped_ids.json`），避免重复抓取
- 每次请求间隔 2-3 秒，防止被封
- 由于这是付费社群平台，API 接口的具体字段名可能需要根据实际响应微调，建议先用 `--single 1919853` 测试单篇抓取

