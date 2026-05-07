#!/bin/bash
# Notion股票价格每日同步脚本
# 功能: 定时从Tushare获取股票最新价格并更新到Notion数据库
#
# 使用方法:
#   ./daily_stock_sync.sh           # 正式运行
#   ./daily_stock_sync.sh --test    # 测试模式
#   ./daily_stock_sync.sh --verify  # 验证数据库连接
#
# 定时设置 (crontab -e):
#   0 9 * * 1-5 cd /path/to/red_ring_scraper && ./daily_stock_sync.sh >> stock_sync.log 2>&1
#   (每个交易日早上9点执行)

set -e

# 脚本所在目录(项目根目录)
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 使用anaconda base环境的python（确保在conda base环境中运行）
if command -v python &> /dev/null; then
    PYTHON=python
elif command -v python3 &> /dev/null; then
    PYTHON=python3
else
    echo "错误: 未找到python或python3"
    exit 1
fi

# 如果使用conda，激活base环境
if command -v conda &> /dev/null; then
    eval "$(conda shell.bash hook)"
    conda activate base 2>/dev/null || true
fi

# 日志文件
LOG_FILE="$PROJECT_DIR/stock_sync.log"

echo "=========================================="
echo "股票价格同步任务开始"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到python3"
    exit 1
fi

# 检查依赖
echo "检查依赖..."
$PYTHON -c "import tushare" 2>/dev/null || {
    echo "安装tushare..."
    pip install tushare pandas requests -q
}

$PYTHON -c "import requests" 2>/dev/null || {
    echo "安装requests..."
    pip install requests -q
}

# 检查.env文件
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "错误: 未找到.env凭证文件"
    echo "请复制.env.example并填入你的token: cp .env.example .env"
    exit 1
fi

# 检查关键token是否配置
TUSHARE_TOKEN=$($PYTHON -c "import env_loader; print(env_loader.get('TUSHARE_TOKEN'))" 2>/dev/null)
if [ -z "$TUSHARE_TOKEN" ]; then
    echo "错误: .env中未配置TUSHARE_TOKEN"
    exit 1
fi

NOTION_TOKEN=$($PYTHON -c "import env_loader; print(env_loader.get('NOTION_TOKEN'))" 2>/dev/null)
if [ -z "$NOTION_TOKEN" ]; then
    echo "错误: .env中未配置NOTION_TOKEN"
    exit 1
fi

# 执行同步
echo ""
echo "开始同步股票数据..."
echo ""

if [ "$1" == "--verify" ]; then
    $PYTHON "$SCRIPT_DIR/notion_stock_sync.py" --verify
elif [ "$1" == "--test" ]; then
    echo "[测试模式]"
    $PYTHON "$SCRIPT_DIR/notion_stock_sync.py" --test
else
    $PYTHON "$SCRIPT_DIR/notion_stock_sync.py"
fi

EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ 同步任务完成"
else
    echo "✗ 同步任务失败 (exit code: $EXIT_CODE)"
fi
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

exit $EXIT_CODE
