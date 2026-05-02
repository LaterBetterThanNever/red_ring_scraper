#!/bin/bash
# ============================================================
# 小红圈每日自动任务
# 流程: 爬取文章 → 上传图片到GitHub → 同步到Notion数据库
#
# 使用方法:
#   ./daily_task.sh              # 爬取今天的内容
#   ./daily_task.sh 2026-05-02   # 爬取指定日期的内容
#   ./daily_task.sh --all        # 爬取所有内容
#   ./daily_task.sh --test       # 测试模式（只爬取，不上传）
#
# 定时任务设置 (crontab):
#   每天早上8点自动执行:
#   0 8 * * * /path/to/red_ring_scraper/daily_task.sh >> /path/to/red_ring_scraper/daily_task.log 2>&1
# ============================================================

set -e

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 默认日期（今天）
DATE_ARG="${1:-$(date +%Y-%m-%d)}"
TEST_MODE=false

if [ "$1" = "--test" ]; then
    DATE_ARG="$(date +%Y-%m-%d)"
    TEST_MODE=true
fi

echo "============================================"
echo "小红圈每日任务 - $(date '+%Y-%m-%d %H:%M:%S')"
echo "日期参数: $DATE_ARG"
echo "============================================"

# ============ 步骤 1: 爬取小红圈文章 ============
echo ""
echo "[1/3] 爬取小红圈文章..."

if [ "$1" = "--all" ]; then
    python3 scraper.py --all --comments
elif [ "$1" = "--test" ]; then
    python3 scraper.py --date "$DATE_ARG" --comments
else
    python3 scraper.py --date "$DATE_ARG" --comments
fi

echo "[1/3] 爬取完成!"

# ============ 步骤 2: 推送到 GitHub ============
if [ "$TEST_MODE" = true ]; then
    echo ""
    echo "[2/3] 测试模式，跳过 GitHub 推送"
else
    echo ""
    echo "[2/3] 推送到 GitHub..."

    # 启动 SSH agent 并从 Keychain 加载密钥（cron 环境下需要）
    if [ -z "$SSH_AUTH_SOCK" ]; then
        eval "$(ssh-agent -s)" 2>/dev/null
        ssh-add --apple-use-keychain ~/.ssh/id_ed25519_github 2>/dev/null || true
    fi

    # git add 所有文章内容
    git add articles/ .gitignore
    git add -u  # 移除已删除的文件

    # 检查是否有变更需要提交
    if git diff --cached --quiet; then
        echo '没有新的变更需要推送'
    else
        git commit -m "每日更新: $DATE_ARG 文章"
        git push origin main
        echo '已推送到 GitHub'
    fi

    echo "[2/3] GitHub 推送完成!"
fi

# ============ 步骤 3: 同步到 Notion ============
if [ "$TEST_MODE" = true ]; then
    echo ""
    echo "[3/3] 测试模式，跳过 Notion 同步"
else
    echo ""
    echo "[3/3] 同步文章到 Notion..."
    python3 notion_sync.py "articles/$DATE_ARG"
    echo "[3/3] Notion 同步完成!"
fi

echo ""
echo "============================================"
echo "每日任务完成! - $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================"
