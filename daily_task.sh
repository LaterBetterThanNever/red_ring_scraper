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

# ============ 步骤 2: 上传图片到 GitHub ============
if [ "$TEST_MODE" = true ]; then
    echo ""
    echo "[2/3] 测试模式，跳过 GitHub 上传"
else
    echo ""
    echo "[2/3] 上传图片到 GitHub..."
    python3 -c "
import json, os, sys
from github_uploader import GitHubUploader

config_path = os.path.join('$SCRIPT_DIR', 'config.json')
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

github_token = config.get('github_token', '')
github_owner = config.get('github_owner', '')
github_repo = config.get('github_repo', 'red-ring-images')
github_branch = config.get('github_branch', 'main')

if not github_token or '填入' in github_token:
    print('WARNING: GitHub Token 未配置，跳过上传')
    sys.exit(0)

if not github_owner or '填入' in github_owner:
    print('WARNING: GitHub 用户名未配置，跳过上传')
    sys.exit(0)

uploader = GitHubUploader(github_token, github_owner, github_repo, github_branch)

# 确保仓库存在
try:
    uploader.get_repo_info()
    print(f'仓库已存在: {github_owner}/{github_repo}')
except:
    try:
        uploader.create_repo(private=True)
        print(f'已创建仓库: {github_owner}/{github_repo}')
    except Exception as e:
        print(f'创建仓库失败: {e}')
        sys.exit(1)

# 上传指定日期目录下的所有文章图片
date_str = '$DATE_ARG'
articles_dir = os.path.join('$SCRIPT_DIR', 'articles', date_str)
if not os.path.isdir(articles_dir):
    print(f'目录不存在: {articles_dir}')
    sys.exit(0)

total_images = 0
for article_name in sorted(os.listdir(articles_dir)):
    article_dir = os.path.join(articles_dir, article_name)
    if not os.path.isdir(article_dir):
        continue
    images_dir = os.path.join(article_dir, 'images')
    if not os.path.isdir(images_dir):
        continue
    images = [f for f in os.listdir(images_dir) if not f.startswith('.')]
    if not images:
        continue
    print(f'上传文章图片: {article_name} ({len(images)} 张)')
    url_map = uploader.upload_article(article_dir, date_str)
    total_images += len(url_map)

print(f'共上传 {total_images} 张图片到 GitHub')
"
    echo "[2/3] GitHub 上传完成!"
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
