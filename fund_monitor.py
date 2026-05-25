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
from concurrent.futures import ThreadPoolExecutor, as_completed

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


# ==================== 加载基金列表 ====================

def load_funds_from_config(path='config.json'):
    """从 config.json 加载基金列表（含持仓、成本价等）"""
    default_funds = [
        {"code": "017548", "name": "天弘国证2000指数增强C"},
        {"code": "021620", "name": "天弘中证油气产业指数C"},
        {"code": "002170", "name": "东吴移动互联灵活配置混合C"},
    ]
    try:
        if not os.path.exists(path):
            logger.warning(f"配置文件 {path} 不存在，使用默认基金列表")
            return default_funds
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        funds = data.get('funds', [])
        if not funds:
            return default_funds
        return funds
    except Exception as e:
        logger.error(f"读取基金列表失败: {e}")
        return default_funds


# ==================== 东方财富数据接口 ====================

API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://fund.eastmoney.com/',
}


def fetch_realtime_estimate(code: str) -> Optional[Dict]:
    """从东方财富获取基金实时估值"""
    url = f'https://fundgz.1234567.com.cn/js/{code}.js'
    try:
        resp = requests.get(url, headers=API_HEADERS, timeout=5)
        resp.encoding = 'utf-8'
        text = resp.text.strip()
        # 解析 JSONP: jsonpgz({...});
        if text.startswith('jsonpgz(') and text.endswith(');'):
            text = text[8:-2]
        data = json.loads(text)
        return {
            'code': data['fundcode'],
            'name': data['name'],
            'est_nav': float(data['gsz']),
            'est_change_pct': float(data['gszzl']),
            'nav_date': data['jzrq'],
            'nav': float(data['dwjz']),
            'est_time': data['gztime'],
        }
    except Exception as e:
        logger.debug(f"获取估值失败 {code}: {e}")
        return None


def fetch_history(code: str, days: int = 10) -> List[Dict]:
    """获取基金历史净值"""
    url = f'https://api.fund.eastmoney.com/f10/lsjz'
    params = {
        'callback': 'jQuery',
        'fundCode': code,
        'pageIndex': 1,
        'pageSize': days,
        'startDate': '',
        'endDate': '',
    }
    try:
        resp = requests.get(url, params=params, headers=API_HEADERS, timeout=10)
        text = resp.text.strip()
        if text.startswith('jQuery(') and text.endswith(')'):
            text = text[7:-1]
        data = json.loads(text)
        rows = data.get('Data', {}).get('LSJZList', [])
        trends = []
        prev_nav = None
        for row in rows:
            nav = row.get('DWJZ', '')
            if nav:
                nav_f = float(nav)
                change = round((nav_f - prev_nav) / prev_nav * 100, 2) if prev_nav else 0.0
                trends.append({
                    'date': row['FSRQ'],
                    'nav': nav_f,
                    'change': change,
                })
                prev_nav = nav_f
        return trends
    except Exception as e:
        logger.debug(f"获取历史净值失败 {code}: {e}")
        return []


class RealDataFetcher:
    """基于东方财富 API 的真实数据获取器"""

    @staticmethod
    def get_realtime_data(fund: Dict) -> Dict:
        """获取单只基金实时数据"""
        code = fund['code']
        name = fund['name']
        result = fetch_realtime_estimate(code)

        if result:
            change_pct = result['est_change_pct']
            est_nav = result['est_nav']
            nav = result['nav']
        else:
            # 估值接口失败时用最新净值
            history = fetch_history(code, 1)
            if history:
                nav = history[0]['nav']
                change_pct = 0.0
                est_nav = nav
                logger.warning(f"{code} 估值接口不可用，使用最新净值")
            else:
                logger.error(f"{code} 所有数据接口均失败")
                return {
                    'code': code, 'name': name,
                    'price': 0, 'change_percent': 0,
                    'change_amount': 0, 'time': datetime.now().strftime('%H:%M'),
                    'type': fund.get('type', 'index'), 'error': True,
                }

        # 计算预计收益
        holdings = fund.get('holdings', 0)
        cost_price = fund.get('cost_price', 0)
        estimate_profit = 0
        if holdings > 0 and cost_price > 0:
            estimate_profit = round((est_nav - cost_price) * holdings, 2)
        else:
            estimate_profit = round(est_nav * change_pct / 100 * holdings, 2)

        return {
            'code': code,
            'name': name,
            'price': est_nav,
            'change_percent': round(change_pct, 2),
            'change_amount': round(est_nav - nav, 4),
            'profit': estimate_profit,
            'time': result['est_time'] if result else '--:--',
            'type': fund.get('type', 'index'),
            'nav_date': result['nav_date'] if result else '',
            'nav': nav,
        }

    @staticmethod
    def get_trend_data(code: str = '', days: int = 5) -> List[Dict]:
        """获取单只基金历史净值趋势"""
        if code:
            return fetch_history(code, days)
        return []
class FundMonitor:
    """基金监控主程序 - 定时推送版"""
    
    def __init__(self, push_token=None):
        self.funds = load_funds_from_config()
        self.fetcher = RealDataFetcher()
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
        def _fetch_fund_analysis(fund):
            data = self.fetcher.get_realtime_data(fund)
            if data.get('error'):
                raise Exception(f"{fund['code']} 数据获取失败")
            trends = self.fetcher.get_trend_data(code=fund['code'])
            prediction = self._generate_prediction(data, trends)
            return {'name': fund['name'], 'code': fund['code'], 'price': data['price'], 'change': data['change_percent'], 'prediction': prediction, 'trends': trends}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_fetch_fund_analysis, fund): fund for fund in self.funds}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=15)
                    analysis_result.append(result)
                    p = result['prediction']
                    print(f"{result['name']}:")
                    print(f"  📊 当前价: {result['price']:.4f} ({result['change']:+.2f}%)")
                    print(f"  🔮 预测: {p['direction']} (概率{p['probability']}%)")
                    print(f"  💡 建议: {p['advice']}")
                    print()
                except Exception as e:
                    fund = futures[future]
                    logger.error(f"{fund['code']} 获取失败: {e}")
        
        # 推送早盘分析报告
        self._push_morning_report(analysis_result)
    
    def evening_summary(self):
        """收盘复盘"""
        print("\n🌙 收盘复盘")
        print("-" * 40)
        
        summary_result = []
        def _fetch_fund_evening(fund):
            data = self.fetcher.get_realtime_data(fund)
            if data.get('error'):
                raise Exception(f"{fund['code']} 数据获取失败")
            review = self._generate_review(data)
            return {'name': fund['name'], 'code': fund['code'], 'price': data['price'], 'change': data['change_percent'], 'review': review}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_fetch_fund_evening, fund): fund for fund in self.funds}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=15)
                    summary_result.append(result)
                    print(f"{result['name']}:")
                    print(f"  📊 收盘价: {result['price']:.4f} ({result['change']:+.2f}%)")
                    print(f"  📝 评价: {result['review']}")
                    print()
                except Exception as e:
                    fund = futures[future]
                    logger.error(f"{fund['code']} 获取失败: {e}")
        
        # 推送收盘复盘报告
        self._push_evening_report(summary_result)
    
    def get_daily_change_summary(self, push=False):
        """获取所有基金当日涨跌汇总"""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 并发获取数据（加快速度）
        fund_data = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.fetcher.get_realtime_data, fund): fund for fund in self.funds}
            for future in as_completed(futures):
                try:
                    data = future.result(timeout=10)
                    fund_data.append(data)
                except Exception as e:
                    fund = futures[future]
                    logger.error(f"{fund['code']} 获取失败: {e}")
                    fund_data.append({
                        'code': fund['code'], 'name': fund['name'],
                        'price': 0, 'change_percent': 0,
                        'change_amount': 0, 'time': '--:--',
                        'type': fund.get('type', 'index'), 'error': True,
                    })
        
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
        """基于实时数据和历史趋势生成明确预测"""
        change = data['change_percent']
        avg_trend = sum([t['change'] for t in trends]) / len(trends) if trends else 0
        
        # 趋势强度
        trend_strength = 'strong' if abs(avg_trend) > 1.5 else ('mid' if abs(avg_trend) > 0.5 else 'weak')
        
        # ===== 上涨信号 =====
        if change > 3.0:
            return {'direction': '强势上涨', 'probability': 85,
                    'advice': '短期涨幅较大，已持有者可继续持有，不建议追高'}
        if change > 1.5 and avg_trend > 1.0:
            return {'direction': '震荡上涨', 'probability': 78,
                    'advice': '上涨趋势明确，建议持有待涨'}
        if change > 1.0 and trend_strength == 'weak':
            return {'direction': '短期冲高', 'probability': 62,
                    'advice': '单日涨幅较大但趋势偏弱，注意回调风险，暂不加仓'}
        if 0 < change <= 1.0 and avg_trend > 0.5:
            return {'direction': '稳步上行', 'probability': 72,
                    'advice': '温和上涨配合上升趋势，可适当加仓'}
        if 0 < change <= 1.0:
            return {'direction': '小幅上涨', 'probability': 55,
                    'advice': '微涨但趋势不明，建议持有观望'}
        
        # ===== 下跌信号 =====
        if change < -3.0:
            return {'direction': '大幅下跌', 'probability': 88,
                    'advice': '短期暴跌，不要恐慌抛售，等待企稳后再决策'}
        if change < -1.5 and avg_trend < -1.0:
            return {'direction': '持续下跌', 'probability': 82,
                    'advice': '下跌趋势确认，建议减仓避险'}
        if change < -1.0 and trend_strength == 'weak':
            return {'direction': '短期回调', 'probability': 60,
                    'advice': '单日下跌但趋势未破，可继续持有观察'}
        if -1.0 <= change < 0 and avg_trend < -0.5:
            return {'direction': '偏弱下行', 'probability': 68,
                    'advice': '趋势偏弱，建议降低仓位'}
        if -1.0 <= change < 0:
            return {'direction': '窄幅震荡', 'probability': 52,
                    'advice': '小幅下跌属正常波动，建议持有不动'}
        
        # ===== 横盘信号 =====
        if abs(change) <= 0.2:
            if trend_strength == 'strong':
                return {'direction': '趋势中继', 'probability': 70,
                        'advice': f'横盘整理，原有{"上涨" if avg_trend>0 else "下跌"}趋势可能延续'}
            return {'direction': '横盘整理', 'probability': 50,
                    'advice': '方向不明，建议等待信号再做决策'}
        
        return {'direction': '方向不明确', 'probability': 45,
                'advice': '数据不足，建议参考大盘走势综合判断'}
    
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
        """基于持仓基金整体表现给出明确组合建议"""
        up_ratio = len([f for f in fund_data if f['change_percent'] > 0]) / len(fund_data) if fund_data else 0
        avg_chg = sum(f['change_percent'] for f in fund_data) / len(fund_data) if fund_data else 0
        max_chg = max(f['change_percent'] for f in fund_data) if fund_data else 0
        min_chg = min(f['change_percent'] for f in fund_data) if fund_data else 0
        
        # 综合得分 [-10, +10]
        score = (up_ratio - 0.5) * 10 + avg_chg * 0.8
        
        if score > 4:
            return {
                'sentiment': '🔥 市场强势',
                'suggestion': f'整体上涨（平均{avg_chg:+.2f}%），可维持满仓，关注最强品种',
                'focus': f'领涨基金涨幅达{max_chg:+.2f}%，可适当追加'
            }
        elif score > 1.5:
            return {
                'sentiment': '📈 偏强震荡',
                'suggestion': f'涨多跌少（涨跌比{up_ratio:.0%}），建议保持6-7成仓位',
                'focus': f'重点关注涨幅稳定品种，避开{min_chg:+.2f}%的弱势基金'
            }
        elif score > -1.5:
            return {
                'sentiment': '➡️ 区间震荡',
                'suggestion': f'涨跌互现，平均{avg_chg:+.2f}%，建议半仓观望',
                'focus': '控制仓位在5成以内，等待方向明确'
            }
        elif score > -4:
            return {
                'sentiment': '📉 偏弱承压',
                'suggestion': f'跌多涨少（涨跌比{up_ratio:.0%}），建议降至3-4成仓位',
                'focus': f'跌幅最大的基金达{min_chg:+.2f}%，考虑是否止损'
            }
        else:
            return {
                'sentiment': '🔴 市场低迷',
                'suggestion': f'普跌行情（平均{avg_chg:+.2f}%），建议轻仓防御（1-2成）',
                'focus': '现金为王，等待企稳信号再入场'
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




