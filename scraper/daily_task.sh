#!/bin/bash
# ============================================================
# 小红圈每日自动任务
# 流程: 爬取文章 → 推送图片到GitHub → 同步到Notion数据库
#
# 使用方法:
#   ./daily_task.sh                # 回溯处理最近 LOOKBACK_DAYS 天（默认3天，可自愈漏跑）
#   ./daily_task.sh 2026-05-02     # 只处理指定日期
#   ./daily_task.sh --all          # 全量爬取 + 同步所有本地日期
#   ./daily_task.sh --test         # 测试模式（只爬取，不推送/不同步）
#
# 定时任务: 由 launchd 每天 19:00 触发（见 com.redring.scraper.plist）
# 幂等性: 爬虫按 content_id 去重，Notion 按(日期,标题)去重，重复运行不会产生重复条目
# ============================================================

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

# 回溯窗口：漏跑 N 天内可自动补齐（爬虫与 Notion 均已去重，重复跑无副作用）
LOOKBACK_DAYS="${LOOKBACK_DAYS:-7}"
TEST_MODE=false
DATE_LIST=()
FAILURES=0

if [ "${1:-}" = "--test" ]; then
    TEST_MODE=true
    shift
fi

if [ "${1:-}" = "--all" ]; then
    MODE="all"
elif [ -n "${1:-}" ]; then
    MODE="single"
    DATE_LIST=("$1")
else
    MODE="lookback"
    for ((i=0; i<LOOKBACK_DAYS; i++)); do
        DATE_LIST+=("$(date -v-${i}d +%Y-%m-%d)")
    done
fi

# 失败时留下可见告警：令牌过期是静默失败，必须主动提醒
report_result() {
    local code="$1"
    if [ "$code" -gt 0 ]; then
        printf '%s 失败步骤数=%s（多为小红圈令牌过期，需重新登录并更新 .env）\n' \
            "$(date '+%Y-%m-%d %H:%M:%S')" "$code" > "$PROJECT_DIR/.last_run_failure"
        /usr/bin/osascript -e "display notification \"失败 $code 步，多半是令牌过期，请重新登录小红圈更新 .env\" with title \"小红圈爬虫任务失败\"" 2>/dev/null || true
    else
        rm -f "$PROJECT_DIR/.last_run_failure"
    fi
}

echo "============================================"
echo "小红圈任务 - $(date '+%Y-%m-%d %H:%M:%S')"
echo "模式: $MODE | 测试: $TEST_MODE"
if [ "$MODE" != "all" ]; then
    echo "处理日期: ${DATE_LIST[*]}"
fi
echo "============================================"

# ============ 步骤 1: 爬取小红圈文章 ============
echo ""
echo "[1/3] 爬取小红圈文章..."

if [ "$MODE" = "all" ]; then
    python3 scraper/scraper.py --all --comments || FAILURES=$((FAILURES+1))
else
    for d in "${DATE_LIST[@]}"; do
        python3 scraper/scraper.py --date "$d" --comments || {
            echo "  !! $d 爬取失败（可能令牌过期）"
            FAILURES=$((FAILURES+1))
        }
    done
fi

echo "[1/3] 爬取完成"

if [ "$TEST_MODE" = true ]; then
    echo ""
    echo "[2/3] 测试模式，跳过 GitHub 推送"
    echo "[3/3] 测试模式，跳过 Notion 同步"
    echo "测试完成（退出码 $FAILURES）"
    report_result $FAILURES
    exit $FAILURES
fi

# ============ 步骤 2: 推送到 GitHub（图片永久 URL 的来源） ============
echo ""
echo "[2/3] 推送到 GitHub..."

if [ -z "$SSH_AUTH_SOCK" ]; then
    eval "$(ssh-agent -s)" 2>/dev/null
    ssh-add --apple-use-keychain ~/.ssh/id_ed25519_github 2>/dev/null || true
fi

# 只提交 articles/：仓库里还有未相关的手工改动，不能把它们扫进"每日更新"提交
git add articles/ .gitignore 2>/dev/null
git add -u articles/ 2>/dev/null

if git diff --cached --quiet; then
    echo '没有新的变更需要推送'
else
    LABEL="$MODE"
    [ "$MODE" != "all" ] && LABEL="${DATE_LIST[*]}"
    git commit -m "每日更新: $LABEL 文章" && git push origin main && echo '已推送到 GitHub' \
        || { echo "  !! GitHub 推送失败"; FAILURES=$((FAILURES+1)); }
fi

# ============ 步骤 3: 同步到 Notion ============
echo ""
echo "[3/3] 同步文章到 Notion..."

if [ "$MODE" = "all" ]; then
    for dir in articles/*/; do
        [ -d "$dir" ] || continue
        python3 scraper/notion_sync.py "$dir" || {
            echo "  !! $dir 同步失败"
            FAILURES=$((FAILURES+1))
        }
    done
else
    for d in "${DATE_LIST[@]}"; do
        [ -d "articles/$d" ] || continue
        python3 scraper/notion_sync.py "articles/$d" || {
            echo "  !! $d 同步失败"
            FAILURES=$((FAILURES+1))
        }
    done
fi

echo ""
echo "============================================"
echo "任务完成 - $(date '+%Y-%m-%d %H:%M:%S') | 失败步骤数: $FAILURES"
echo "============================================"
report_result $FAILURES
exit $FAILURES
