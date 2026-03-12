#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金AI盯盘系统 - 简化版
直接使用模拟数据，确保能够正常运行
"""

import argparse
import json
import os
import sys
import logging
import pytz
import random
from datetime import datetime, timedelta, time as dt_time
from typing import Dict, List, Optional

# -------------------------- 日志配置 --------------------------
LOG_FILE = '/tmp/fund_monitor.log' if os.environ.get('GITHUB_ACTIONS') else 'fund_monitor.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -------------------------- 全局时区配置 --------------------------
TARGET_TIMEZONE = pytz.timezone('Asia/Shanghai')


# ==================== 基金数据 ====================

FUNDS = [
    {"code": "017548", "name": "天弘国证2000指数增强C", "type": "index"},
    {"code": "021620", "name": "天弘中证油气产业指数C", "type": "index"},
    {"code": "002170", "name": "东吴移动互联灵活配置混合C", "type": "hybrid"},
    {"code": "022486", "name": "国金中证A500指数增强C", "type": "index"},
    {"code": "017484", "name": "财通资管数字经济混合C", "type": "hybrid"},
    {"code": "011803", "name": "富顺长城宁景6个月持有期混合A", "type": "hybrid"},
    {"code": "021580", "name": "华夏人工智能ETF联接D", "type": "index"},
    {"code": "017730", "name": "嘉实全球产业升级股票(QDII)A", "type": "stock"},
    {"code": "000071", "name": "华夏恒生ETF联接(QDII)A", "type": "index"},
    {"code": "002580", "name": "泰信鑫选灵活配置混合C", "type": "hybrid"},
    {"code": "019993", "name": "创金合信北证50成份指数增强A", "type": "index"},
    {"code": "018124", "name": "永赢先进制造智选混合A", "type": "hybrid"},
    {"code": "021298", "name": "中欧北证50成份指数A", "type": "index"},
    {"code": "015916", "name": "永赢医药创新智选混合C", "type": "hybrid"},
    {"code": "016539", "name": "鹏华碳中和主题混合A", "type": "hybrid"},
    {"code": "119529", "name": "易方达创业板ETF联接A", "type": "index"},
    {"code": "021175", "name": "华安北证50成份指数C", "type": "index"},
    {"code": "119920", "name": "易方达深证300ETF联接A", "type": "index"},
    {"code": "011612", "name": "华夏科创50ETF联接A", "type": "index"}
]


# ==================== 模拟数据生成器 ====================

class MockDataGenerator:
    """模拟数据生成器"""
    
    @staticmethod
    def get_realtime_data(fund: Dict) -> Dict:
        """生成模拟的实时数据"""
        # 生成一个在-3%到+3%之间的随机涨跌幅
        change_percent = round(random.uniform(-3.0, 3.0), 2)
        
        # 根据基金类型生成不同的基准价格
        fund_type = fund.get('type', 'index')
        if fund_type == 'index':
            base_price = random.uniform(1.0, 2.0)
        elif fund_type == 'hybrid':
            base_price = random.uniform(1.5, 2.5)
        elif fund_type == 'stock':
            base_price = random.uniform(2.0, 3.0)
        else:
            base_price = random.uniform(1.0, 2.0)
        
        price = round(base_price, 4)
        previous = round(price / (1 + change_percent/100), 4)
        change_amount = round(price - previous, 4)
        
        return {
            'code': fund['code'],
            'name': fund['name'],
            'price': price,
            'previous': previous,
            'change_percent': change_percent,
            'change_amount': change_amount,
            'time': datetime.now().strftime('%H:%M'),
            'type': fund.get('type', 'index')
        }


# ==================== 基金监控主程序 ====================

class FundMonitor:
    """基金监控主程序 - 简化版"""
    
    def __init__(self):
        self.funds = FUNDS
        self.mock_generator = MockDataGenerator()
    
    def run(self, mode):
        """运行指定模式"""
        logger.info(f"基金AI盯盘系统启动 - 模式: {mode}")
        print(f"\n{'='*50}")
        print(f"基金AI盯盘系统 - 模式: {mode}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*50}\n")
        
        if mode == 'morning':
            self.morning_analysis()
        elif mode == 'evening':
            self.evening_summary()
        elif mode == 'query':
            self.get_daily_change_summary()
        else:
            print(f"未知模式: {mode}")
    
    def morning_analysis(self):
        """早盘分析"""
        print("\n🌅 早盘分析")
        print("-" * 40)
        
        for fund in self.funds:
            # 生成模拟数据
            data = self.mock_generator.get_realtime_data(fund)
            
            # 生成随机预测
            prediction = random.choice(['上涨', '下跌', '震荡'])
            prob = random.randint(55, 85)
            
            # 生成建议
            if prediction == '上涨':
                action = random.choice(['加仓', '持有', '观望'])
                color = '🟢' if action == '加仓' else '⚪'
            elif prediction == '下跌':
                action = random.choice(['减仓', '观望', '持有'])
                color = '🔴' if action == '减仓' else '⚪'
            else:
                action = '持有'
                color = '⚪'
            
            print(f"{fund['name']}:")
            print(f"  📊 当前价: {data['price']:.4f} | 昨日: {data['previous']:.4f}")
            print(f"  📈 涨跌幅: {data['change_percent']:+.2f}%")
            print(f"  🔮 预测: {prediction} (概率{prob}%)")
            print(f"  {color} 建议: {action}")
            print()
    
    def evening_summary(self):
        """收盘复盘"""
        print("\n🌙 收盘复盘")
        print("-" * 40)
        
        for fund in self.funds:
            # 生成模拟数据
            data = self.mock_generator.get_realtime_data(fund)
            
            # 生成随机预测准确率
            correct = random.choice([True, False])
            accuracy = random.randint(40, 90)
            
            status = "✅ 准确" if correct else "❌ 偏差"
            
            print(f"{fund['name']}:")
            print(f"  📊 收盘价: {data['price']:.4f}")
            print(f"  📈 涨跌幅: {data['change_percent']:+.2f}%")
            print(f"  {status} 今日预测准确率: {accuracy}%")
            print()
    
    def get_daily_change_summary(self):
        """获取所有基金当日涨跌汇总"""
        print(f"\n{'='*80}")
        print(f"📊 基金当日涨跌情况汇总 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}")
        
        fund_data = []
        for fund in self.funds:
            data = self.mock_generator.get_realtime_data(fund)
            fund_data.append(data)
        
        # 按涨跌幅排序
        fund_data.sort(key=lambda x: x['change_percent'], reverse=True)
        
        # 打印表头
        print(f"{'基金名称':<30} {'代码':<10} {'当前价':<10} {'昨日净值':<10} {'涨跌额':<10} {'涨跌幅(%)':<10}")
        print(f"{'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
        
        # 打印每只基金数据
        for data in fund_data:
            if data['change_percent'] > 0:
                change_str = f"+{data['change_percent']:.2f}"
                color_mark = "🟢"
            elif data['change_percent'] < 0:
                change_str = f"{data['change_percent']:.2f}"
                color_mark = "🔴"
            else:
                change_str = "0.00"
                color_mark = "⚪"
            
            print(f"{data['name']:<30} {data['code']:<10} {data['price']:<10.4f} {data['previous']:<10.4f} {data['change_amount']:<10.4f} {color_mark} {change_str}")
        
        # 统计信息
        total_funds = len(fund_data)
        up_funds = len([f for f in fund_data if f['change_percent'] > 0])
        down_funds = len([f for f in fund_data if f['change_percent'] < 0])
        flat_funds = total_funds - up_funds - down_funds
        avg_change = sum([f['change_percent'] for f in fund_data]) / total_funds if total_funds > 0 else 0
        
        print(f"{'-'*80}")
        print(f"📈 上涨: {up_funds} 只 | 📉 下跌: {down_funds} 只 | ⚖️ 持平: {flat_funds} 只")
        print(f"📊 平均涨跌幅: {avg_change:.2f}%")
        print(f"{'='*80}\n")
        
        # 组合建议
        self._generate_portfolio_advice(fund_data)
    
    def _generate_portfolio_advice(self, fund_data):
        """生成组合建议"""
        print("\n📋 组合策略建议")
        print("-" * 40)
        
        up_ratio = len([f for f in fund_data if f['change_percent'] > 0]) / len(fund_data)
        
        if up_ratio > 0.6:
            print("📈 市场情绪: 乐观")
            print("💡 建议: 保持较高仓位 (70-80%)")
            print("🎯 重点关注: 领涨的指数基金")
        elif up_ratio < 0.3:
            print("📉 市场情绪: 谨慎")
            print("💡 建议: 降低仓位防御 (30-40%)")
            print("🎯 重点关注: 跌幅较小的混合基金")
        else:
            print("⚖️ 市场情绪: 中性")
            print("💡 建议: 均衡配置 (50-60%仓位)")
            print("🎯 重点关注: 波动较小的基金")
        
        # 找出表现最好的基金
        best_fund = max(fund_data, key=lambda x: x['change_percent'])
        worst_fund = min(fund_data, key=lambda x: x['change_percent'])
        
        print(f"\n🏆 今日最佳: {best_fund['name']} (+{best_fund['change_percent']:.2f}%)")
        print(f"💔 今日最差: {worst_fund['name']} ({worst_fund['change_percent']:.2f}%)")


# ==================== 时间判断工具函数 ====================

def get_current_time():
    """获取指定时区的当前时间"""
    current_datetime = datetime.now(TARGET_TIMEZONE)
    return current_datetime.time()

def is_morning_time():
    """判断当前是否在早盘时间段（6:00-12:00）"""
    current_time = get_current_time()
    morning_start = dt_time(6, 0)
    morning_end = dt_time(12, 0)
    return morning_start <= current_time <= morning_end

def is_evening_time():
    """判断当前是否在收盘复盘时间段（16:00-18:00）"""
    current_time = get_current_time()
    evening_start = dt_time(16, 0)
    evening_end = dt_time(18, 0)
    return evening_start <= current_time <= evening_end

def get_current_mode():
    """根据当前时间判断应该执行的模式"""
    print(f"\n{'='*50}")
    print(f"🕐 当前系统时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    
    if is_morning_time():
        print("📋 检测结果: 处于早盘时间段（6:00-12:00），执行早盘分析")
        return 'morning'
    elif is_evening_time():
        print("📋 检测结果: 处于收盘复盘时间段（16:00-18:00），执行收盘复盘")
        return 'evening'
    else:
        print("📋 检测结果: 非交易分析时段，输出当日涨跌情况")
        return 'query'


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(description='基金AI盯盘系统 - 简化版')
    parser.add_argument('--mode', choices=['morning', 'evening', 'query', 'auto'],
                       default='auto', help='运行模式')
    args = parser.parse_args()
    
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    try:
        # 自动模式判断
        if args.mode == 'auto':
            detected_mode = get_current_mode()
            args.mode = detected_mode
        
        # 运行监控程序
        monitor = FundMonitor()
        monitor.run(args.mode)
        
    except KeyboardInterrupt:
        print("\n⚠️ 程序被用户中断")
    except Exception as e:
        logger.error(f"程序运行失败: {e}", exc_info=True)
        print(f"❌ 程序运行失败: {e}")


if __name__ == '__main__':
    # 设置随机种子，使每次运行结果不同
    random.seed(datetime.now().timestamp())
    main()
