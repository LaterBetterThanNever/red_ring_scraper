"""Notion持仓管理脚本
功能:
1. 从Notion数据库读取股票列表
2. 根据股票名称查询股票代码(首次运行时)
3. 根据股票代码查询最新价格(A股/港股)
4. 更新Notion数据库中的「股票代码」「现价」字段
5. 增持/减持股票: 重新计算持仓数量和成本
6. 新建股票: 在Notion中创建新的股票记录
7. 更新目标仓位

使用方法:
  python notion_stock_sync.py [--test]           # 同步价格
  python notion_stock_sync.py --verify           # 验证数据库

依赖:
  pip install tushare pandas requests
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import tushare as ts

# ============ 配置 ============

import sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import env_loader

# Tushare配置
TUSHARE_TOKEN = env_loader.get("TUSHARE_TOKEN")

# Notion配置
NOTION_TOKEN = env_loader.get("NOTION_TOKEN")
NOTION_STOCK_DATABASE_ID = env_loader.get("NOTION_STOCK_DATABASE_ID", "355ff2ba0bbc8152bca0d830a11a6eff")

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stock_sync.log"),
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger(__name__)


# ============ Tushare数据查询 ============

class StockDataFetcher:
    """股票数据查询器"""
    
    def __init__(self, token: str):
        """初始化Tushare API"""
        if not token:
            raise ValueError("TUSHARE_TOKEN未配置,请在config.json或环境变量中设置")
        self.pro = ts.pro_api(token)
        self._a_stock_cache = {}
        self._hk_stock_cache = {}
        
    def _load_a_stock_list(self):
        """加载A股股票列表"""
        if self._a_stock_cache:
            return
        
        logger.info("加载A股股票列表...")
        try:
            df = self.pro.stock_basic(exchange='', list_status='L', 
                                     fields='ts_code,name,area,industry,list_date')
            for _, row in df.iterrows():
                self._a_stock_cache[row['name']] = row['ts_code']
            logger.info(f"已加载 {len(self._a_stock_cache)} 只A股")
        except Exception as e:
            logger.error(f"加载A股列表失败: {e}")
            raise
    
    def _load_hk_stock_list(self):
        """加载港股股票列表"""
        if self._hk_stock_cache:
            return
        
        logger.info("加载港股股票列表...")
        try:
            df = self.pro.hk_basic(exchange='HKSE', list_status='L')
            for _, row in df.iterrows():
                self._hk_stock_cache[row['name']] = row['ts_code']
            logger.info(f"已加载 {len(self._hk_stock_cache)} 只港股")
        except Exception as e:
            logger.error(f"加载港股列表失败: {e}")
            raise
    
    def find_stock_code(self, stock_name: str) -> tuple:
        """
        根据股票名称查询股票代码
        返回: (ts_code, market_type) 
        market_type: 'A' 或 'HK'
        """
        # 先查A股
        self._load_a_stock_list()
        if stock_name in self._a_stock_cache:
            return self._a_stock_cache[stock_name], 'A'
        
        # 再查港股
        self._load_hk_stock_list()
        if stock_name in self._hk_stock_cache:
            return self._hk_stock_cache[stock_name], 'HK'
        
        # 模糊匹配
        for name, code in self._a_stock_cache.items():
            if stock_name in name or name in stock_name:
                logger.info(f"模糊匹配A股: {stock_name} -> {name} ({code})")
                return code, 'A'
        
        for name, code in self._hk_stock_cache.items():
            if stock_name in name or name in stock_name:
                logger.info(f"模糊匹配港股: {stock_name} -> {name} ({code})")
                return code, 'HK'
        
        return None, None
    
    def get_a_stock_price(self, ts_code: str) -> float:
        """获取A股最新价格"""
        try:
            # 获取最近5天的数据
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
            
            df = self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if df.empty:
                logger.warning(f"A股 {ts_code} 无行情数据")
                return None
            
            # 按日期排序,取最新一条
            df = df.sort_values('trade_date', ascending=False)
            latest_price = df.iloc[0]['close']
            return float(latest_price)
        except Exception as e:
            logger.error(f"获取A股 {ts_code} 价格失败: {e}")
            return None
    
    def get_hk_stock_price(self, ts_code: str) -> float:
        """获取港股最新价格（使用腾讯财经API获取实时数据）"""
        try:
            # 从ts_code提取股票代码: 09988.HK -> hk09988
            if ts_code.endswith('.HK'):
                symbol = 'hk' + ts_code.replace('.HK', '')
            else:
                symbol = 'hk' + ts_code
            
            # 腾讯财经港股实时行情API
            url = f"http://qt.gtimg.cn/q={symbol}"
            
            response = requests.get(url, timeout=10)
            response.encoding = 'gbk'
            
            if response.status_code == 200 and response.text.strip():
                # 解析返回数据
                # 格式: v_hk09988="1~阿里巴巴-W~09988~131.700~126.000~128.800~...";
                data_str = response.text.split('=')[1].strip('";')
                fields = data_str.split('~')
                
                if len(fields) >= 4:
                    current_price = float(fields[3])  # 当前价
                    return current_price
            
            # 如果腾讯API失败，回退到Tushare
            logger.warning(f"腾讯API获取失败，回退到Tushare: {ts_code}")
            return self._get_hk_price_from_tushare(ts_code)
            
        except Exception as e:
            logger.error(f"获取港股价格失败(腾讯): {e}")
            # 回退到Tushare
            try:
                return self._get_hk_price_from_tushare(ts_code)
            except:
                return None
    
    def _get_hk_price_from_tushare(self, ts_code: str) -> float:
        """从Tushare获取港股价格（备用方案）"""
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
            
            df = self.pro.hk_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if df.empty:
                logger.warning(f"港股 {ts_code} 无行情数据")
                return None
            
            df = df.sort_values('trade_date', ascending=False)
            latest_price = df.iloc[0]['close']
            return float(latest_price)
        except Exception as e:
            logger.error(f"获取港股 {ts_code} 价格失败(Tushare): {e}")
            return None
    
    def get_stock_price(self, ts_code: str, market_type: str) -> float:
        """根据市场类型获取股票价格"""
        if market_type == 'A':
            return self.get_a_stock_price(ts_code)
        elif market_type == 'HK':
            return self.get_hk_stock_price(ts_code)
        else:
            logger.error(f"未知的市场类型: {market_type}")
            return None


# ============ Notion数据库操作 ============

class NotionStockManager:
    """Notion股票数据库管理器"""
    
    def __init__(self, token: str, database_id: str):
        """初始化Notion客户端"""
        if not token:
            raise ValueError("NOTION_TOKEN未配置")
        self.token = token
        self.database_id = database_id
        self.api_base = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        
    def verify_database(self):
        """验证数据库是否可访问"""
        try:
            # 获取数据库信息
            url = f"{self.api_base}/databases/{self.database_id}"
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            db = response.json()
            
            title = db.get("title", [])
            title_text = "".join(t.get("plain_text", "") for t in title)
            logger.info(f"数据库: {title_text} (ID: {self.database_id})")
            
            # 打印属性信息
            props = db.get("properties", {})
            logger.info(f"数据库属性: {list(props.keys())}")
            
            # 尝试查询一条记录验证权限
            query_url = f"{self.api_base}/databases/{self.database_id}/query"
            query_response = requests.post(
                query_url,
                headers=self.headers,
                json={"page_size": 1},
                timeout=30
            )
            query_response.raise_for_status()
            result = query_response.json()
            logger.info(f"查询测试成功,返回 {len(result.get('results', []))} 条记录")
            
            return True
        except Exception as e:
            logger.error(f"无法访问数据库: {e}")
            return False
    
    def get_all_stocks(self) -> list:
        """
        读取数据库中所有股票
        返回: [{
            'page_id': str,
            '股票名称': str,
            '股票代码': str,
            '现价': float,
            '持仓数量': int,
            '成本': float,
            '目标仓位': float,
            '是否港股': bool,
        }]
        """
        stocks = []
        
        try:
            # 分页查询所有记录
            has_more = True
            start_cursor = None
            query_url = f"{self.api_base}/databases/{self.database_id}/query"
            
            while has_more:
                payload = {"page_size": 100}
                if start_cursor:
                    payload["start_cursor"] = start_cursor
                
                response = requests.post(
                    query_url,
                    headers=self.headers,
                    json=payload,
                    timeout=30
                )
                response.raise_for_status()
                result = response.json()
                
                for page in result.get("results", []):
                    stock_data = {'page_id': page['id']}
                    properties = page.get('properties', {})
                    
                    # 股票名称
                    stock_data['股票名称'] = (
                        self._get_property_text(properties, '投资标的') or 
                        self._get_property_text(properties, '股票名称')
                    )
                    # 股票代码
                    stock_data['股票代码'] = (
                        self._get_property_text(properties, '股票代码') or ''
                    )
                    # 现价
                    stock_data['现价'] = self._get_property_number(properties, '现价')
                    # 持仓数量
                    stock_data['持仓数量'] = self._get_property_number(properties, '持仓数量')
                    # 成本
                    stock_data['成本'] = self._get_property_number(properties, '成本')
                    # 目标仓位
                    stock_data['目标仓位'] = self._get_property_number(properties, '目标仓位')
                    # 是否港股
                    stock_data['是否港股'] = self._get_property_checkbox(properties, '是否港股')
                    
                    stocks.append(stock_data)
                
                has_more = result.get("has_more", False)
                start_cursor = result.get("next_cursor")
            
            logger.info(f"共读取 {len(stocks)} 只股票")
            return stocks
            
        except Exception as e:
            logger.error(f"查询股票列表失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    def update_properties(self, page_id: str, properties: dict):
        """通用属性更新方法"""
        try:
            url = f"{self.api_base}/pages/{page_id}"
            payload = {"properties": properties}
            response = requests.patch(url, headers=self.headers, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"已更新属性: {list(properties.keys())} (page: {page_id[:8]}...)")
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.error(f"更新属性失败: {e}")
            return False
    
    def update_stock_code(self, page_id: str, stock_code: str):
        """更新股票代码"""
        return self.update_properties(page_id, {
            "股票代码": {"rich_text": [{"text": {"content": stock_code}}]}
        })
    
    def update_stock_price(self, page_id: str, price: float):
        """更新股票现价"""
        logger.info(f"更新现价: ¥{price:.2f}")
        return self.update_properties(page_id, {
            "现价": {"number": price}
        })
    
    def update_holding(self, page_id: str, quantity: int, cost: float):
        """更新持仓数量和成本"""
        logger.info(f"更新持仓: 数量={quantity}, 成本={cost:.3f}")
        return self.update_properties(page_id, {
            "持仓数量": {"number": quantity},
            "成本": {"number": cost}
        })
    
    def update_target_position(self, page_id: str, target: float):
        """更新目标仓位 (0~1之间的小数,如0.1表示10%)"""
        logger.info(f"更新目标仓位: {target*100:.1f}%")
        return self.update_properties(page_id, {
            "目标仓位": {"number": target}
        })
    
    def create_stock(self, name: str, stock_code: str, quantity: int, 
                     cost: float, is_hk: bool, target_position: float = 0) -> str:
        """
        在Notion数据库中创建新的股票记录
        返回新创建的page_id
        """
        try:
            url = f"{self.api_base}/pages"
            payload = {
                "parent": {"database_id": self.database_id},
                "properties": {
                    "投资标的": {"title": [{"text": {"content": name}}]},
                    "股票代码": {"rich_text": [{"text": {"content": stock_code}}]},
                    "持仓数量": {"number": quantity},
                    "成本": {"number": cost},
                    "是否港股": {"checkbox": is_hk},
                    "目标仓位": {"number": target_position},
                }
            }
            response = requests.post(url, headers=self.headers, json=payload, timeout=30)
            response.raise_for_status()
            page_id = response.json()['id']
            logger.info(f"已创建新股票: {name} ({stock_code}), 数量={quantity}, 成本={cost:.3f}, page={page_id[:8]}...")
            time.sleep(0.5)
            return page_id
        except Exception as e:
            logger.error(f"创建股票失败: {e}")
            return None
    
    def _get_property_text(self, properties: dict, prop_name: str) -> str:
        """获取文本类型属性"""
        if prop_name not in properties:
            return None
        
        prop = properties[prop_name]
        prop_type = prop.get('type')
        
        if prop_type == 'title':
            titles = prop.get('title', [])
            return titles[0]['plain_text'] if titles else None
        elif prop_type == 'rich_text':
            texts = prop.get('rich_text', [])
            return texts[0]['plain_text'] if texts else None
        
        return None
    
    def _get_property_number(self, properties: dict, prop_name: str) -> float:
        """获取数字类型属性"""
        if prop_name not in properties:
            return None
        
        prop = properties[prop_name]
        if prop.get('type') == 'number':
            return prop.get('number')
        
        return None
    
    def _get_property_checkbox(self, properties: dict, prop_name: str) -> bool:
        """获取复选框类型属性"""
        if prop_name not in properties:
            return False
        
        prop = properties[prop_name]
        if prop.get('type') == 'checkbox':
            return prop.get('checkbox', False)
        
        return False


# ============ 主流程 ============

def sync_stocks(test_mode: bool = False):
    """同步股票价格到Notion"""
    
    logger.info("=" * 60)
    logger.info("开始同步股票数据")
    logger.info("=" * 60)
    
    # 初始化
    try:
        stock_fetcher = StockDataFetcher(TUSHARE_TOKEN)
        notion_manager = NotionStockManager(NOTION_TOKEN, NOTION_STOCK_DATABASE_ID)
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return False
    
    # 验证Notion数据库
    if not notion_manager.verify_database():
        logger.error("Notion数据库验证失败,请检查:")
        logger.error("1. NOTION_TOKEN是否正确")
        logger.error("2. 数据库是否已共享给Integration")
        logger.error("3. 数据库ID是否正确")
        return False
    
    # 读取所有股票
    stocks = notion_manager.get_all_stocks()
    if not stocks:
        logger.warning("未找到任何股票记录")
        return False
    
    # 处理每只股票
    success_count = 0
    fail_count = 0
    
    for i, stock in enumerate(stocks, 1):
        logger.info(f"\n[{i}/{len(stocks)}] 处理股票: {stock.get('股票名称', '未知')}")
        
        page_id = stock['page_id']
        stock_name = stock.get('股票名称')
        stock_code = stock.get('股票代码')
        
        if not stock_name:
            logger.warning("股票名称为空,跳过")
            fail_count += 1
            continue
        
        # 步骤1: 如果没有股票代码,先查询并更新
        if not stock_code:
            logger.info(f"股票代码为空,正在查询: {stock_name}")
            
            if test_mode:
                logger.info("[测试模式] 跳过查询")
                continue
            
            ts_code, market_type = stock_fetcher.find_stock_code(stock_name)
            
            if ts_code:
                logger.info(f"查询成功: {stock_name} -> {ts_code} ({market_type}股)")
                notion_manager.update_stock_code(page_id, ts_code)
                stock_code = ts_code
            else:
                logger.error(f"未找到股票: {stock_name}")
                fail_count += 1
                continue
        
        # 步骤2: 判断市场类型并查询价格
        # Notion中的港股代码格式: HK0241, HK9988
        # A股代码格式: 000001, 600000
        # Tushare港股代码格式: 00241.HK, 9988.HK
        # TushareA股代码格式: 000001.SZ, 600000.SH
        
        if stock_code.startswith('HK') or stock_code.endswith('.HK'):
            market_type = 'HK'
            # 转换为Tushare格式: HK0241 -> 00241.HK (保持5位数字)
            if stock_code.startswith('HK'):
                code_num = stock_code[2:].zfill(5)  # 补齐5位
                ts_code_for_query = code_num + '.HK'
            else:
                ts_code_for_query = stock_code
        else:
            market_type = 'A'
            # 转换为Tushare格式: 600015 -> 600015.SH, 000001 -> 000001.SZ
            if '.' not in stock_code:
                if stock_code.startswith('6'):
                    ts_code_for_query = stock_code + '.SH'
                else:
                    ts_code_for_query = stock_code + '.SZ'
            else:
                ts_code_for_query = stock_code
        
        logger.info(f"查询{market_type}股价格: {stock_code} -> {ts_code_for_query}")
        
        if test_mode:
            logger.info("[测试模式] 跳过价格查询")
            continue
        
        price = stock_fetcher.get_stock_price(ts_code_for_query, market_type)
        
        if price is not None:
            logger.info(f"{stock_name} 最新价格: ¥{price:.2f}")
            notion_manager.update_stock_price(page_id, price)
            success_count += 1
        else:
            logger.error(f"获取价格失败: {stock_name}")
            fail_count += 1
        
        # 添加延迟避免API限流
        # 港股接口限制更严格(2次/分钟),需要更长延迟
        if market_type == 'HK':
            time.sleep(35)  # 港股等待35秒
        else:
            time.sleep(1)  # A股等待1秒
    
    # 总结
    logger.info("\n" + "=" * 60)
    logger.info("同步完成!")
    logger.info(f"成功: {success_count} 只")
    logger.info(f"失败: {fail_count} 只")
    logger.info(f"总计: {len(stocks)} 只")
    logger.info("=" * 60)
    
    return fail_count == 0


# ============ 主入口 ============

def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Notion股票价格同步脚本')
    parser.add_argument('--test', action='store_true', help='测试模式(只查询不更新)')
    parser.add_argument('--verify', action='store_true', help='验证数据库连接')
    
    args = parser.parse_args()
    
    if args.verify:
        # 仅验证数据库连接
        try:
            notion_manager = NotionStockManager(NOTION_TOKEN, NOTION_STOCK_DATABASE_ID)
            if notion_manager.verify_database():
                print("✓ 数据库连接正常")
                stocks = notion_manager.get_all_stocks()
                print(f"✓ 共找到 {len(stocks)} 只股票")
                if stocks:
                    print("\n前3只股票:")
                    for stock in stocks[:3]:
                        print(f"  - {stock.get('股票名称')} | 代码: {stock.get('股票代码', '未填写')} | 现价: {stock.get('现价', '未填写')}")
            else:
                print("✗ 数据库连接失败")
        except Exception as e:
            print(f"✗ 错误: {e}")
        return
    
    # 执行同步
    success = sync_stocks(test_mode=args.test)
    
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
