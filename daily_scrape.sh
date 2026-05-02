#!/bin/bash
# 小红圈文章每日定时爬取脚本
# 使用方法: 添加到 crontab 实现每日自动运行
#
# 编辑 crontab:
#   crontab -e
#
# 添加以下行（每天早上 8:00 运行）:
#   0 8 * * * /Users/shenwuyue/Documents/Code\ Projects/red_ring_scraper/daily_scrape.sh >> /Users/shenwuyue/Documents/Code\ Projects/red_ring_scraper/cron.log 2>&1
#
# 或者使用 launchd（macOS 推荐方式，见下方说明）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 使用 Python3 运行爬虫
# 爬取最新文章（默认5页）
python3 scraper.py --pages 5

echo "------- 爬取完成: $(date) -------"
