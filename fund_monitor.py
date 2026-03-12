#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金AI盯盘系统 - 定时推送版
功能：每隔半小时自动执行，查看涨跌情况并推送
支持：早盘分析、收盘复盘、定时查询
"""

import argparse
import json
import os
import sys
import logging
import pytz
import random
import time
from datetime import datetime, timedelta, time as dt_time
from typing import Dict, List, Optional, Any

# 尝试导入schedule，如果没有则安装
try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False
    print("提示: 如需守护进程模式，请安装 schedule: pip install schedule")

# 尝试导入requests
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

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


# ==================== PushPlus推送配置 ====================

class PushNotifier:
    """Pushplus推送"""
    
    def __init__(self, token=None):
        self.token = token or os.environ.get('PUSHPLUS_TOKEN', '')
        self.url = "http://www.pushplus.plus/send"
        
    def send(self, title, content, template='txt'):
        """发送推送"""
        if not self.token:
            logger.warning("未配置Pushplus Token，跳过推送")
            print(f"\n📱 【模拟推送】\n标题: {title}\n内容:\n{content}\n")
            return False
        
        if not HAS_REQUESTS:
            logger.error("未安装requests库，无法发送推送")
            print(f"\n📱 【模拟推送】\n标题: {title}\n内容:\n{content}\n")
            return False
        
        try:
            data = {
                'token': self.token,
                'title': title[:100],
                'content': content,
                'template': template
            }
            
            resp = requests.post(self.url, json=data, timeout=10)
            if resp.status_code == 200:
                result = resp.json()
                if result.get('code') == 200:
                    logger.info(f"推送成功: {title}")
                    return True
                else:
                    logger.error(f"推送失败: {result}")
            else:
                logger.error(f"推送请求失败: {resp.status_code}")
        except Exception as e:
            logger.error(f"推送异常: {e}")
            
        # 如果推送失败，至少打印到控制台
        print(f"\n📱 【推送内容】\n{title}\n{content}\n")
        return False


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
    
    @staticmethod
    def get_trend_data(days=5):
        """生成趋势数据"""
        trends = []
        for i in range(days):
            date = (datetime.now() - timedelta(days=days-i)).strftime('%m-%d')
            change = round(random.uniform(-2.0, 2.0), 2)
            trends.append({
                'date': date,
                'change': change
            })
        return trends


# ==================== 基金监控主程序 ====================

class FundMonitor:
    """基金监控主程序 - 定时推送版"""
    
    def __init__(self, push_token=None):
        self.funds = FUNDS
        self.mock_generator = MockDataGenerator()
        self.notifier = PushNotifier(push_token)
        self.last_push_time = None
        self.push_interval = 30  # 默认30分钟推送一次
        
    def run(self, mode):
        """运行指定模式"""
        logger.info(f"基金AI盯盘系统启动 - 模式: {mode}")
        
        if mode == 'morning':
            self.morning_analysis()
        elif mode == 'evening':
            self.evening_summary()
        elif mode == 'query':
            self.get_daily_change_summary(push=True)
        elif mode == 'daemon':
            self.run_daemon()
        else:
            print(f"未知模式: {mode}")
    
    def morning_analysis(self):
        """早盘分析"""
        print("\n🌅 早盘分析")
        print("-" * 40)
        
        analysis_result = []
        for fund in self.funds:
            data = self.mock_generator.get_realtime_data(fund)
            trends = self.mock_generator.get_trend_data()
            
            # 生成预测
            prediction = self._generate_prediction(data, trends)
            
            fund_result = {
                'name': fund['name'],
                'code': fund['code'],
                'price': data['price'],
                'change': data['change_percent'],
                'prediction': prediction,
                'trends': trends
            }
            analysis_result.append(fund_result)
            
            print(f"{fund['name']}:")
            print(f"  📊 当前价: {data['price']:.4f} ({data['change_percent']:+.2f}%)")
            print(f"  🔮 预测: {prediction['direction']} (概率{prediction['probability']}%)")
            print(f"  💡 建议: {prediction['advice']}")
            print()
        
        # 推送早盘分析报告
        self._push_morning_report(analysis_result)
    
    def evening_summary(self):
        """收盘复盘"""
        print("\n🌙 收盘复盘")
        print("-" * 40)
        
        summary_result = []
        for fund in self.funds:
            data = self.mock_generator.get_realtime_data(fund)
            
            # 生成复盘评价
            review = self._generate_review(data)
            
            fund_result = {
                'name': fund['name'],
                'code': fund['code'],
                'price': data['price'],
                'change': data['change_percent'],
                'review': review
            }
            summary_result.append(fund_result)
            
            print(f"{fund['name']}:")
            print(f"  📊 收盘价: {data['price']:.4f} ({data['change_percent']:+.2f}%)")
            print(f"  📝 评价: {review}")
            print()
        
        # 推送收盘复盘报告
        self._push_evening_report(summary_result)
    
    def get_daily_change_summary(self, push=False):
        """获取所有基金当日涨跌汇总"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 生成数据
        fund_data = []
        for fund in self.funds:
            data = self.mock_generator.get_realtime_data(fund)
            fund_data.append(data)
        
        # 按涨跌幅排序
        fund_data.sort(key=lambda x: x['change_percent'], reverse=True)
        
        # 统计信息
        total_funds = len(fund_data)
        up_funds = len([f for f in fund_data if f['change_percent'] > 0])
        down_funds = len([f for f in fund_data if f['change_percent'] < 0])
        flat_funds = total_funds - up_funds - down_funds
        avg_change = sum([f['change_percent'] for f in fund_data]) / total_funds if total_funds > 0 else 0
        
        # 找出表现最好的基金
        best_fund = max(fund_data, key=lambda x: x['change_percent']) if fund_data else None
        worst_fund = min(fund_data, key=lambda x: x['change_percent']) if fund_data else None
        
        # 生成组合建议
        portfolio_advice = self._generate_portfolio_advice(fund_data)
        
        # 打印到控制台
        self._print_summary(current_time, fund_data, up_funds, down_funds, flat_funds, 
                           avg_change, best_fund, worst_fund, portfolio_advice)
        
        # 推送通知
        if push:
            self._push_timely_report(current_time, fund_data, up_funds, down_funds, flat_funds,
                                    avg_change, best_fund, worst_fund, portfolio_advice)
        
        return {
            'time': current_time,
            'funds': fund_data,
            'stats': {
                'total': total_funds,
                'up': up_funds,
                'down': down_funds,
                'flat': flat_funds,
                'avg_change': avg_change
            },
            'best': best_fund,
            'worst': worst_fund,
            'advice': portfolio_advice
        }
    
    def run_daemon(self):
        """以守护进程模式运行，每隔30分钟执行一次"""
        if not HAS_SCHEDULE:
            print("❌ 错误: 未安装schedule库，无法运行守护进程模式")
            print("请安装: pip install schedule")
            return
        
        print(f"\n{'='*50}")
        print(f"🚀 基金监控守护进程启动")
        print(f"📅 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏰ 推送间隔: {self.push_interval}分钟")
        print(f"{'='*50}\n")
        
        # 立即执行一次
        logger.info("首次执行...")
        self.get_daily_change_summary(push=True)
        
        # 设置定时任务
        schedule.every(self.push_interval).minutes.do(self._timed_push)
        
        # 添加时间检查任务
        schedule.every().day.at("09:30").do(self._check_and_push, "morning_start")
        schedule.every().day.at("11:30").do(self._check_and_push, "morning_end")
        schedule.every().day.at("13:00").do(self._check_and_push, "afternoon_start")
        schedule.every().day.at("15:00").do(self._check_and_push, "afternoon_end")
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("守护进程被用户中断")
            print("\n👋 守护进程已停止")
    
    def _timed_push(self):
        """定时推送"""
        current_hour = datetime.now().hour
        
        # 非交易时间不推送（9:00-15:00）
        if current_hour < 9 or current_hour > 15:
            logger.info("非交易时间，跳过推送")
            return
        
        # 周末不推送
        if datetime.now().weekday() >= 5:  # 5=周六, 6=周日
            logger.info("周末，跳过推送")
            return
        
        logger.info("执行定时推送...")
        self.get_daily_change_summary(push=True)
    
    def _check_and_push(self, period):
        """检查并推送特定时段的信息"""
        logger.info(f"执行{period}时段推送...")
        self.get_daily_change_summary(push=True)
    
    def _generate_prediction(self, data, trends):
        """生成预测"""
        # 基于当前涨跌和趋势生成预测
        current_change = data['change_percent']
        avg_trend = sum([t['change'] for t in trends]) / len(trends) if trends else 0
        
        if current_change > 1.0 and avg_trend > 0:
            direction = '上涨'
            probability = random.randint(65, 85)
            advice = '建议持有或小幅加仓'
        elif current_change < -1.0 and avg_trend < 0:
            direction = '下跌'
            probability = random.randint(65, 85)
            advice = '建议观望或减仓'
        else:
            direction = random.choice(['震荡', '小幅上涨', '小幅下跌'])
            probability = random.randint(50, 65)
            advice = '建议持有观望'
        
        return {
            'direction': direction,
            'probability': probability,
            'advice': advice
        }
    
    def _generate_review(self, data):
        """生成复盘评价"""
        change = data['change_percent']
        
        if change > 2.0:
            return '强势上涨，表现优异'
        elif change > 1.0:
            return '温和上涨，走势稳健'
        elif change > 0:
            return '微幅上涨，趋势向好'
        elif change > -1.0:
            return '小幅下跌，正常调整'
        elif change > -2.0:
            return '明显下跌，需警惕风险'
        else:
            return '大幅下跌，建议关注'
    
    def _generate_portfolio_advice(self, fund_data):
        """生成组合建议"""
        up_ratio = len([f for f in fund_data if f['change_percent'] > 0]) / len(fund_data)
        
        if up_ratio > 0.6:
            return {
                'sentiment': '乐观',
                'suggestion': '保持较高仓位 (70-80%)',
                'focus': '领涨的指数基金'
            }
        elif up_ratio < 0.3:
            return {
                'sentiment': '谨慎',
                'suggestion': '降低仓位防御 (30-40%)',
                'focus': '跌幅较小的混合基金'
            }
        else:
            return {
                'sentiment': '中性',
                'suggestion': '均衡配置 (50-60%仓位)',
                'focus': '波动较小的基金'
            }
    
    def _print_summary(self, current_time, fund_data, up_funds, down_funds, flat_funds, 
                       avg_change, best_fund, worst_fund, portfolio_advice):
        """打印汇总信息"""
        print(f"\n{'='*80}")
        print(f"📊 基金实时监控 - {current_time}")
        print(f"{'='*80}")
        
        print(f"{'基金名称':<30} {'代码':<10} {'当前价':<10} {'涨跌幅':<10} {'状态'}")
        print(f"{'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
        
        for data in fund_data:
            if data['change_percent'] > 0:
                status = "🟢 上涨"
            elif data['change_percent'] < 0:
                status = "🔴 下跌"
            else:
                status = "⚪ 持平"
            
            print(f"{data['name']:<30} {data['code']:<10} {data['price']:<10.4f} {data['change_percent']:>+7.2f}%  {status}")
        
        print(f"{'-'*80}")
        print(f"📈 上涨: {up_funds} 只 | 📉 下跌: {down_funds} 只 | ⚖️ 持平: {flat_funds} 只")
        print(f"📊 平均涨跌幅: {avg_change:+.2f}%")
        
        if best_fund:
            print(f"🏆 今日最佳: {best_fund['name']} (+{best_fund['change_percent']:.2f}%)")
        if worst_fund:
            print(f"💔 今日最差: {worst_fund['name']} ({worst_fund['change_percent']:.2f}%)")
        
        print(f"\n📋 组合策略建议")
        print(f"  市场情绪: {portfolio_advice['sentiment']}")
        print(f"  建议仓位: {portfolio_advice['suggestion']}")
        print(f"  重点关注: {portfolio_advice['focus']}")
        print(f"{'='*80}\n")
    
    def _push_timely_report(self, current_time, fund_data, up_funds, down_funds, flat_funds,
                           avg_change, best_fund, worst_fund, portfolio_advice):
        """推送定时报告"""
        title = f"⏰ 基金实时监控 {current_time}"
        
        # 构建推送内容
        content = f"【市场概况】\n"
        content += f"📈 上涨: {up_funds}只\n"
        content += f"📉 下跌: {down_funds}只\n"
        content += f"⚖️ 持平: {flat_funds}只\n"
        content += f"📊 平均涨跌幅: {avg_change:+.2f}%\n\n"
        
        content += "【涨跌前5】\n"
        # 涨幅前5
        content += "📈 涨幅榜:\n"
        for data in fund_data[:5]:
            content += f"  • {data['name']}: {data['change_percent']:+.2f}%\n"
        
        # 跌幅前5
        content += "\n📉 跌幅榜:\n"
        for data in fund_data[-5:]:
            content += f"  • {data['name']}: {data['change_percent']:+.2f}%\n"
        
        if best_fund and worst_fund:
            content += f"\n🏆 最佳: {best_fund['name']} (+{best_fund['change_percent']:.2f}%)\n"
            content += f"💔 最差: {worst_fund['name']} ({worst_fund['change_percent']:.2f}%)\n"
        
        content += f"\n【策略建议】\n"
        content += f"市场情绪: {portfolio_advice['sentiment']}\n"
        content += f"建议仓位: {portfolio_advice['suggestion']}\n"
        content += f"重点关注: {portfolio_advice['focus']}\n"
        
        self.notifier.send(title, content, template='txt')
    
    def _push_morning_report(self, analysis_result):
        """推送早盘分析报告"""
        title = f"🌅 早盘分析 {datetime.now().strftime('%m-%d %H:%M')}"
        
        content = "【今日预测】\n"
        for fund in analysis_result[:5]:  # 只推送前5只
            content += f"• {fund['name']}: {fund['prediction']['direction']} "
            content += f"(概率{fund['prediction']['probability']}%)\n"
            content += f"  建议: {fund['prediction']['advice']}\n"
        
        self.notifier.send(title, content, template='txt')
    
    def _push_evening_report(self, summary_result):
        """推送收盘复盘报告"""
        title = f"🌙 收盘复盘 {datetime.now().strftime('%m-%d %H:%M')}"
        
        content = "【今日复盘】\n"
        up_count = len([f for f in summary_result if f['change'] > 0])
        down_count = len([f for f in summary_result if f['change'] < 0])
        
        content += f"上涨: {up_count}只 | 下跌: {down_count}只\n\n"
        
        content += "【表现回顾】\n"
        for fund in summary_result[:5]:
            content += f"• {fund['name']}: {fund['change']:+.2f}% - {fund['review']}\n"
        
        self.notifier.send(title, content, template='txt')


# ==================== 配置文件管理（修复版）====================

def load_config():
    """加载配置文件 - 修复版"""
    config_file = 'fund_config.json'
    default_config = {
        'pushplus_token': '',
        'push_interval': 30,
        'enable_morning_push': True,
        'enable_evening_push': True,
        'enable_timely_push': True
    }
    
    # 如果文件不存在，创建默认配置文件
    if not os.path.exists(config_file):
        try:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            print(f"✅ 已创建配置文件: {config_file}")
            print("请编辑配置文件添加PushPlus Token")
        except Exception as e:
            logger.error(f"创建配置文件失败: {e}")
        return default_config
    
    # 文件存在，尝试读取
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                # 文件为空，写入默认配置
                with open(config_file, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
                print(f"✅ 配置文件为空，已写入默认配置")
                return default_config
            
            config = json.loads(content)
            # 合并默认配置
            for key, value in default_config.items():
                if key not in config:
                    config[key] = value
            return config
    except json.JSONDecodeError as e:
        logger.error(f"配置文件格式错误: {e}")
        print(f"❌ 配置文件 {config_file} 格式错误，将使用默认配置")
        print("请检查文件内容或删除后重新生成")
        return default_config
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        return default_config


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
    parser = argparse.ArgumentParser(description='基金AI盯盘系统 - 定时推送版')
    parser.add_argument('--mode', choices=['morning', 'evening', 'query', 'auto', 'daemon'],
                       default='auto', help='运行模式')
    parser.add_argument('--token', help='PushPlus Token')
    parser.add_argument('--interval', type=int, default=30, help='推送间隔（分钟）')
    parser.add_argument('--init-config', action='store_true', help='初始化配置文件')
    parser.add_argument('--show-config', action='store_true', help='显示当前配置')
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
        # 初始化配置文件
        if args.init_config:
            config = load_config()
            print(f"\n✅ 配置文件已初始化: fund_config.json")
            print(f"配置文件内容:")
            print(json.dumps(config, ensure_ascii=False, indent=2))
            print(f"\n请编辑文件添加您的PushPlus Token后重新运行")
            return
        
        # 加载配置
        config = load_config()
        
        # 显示配置
        if args.show_config:
            print(f"\n当前配置:")
            print(json.dumps(config, ensure_ascii=False, indent=2))
            return
        
        # 获取推送Token（优先级：命令行 > 配置文件 > 环境变量）
        push_token = args.token or config.get('pushplus_token') or os.environ.get('PUSHPLUS_TOKEN', '')
        push_interval = args.interval or config.get('push_interval', 30)
        
        if not push_token and args.mode != 'query':
            print("⚠️ 未配置PushPlus Token，推送功能将不可用")
            print("可以通过以下方式设置：")
            print("1. 编辑 fund_config.json 文件")
            print("2. 使用 --token 参数指定")
            print("3. 设置环境变量 PUSHPLUS_TOKEN")
            print("4. 运行 --init-config 初始化配置文件\n")
        
        # 设置随机种子
        random.seed(datetime.now().timestamp())
        
        # 自动模式判断
        if args.mode == 'auto':
            detected_mode = get_current_mode()
            args.mode = detected_mode
        
        # 运行监控程序
        monitor = FundMonitor(push_token)
        monitor.push_interval = push_interval
        
        if args.mode == 'daemon':
            monitor.run_daemon()
        else:
            monitor.run(args.mode)
        
    except KeyboardInterrupt:
        print("\n👋 程序已停止")
    except Exception as e:
        logger.error(f"程序运行失败: {e}", exc_info=True)
        print(f"❌ 程序运行失败: {e}")


if __name__ == '__main__':
    main()
