#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金AI盯盘系统 - 修复版
修复了基金代码119529、119920等无法获取数据的问题
"""

import argparse
import json
import os
import sys
import re
import math
import logging
import pytz
import hashlib
import time
from functools import lru_cache
from datetime import datetime, timedelta, time as dt_time
from urllib import request, parse
from urllib.error import URLError, HTTPError
from typing import Dict, List, Optional, Any, Tuple

# 兼容处理
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

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


# ==================== 自定义异常类 ====================

class FundMonitorError(Exception):
    """自定义异常类"""
    pass

class DataFetchError(FundMonitorError):
    """数据获取异常"""
    pass

class ConfigError(FundMonitorError):
    """配置异常"""
    pass


# ==================== HTTP客户端 ====================

class HttpClient:
    """统一HTTP客户端，增加重试机制"""
    
    def __init__(self, max_retries=3, retry_delay=2):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        }
        self.cookie_jar = {}
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    def get(self, url, timeout=10) -> str:
        """带重试的GET请求"""
        for attempt in range(self.max_retries):
            try:
                if HAS_REQUESTS:
                    resp = requests.get(
                        url, 
                        headers=self.headers, 
                        timeout=timeout, 
                        cookies=self.cookie_jar
                    )
                    resp.raise_for_status()
                    self.cookie_jar.update(resp.cookies.get_dict())
                    return resp.text
                else:
                    return self._urllib_get(url, timeout)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise DataFetchError(f"GET请求失败 {url}: {e}")
                logger.warning(f"请求失败，{self.retry_delay * (attempt + 1)}秒后重试 ({attempt+1}/{self.max_retries})")
                time.sleep(self.retry_delay * (attempt + 1))
        return ""
    
    def post(self, url, data, timeout=10):
        """POST请求"""
        if HAS_REQUESTS:
            try:
                resp = requests.post(url, data=data, headers=self.headers, timeout=timeout)
                return resp.text
            except Exception as e:
                logger.error(f"requests POST失败: {e}，尝试urllib")
                return self._urllib_post(url, data, timeout)
        else:
            return self._urllib_post(url, data, timeout)
    
    def _urllib_get(self, url, timeout):
        """使用 urllib 的 GET"""
        try:
            req = request.Request(url, headers=self.headers)
            with request.urlopen(req, timeout=timeout) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"urllib GET失败: {e}")
            return ""
    
    def _urllib_post(self, url, data, timeout):
        """使用 urllib 的 POST"""
        try:
            encoded_data = parse.urlencode(data).encode('utf-8')
            req = request.Request(url, data=encoded_data, headers=self.headers)
            with request.urlopen(req, timeout=timeout) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"urllib POST失败: {e}")
            return ""


# ==================== 数据验证器 ====================

class DataValidator:
    """数据验证器"""
    
    @staticmethod
    def validate_fund_code(code: str) -> bool:
        """验证基金代码格式"""
        return bool(re.match(r'^\d{6}$', code))
    
    @staticmethod
    def validate_percentage(value: float) -> float:
        """验证百分比值范围"""
        return max(-20, min(20, value))
    
    @staticmethod
    def validate_date(date_str: str) -> bool:
        """验证日期格式"""
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except:
            return False


# ==================== 配置管理 ====================

class Config:
    """配置管理类"""
    
    DEFAULT_CONFIG = {
        "funds": [
            {
                "code": "017548",
                "name": "天弘国证2000指数增强C",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "021620",
                "name": "天弘中证油气产业指数C",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 3.0,
                "enabled": True
            },
            {
                "code": "002170",
                "name": "东吴移动互联灵活配置混合C",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "022486",
                "name": "国金中证A500指数增强C",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "017484",
                "name": "财通资管数字经济混合C",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "011803",
                "name": "富顺长城宁景6个月持有期混合A",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "021580",
                "name": "华夏人工智能ETF联接D",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "017730",
                "name": "嘉实全球产业升级股票(QDII)A",
                "type": "stock",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 3.0,
                "enabled": True
            },
            {
                "code": "000071",
                "name": "华夏恒生ETF联接(QDII)A",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "002580",
                "name": "泰信鑫选灵活配置混合C",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "019993",
                "name": "创金合信北证50成份指数增强A",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "018124",
                "name": "永赢先进制造智选混合A",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "021298",
                "name": "中欧北证50成份指数A",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "015916",
                "name": "永赢医药创新智选混合C",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "016539",
                "name": "鹏华碳中和主题混合A",
                "type": "hybrid",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "119529",
                "name": "易方达创业板ETF联接A",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "021175",
                "name": "华安北证50成份指数C",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            },
            {
                "code": "119920",
                "name": "易方达深证300ETF联接A",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "011612",
                "name": "华夏科创50ETF联接A",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.5,
                "enabled": True
            }
        ],
        "settings": {
            "pushplus_token": "",  # 请在这里填写你的PushPlus Token
            "morning_analysis_start": "06:00",
            "morning_analysis_end": "12:00",
            "evening_summary_start": "16:00",
            "evening_summary_end": "18:00",
            "news_keywords": ["重仓股", "基金经理", "分红", "限购", "降准", "降息", "IPO", "北向资金", "南向资金", "政策", "监管"]
        },
        "ai_settings": {
            "trend_days": 5,
            "news_weight": 0.4,
            "trend_weight": 0.6,
            "confidence_threshold": 0.6
        }
    }
    
    def __init__(self, config_path='config.json'):
        self.config_path = config_path
        self.data = self.load()
        self.pushplus_token = os.environ.get('PUSHPLUS_TOKEN', self.data['settings']['pushplus_token'])
    
    def load(self):
        """加载配置"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for key, value in self.DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                    elif isinstance(value, dict) and isinstance(config[key], dict):
                        for sub_key, sub_value in value.items():
                            if sub_key not in config[key]:
                                config[key][sub_key] = sub_value
                return config
        except FileNotFoundError:
            if os.environ.get('GITHUB_ACTIONS'):
                logger.info("GitHub Actions环境：使用默认内存配置")
                return self.DEFAULT_CONFIG.copy()
            self.save(self.DEFAULT_CONFIG)
            logger.info(f"配置文件不存在，已创建默认配置: {self.config_path}")
            return self.DEFAULT_CONFIG.copy()
        except Exception as e:
            logger.error(f"加载配置失败: {e}，使用默认配置")
            return self.DEFAULT_CONFIG.copy()
    
    def save(self, data=None):
        """保存配置"""
        if os.environ.get('GITHUB_ACTIONS'):
            logger.info("GitHub Actions环境：跳过保存配置文件")
            return
        if data is None:
            data = self.data
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
    
    def get_funds(self, enabled_only=True):
        funds = self.data.get('funds', [])
        if enabled_only:
            funds = [f for f in funds if f.get('enabled', True)]
        return funds
    
    def get_setting(self, key, default=None):
        return self.data.get('settings', {}).get(key, default)
    
    def get_ai_setting(self, key, default=None):
        return self.data.get('ai_settings', {}).get(key, default)


# ==================== 配置管理器 ====================

class ConfigManager(Config):
    """配置管理器，增加验证功能"""
    
    CONFIG_VERSION = '2.0'
    
    def __init__(self, config_path='config.json'):
        super().__init__(config_path)
        self.migrate()
    
    def validate(self) -> List[str]:
        """验证配置有效性"""
        errors = []
        
        for i, fund in enumerate(self.data.get('funds', [])):
            if 'code' not in fund:
                errors.append(f"基金 #{i+1} 缺少code字段")
            elif not re.match(r'^\d{6}$', str(fund['code'])):
                errors.append(f"基金 {fund.get('code', 'unknown')} 代码格式不正确")
            
            if 'weight' in fund and not (0 < fund['weight'] <= 1):
                errors.append(f"基金 {fund.get('code', 'unknown')} 权重应在0-1之间")
        
        settings = self.data.get('settings', {})
        if not settings.get('pushplus_token'):
            errors.append("PushPlus Token未配置，将无法接收推送")
        
        return errors
    
    def migrate(self):
        """迁移旧版本配置"""
        if self.data.get('version') == self.CONFIG_VERSION:
            return
        
        logger.info("开始迁移配置到新版本...")
        self.data['version'] = self.CONFIG_VERSION
        
        if 'funds' in self.data:
            for fund in self.data['funds']:
                if 'alert_threshold' not in fund:
                    fund['alert_threshold'] = 2.0
                if 'enabled' not in fund:
                    fund['enabled'] = True
        
        self.save()
        logger.info("配置迁移完成")


# ==================== 修复版数据获取模块 ====================

class FundDataFetcher:
    """基金数据获取 - 修复版，支持多种数据源"""
    
    def __init__(self):
        self.http = HttpClient()
        self.cache = {}
        self.validator = DataValidator()
    
    @lru_cache(maxsize=50)
    def get_realtime_data(self, fund_code):
        """获取实时估值 - 支持多个数据源"""
        if not self.validator.validate_fund_code(fund_code):
            logger.error(f"无效的基金代码: {fund_code}")
            return None
            
        cache_key = f"rt_{fund_code}"
        if cache_key in self.cache:
            cache_time, data = self.cache[cache_key]
            if datetime.now() - cache_time < timedelta(minutes=5):
                return data
        
        # 尝试多个数据源
        data_sources = [
            self._get_from_eastmoney,
            self._get_from_tiantian,
            self._get_from_sina
        ]
        
        for source_func in data_sources:
            try:
                result = source_func(fund_code)
                if result:
                    self.cache[cache_key] = (datetime.now(), result)
                    return result
            except Exception as e:
                logger.debug(f"数据源 {source_func.__name__} 失败: {e}")
                continue
        
        logger.error(f"所有数据源都无法获取基金 {fund_code} 的实时数据")
        return None
    
    def _get_from_eastmoney(self, fund_code):
        """从东方财富获取实时数据"""
        try:
            # 对于以11开头的基金代码，使用不同的接口
            if fund_code.startswith('11'):
                url = f"http://fund.eastmoney.com/pingzhongdata/{fund_code}.js"
                resp = self.http.get(url, timeout=10)
                
                # 尝试解析Data_netWorthTrend数据
                match = re.search(r'var Data_netWorthTrend = (\[.*?\]);', resp)
                if match:
                    import json
                    trend_data = json.loads(match.group(1))
                    if trend_data and len(trend_data) > 0:
                        latest = trend_data[-1]
                        # 获取昨日净值
                        history = self.get_history_data(fund_code, days=2)
                        previous = history[1]['nav'] if len(history) > 1 else 0
                        
                        return {
                            'code': fund_code,
                            'name': self._get_fund_name(fund_code),
                            'price': latest.get('y', 0),
                            'previous': previous,
                            'change_percent': ((latest.get('y', 0) - previous) / previous * 100) if previous else 0,
                            'change_amount': latest.get('y', 0) - previous if previous else 0,
                            'time': datetime.now().strftime('%Y-%m-%d %H:%M'),
                            'source': 'eastmoney_pingzhong'
                        }
            else:
                # 标准接口
                url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js"
                resp = self.http.get(url, timeout=10)
                
                match = re.search(r'jsonpgz\((.+)\);', resp)
                if match:
                    data = json.loads(match.group(1))
                    return {
                        'code': fund_code,
                        'name': data.get('name', ''),
                        'price': float(data.get('gsz', 0)),
                        'previous': float(data.get('dwjz', 0)),
                        'change_percent': float(data.get('gszzl', 0)),
                        'change_amount': round(float(data.get('gsz', 0)) - float(data.get('dwjz', 0)), 4),
                        'time': data.get('gztime', ''),
                        'source': 'eastmoney'
                    }
        except Exception as e:
            logger.debug(f"东方财富获取失败 {fund_code}: {e}")
        return None
    
    def _get_from_tiantian(self, fund_code):
        """从天天基金获取实时数据"""
        try:
            url = f"http://fund.10jqka.com.cn/{fund_code}/"
            resp = self.http.get(url, timeout=10)
            
            if HAS_BS4:
                soup = BeautifulSoup(resp, 'html.parser')
                # 尝试解析页面数据
                price_elem = soup.find('span', class_='gz_num')
                if price_elem:
                    price = float(price_elem.text)
                    
                    # 获取基金名称
                    name_elem = soup.find('h1', class_='fund_name')
                    name = name_elem.text.strip() if name_elem else fund_code
                    
                    return {
                        'code': fund_code,
                        'name': name,
                        'price': price,
                        'previous': 0,  # 需要从其他接口获取
                        'change_percent': 0,
                        'change_amount': 0,
                        'time': datetime.now().strftime('%Y-%m-%d %H:%M'),
                        'source': 'tiantian'
                    }
        except Exception as e:
            logger.debug(f"天天基金获取失败 {fund_code}: {e}")
        return None
    
    def _get_from_sina(self, fund_code):
        """从新浪财经获取实时数据"""
        try:
            url = f"http://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getFundInfo?fund={fund_code}"
            resp = self.http.get(url, timeout=10)
            
            data = json.loads(resp)
            if data and data.get('result') and data['result'].get('data'):
                fund_data = data['result']['data']
                return {
                    'code': fund_code,
                    'name': fund_data.get('fund_name', ''),
                    'price': float(fund_data.get('net_value', 0)),
                    'previous': float(fund_data.get('total_net_value', 0)),
                    'change_percent': float(fund_data.get('daily_profit', 0)),
                    'change_amount': float(fund_data.get('daily_profit_amount', 0)),
                    'time': fund_data.get('jzrq', ''),
                    'source': 'sina'
                }
        except Exception as e:
            logger.debug(f"新浪财经获取失败 {fund_code}: {e}")
        return None
    
    def _get_fund_name(self, fund_code):
        """获取基金名称"""
        try:
            url = f"http://fund.eastmoney.com/{fund_code}.html"
            resp = self.http.get(url, timeout=10)
            
            if HAS_BS4:
                soup = BeautifulSoup(resp, 'html.parser')
                name_elem = soup.find('div', class_='fundDetail-tit')
                if name_elem:
                    return name_elem.text.replace(f'({fund_code})', '').strip()
        except:
            pass
        return fund_code
    
    @lru_cache(maxsize=50)
    def get_history_data(self, fund_code, days=5):
        """获取前N天历史净值 - 支持多个数据源"""
        try:
            # 对于以11开头的基金代码，使用不同的接口
            if fund_code.startswith('11'):
                return self._get_history_from_other(fund_code, days)
            
            url = f"http://fund.eastmoney.com/f10/F10DataApi.aspx?type=lsjz&code={fund_code}&page=1&per={days + 5}"
            resp = self.http.get(url, timeout=10)
            
            match = re.search(r'var apidata=\{content:"(.+?)",records', resp)
            if not match:
                return self._get_history_from_other(fund_code, days)
            
            html = match.group(1).replace('\\', '')
            
            if HAS_BS4:
                soup = BeautifulSoup(html, 'html.parser')
                rows = soup.find_all('tr')
            else:
                rows = []
                for tr in re.findall(r'<tr>(.+?)</tr>', html, re.DOTALL):
                    rows.append(tr)
            
            history = []
            for row in rows:
                if HAS_BS4:
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        date = cols[0].get_text(strip=True)
                        nav = cols[1].get_text(strip=True)
                        change = cols[2].get_text(strip=True) if len(cols) > 2 else ''
                else:
                    cols = re.findall(r'<td>(.+?)</td>', str(row))
                    if len(cols) >= 2:
                        date = re.sub(r'<[^>]+>', '', cols[0]).strip()
                        nav = re.sub(r'<[^>]+>', '', cols[1]).strip()
                        change = re.sub(r'<[^>]+>', '', cols[2]).strip() if len(cols) > 2 else ''
                    else:
                        continue
                
                try:
                    if nav and float(nav) > 0:
                        history.append({
                            'date': date,
                            'nav': float(nav),
                            'change': change
                        })
                except:
                    continue
            
            return history[:days] if len(history) >= days else history
        except Exception as e:
            logger.error(f"获取历史数据失败 {fund_code}: {e}")
            return self._get_history_from_other(fund_code, days)
    
    def _get_history_from_other(self, fund_code, days=5):
        """从其他源获取历史数据"""
        try:
            # 使用新浪财经接口
            url = f"http://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getNav?symbol={fund_code}&date={datetime.now().strftime('%Y-%m-%d')}"
            resp = self.http.get(url, timeout=10)
            
            data = json.loads(resp)
            history = []
            
            if data and data.get('result') and data.get('result').get('data'):
                for item in data['result']['data'][:days]:
                    history.append({
                        'date': item.get('fbrq', ''),
                        'nav': float(item.get('jjjz', 0)),
                        'change': item.get('jzzzl', '0')
                    })
            
            return history
        except Exception as e:
            logger.error(f"从其他源获取历史数据失败 {fund_code}: {e}")
            return []


# ==================== 新闻与情绪分析 ====================

class NewsAnalyzer:
    """新闻获取与情绪分析"""
    
    def __init__(self, config):
        self.config = config
        self.http = HttpClient()
        if os.environ.get('GITHUB_ACTIONS'):
            self.cache_file = '/tmp/fund_news_cache.json'
        else:
            self.cache_file = os.path.join(os.getcwd(), 'fund_news_cache.json')
    
    def load_cache(self):
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def save_cache(self, cache):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)
        except:
            pass
    
    def fetch_news(self, fund_codes, days=5):
        """获取前5天相关新闻和政策"""
        all_news = []
        cache = self.load_cache()
        cutoff_time = datetime.now() - timedelta(days=days)
        
        for code in fund_codes:
            try:
                news = self._fetch_fund_announcement(code)
                for n in news:
                    if self._is_new_news(n, cache, cutoff_time):
                        all_news.append(n)
                        cache[n['id']] = datetime.now().isoformat()
            except Exception as e:
                logger.error(f"获取基金公告失败 {code}: {e}")
        
        try:
            policy_news = self._fetch_policy_news(days)
            for n in policy_news:
                if self._is_new_news(n, cache, cutoff_time):
                    all_news.append(n)
                    cache[n['id']] = datetime.now().isoformat()
        except Exception as e:
            logger.error(f"获取政策新闻失败: {e}")
        
        self.save_cache(cache)
        return all_news
    
    def _fetch_policy_news(self, days=5):
        """获取宏观政策新闻"""
        news = []
        try:
            url = "http://data.eastmoney.com/cjsj/hbgy.html"
            resp = self.http.get(url, timeout=10)
            
            if HAS_BS4:
                soup = BeautifulSoup(resp, 'html.parser')
                items = soup.find_all('a', href=re.compile('news'))[:10]
                for item in items:
                    title = item.get_text(strip=True)
                    if any(keyword in title for keyword in self.config.get_setting('news_keywords', [])):
                        news.append({
                            'id': f"policy_{hash(title) % 10000}",
                            'title': title,
                            'source': '宏观政策',
                            'time': datetime.now().strftime('%Y-%m-%d %H:%M'),
                            'type': 'policy',
                            'fund_code': 'policy'
                        })
        except Exception as e:
            logger.error(f"抓取政策新闻失败: {e}")
        return news
    
    def _is_new_news(self, news, cache, cutoff_time):
        """检查是否为前5天的新闻"""
        news_id = news['id']
        if news_id in cache:
            return False
        
        try:
            if 'T' in news['time']:
                news_time = datetime.fromisoformat(news['time'].replace('Z', '+00:00'))
            else:
                news_time = datetime.strptime(news['time'], '%Y-%m-%d %H:%M')
            return news_time > cutoff_time
        except:
            return True
    
    def _fetch_fund_announcement(self, fund_code):
        """获取基金公告"""
        news = []
        try:
            url = f"http://fund.eastmoney.com/f10/F10DataApi.aspx?type=jjgg&code={fund_code}&page=1&per=10"
            resp = self.http.get(url, timeout=10)
            
            match = re.search(r'var apidata=\{content:"(.+?)",records', resp)
            if match:
                html = match.group(1).replace('\\', '')
                
                if HAS_BS4:
                    soup = BeautifulSoup(html, 'html.parser')
                    rows = soup.find_all('tr')
                else:
                    rows = re.findall(r'<tr>(.+?)</tr>', html, re.DOTALL)
                
                for row in rows:
                    if HAS_BS4:
                        cols = row.find_all('td')
                        if len(cols) >= 3:
                            title = cols[0].get_text(strip=True)
                            date = cols[2].get_text(strip=True)
                        else:
                            continue
                    else:
                        cols = re.findall(r'<td>(.+?)</td>', str(row))
                        if len(cols) >= 3:
                            title = re.sub(r'<[^>]+>', '', cols[0]).strip()
                            date = re.sub(r'<[^>]+>', '', cols[2]).strip()
                        else:
                            continue
                    
                    news.append({
                        'id': f"ann_{fund_code}_{date}_{hash(title) % 10000}",
                        'title': title,
                        'source': '基金公告',
                        'time': f"{datetime.now().year}-{date} 00:00",
                        'type': 'announcement',
                        'fund_code': fund_code
                    })
        except Exception as e:
            logger.error(f"获取公告失败: {e}")
        
        return news
    
    def analyze_sentiment(self, news_list, fund_info=None):
        """基础情绪分析"""
        if not news_list:
            return {'score': 0, 'level': '中性', 'keywords': [], 'relevant_news': []}
        
        positive_words = ['利好', '上涨', '反弹', '增长', '增持', '买入', '降准', '降息', '刺激', '支持', '分红', '超预期']
        negative_words = ['利空', '下跌', '调整', '减持', '卖出', '限购', '监管', '处罚', '违约', '暴雷', '亏损']
        strong_positive = ['涨停', '暴涨', '牛市', '大放水', '创新高', '重磅利好']
        strong_negative = ['跌停', '暴跌', '熊市', '崩盘', '清盘', '腰斩', '重磅利空']
        
        score = 0
        keywords = []
        relevant_news = []
        
        for news in news_list:
            title = news.get('title', '')
            news_score = 0
            matched = []
            
            for word in strong_positive:
                if word in title:
                    news_score += 2
                    matched.append(word)
            for word in positive_words:
                if word in title:
                    news_score += 1
                    matched.append(word)
            for word in strong_negative:
                if word in title:
                    news_score -= 2
                    matched.append(word)
            for word in negative_words:
                if word in title:
                    news_score -= 1
                    matched.append(word)
            
            if news.get('type') == 'policy':
                news_score *= 2.0
            
            score += news_score
            
            if matched:
                keywords.extend(matched)
                relevant_news.append(news)
        
        avg_score = max(-1, min(1, score / max(len(news_list) * 0.5, 3)))
        
        if avg_score > 0.6:
            level = '强烈看多'
        elif avg_score > 0.2:
            level = '看多'
        elif avg_score < -0.6:
            level = '强烈看空'
        elif avg_score < -0.2:
            level = '看空'
        else:
            level = '中性'
        
        return {
            'score': round(avg_score, 2),
            'level': level,
            'keywords': list(set(keywords))[:5],
            'relevant_news': relevant_news[:5]
        }


# ==================== AI分析引擎 ====================

class AIFundAnalyzer:
    """AI基金分析引擎"""
    
    def __init__(self, config):
        self.config = config
        self.fetcher = FundDataFetcher()
        self.news_analyzer = NewsAnalyzer(config)
    
    def analyze_trend(self, fund_code, days=5):
        """分析前5天基金走势"""
        history = self.fetcher.get_history_data(fund_code, days)
        if len(history) < days:
            logger.warning(f"{fund_code} 前{days}天数据不足，仅获取到{len(history)}天")
            return None
        
        trend_data = {
            'trend_days': days,
            'daily_data': [],
            'total_change': 0,
            'avg_change': 0,
            'max_change': 0,
            'min_change': 0,
            'trend': '震荡'
        }
        
        changes = []
        for i in range(len(history)-1):
            current = history[i]['nav']
            prev = history[i+1]['nav']
            change = (current - prev) / prev * 100 if prev != 0 else 0
            changes.append(change)
            trend_data['daily_data'].append({
                'date': history[i]['date'],
                'nav': current,
                'change': round(change, 2)
            })
        
        trend_data['total_change'] = round(sum(changes), 2)
        trend_data['avg_change'] = round(sum(changes) / len(changes), 2) if changes else 0
        trend_data['max_change'] = round(max(changes), 2) if changes else 0
        trend_data['min_change'] = round(min(changes), 2) if changes else 0
        
        if trend_data['avg_change'] > 0.5:
            trend_data['trend'] = '上升'
        elif trend_data['avg_change'] < -0.5:
            trend_data['trend'] = '下降'
        else:
            trend_data['trend'] = '震荡'
        
        return trend_data
    
    def predict_today(self, fund):
        """早盘预测"""
        code = fund['code']
        days = self.config.get_ai_setting('trend_days', 5)
        
        logger.info(f"正在分析 {fund['name']} 前{days}天数据...")
        
        trend_data = self.analyze_trend(code, days)
        if not trend_data:
            return None
        
        news = self.news_analyzer.fetch_news([code], days=days)
        sentiment = self.news_analyzer.analyze_sentiment(news, fund)
        
        trend_score = (trend_data['avg_change'] / 10) * self.config.get_ai_setting('trend_weight', 0.6)
        news_score = sentiment['score'] * self.config.get_ai_setting('news_weight', 0.4)
        total_score = trend_score + news_score
        
        if total_score > 0.3:
            prediction = '上涨'
            prob = min(95, 50 + total_score * 60)
        elif total_score < -0.3:
            prediction = '下跌'
            prob = min(95, 50 - total_score * 60)
        else:
            prediction = '震荡'
            prob = 50
        
        advice = self._generate_morning_advice(fund, prediction, total_score, trend_data, sentiment)
        
        return {
            'fund': fund,
            'prediction': prediction,
            'probability': round(prob, 1),
            'confidence': '高' if abs(total_score) > 0.6 else '中' if abs(total_score) > 0.3 else '低',
            'trend_5d': trend_data,
            'sentiment': sentiment,
            'total_score': round(total_score, 2),
            'advice': advice,
            'news_summary': self._summarize_news(news[:5])
        }
    
    def _generate_morning_advice(self, fund, prediction, score, trend_data, sentiment):
        """早盘持仓建议"""
        advice = {
            'action': '持有',
            'action_color': 'blue',
            'reason': [f"前{trend_data['trend_days']}天整体{trend_data['trend']}，平均涨跌幅{trend_data['avg_change']:+.2f}%"],
            'operations': []
        }
        
        if prediction == '上涨':
            if score > 0.8:
                advice['action'] = '加仓'
                advice['action_color'] = 'red'
                advice['operations'].append(f"前{trend_data['trend_days']}天趋势向好+情绪{sentiment['level']}，建议加仓10-20%")
            elif score > 0.4:
                advice['action'] = '持有'
                advice['operations'].append(f"前{trend_data['trend_days']}天趋势平稳+情绪中性偏多，继续持有")
            else:
                advice['action'] = '观望'
                advice['operations'].append("上涨信号较弱，建议观望为主")
            advice['reason'].append(f"情绪面: {sentiment['level']} (分数:{sentiment['score']:+.2f})")
            
        elif prediction == '下跌':
            if score < -0.8:
                advice['action'] = '减仓'
                advice['action_color'] = 'green'
                advice['operations'].append(f"前{trend_data['trend_days']}天趋势走弱+情绪{sentiment['level']}，建议减仓20-30%")
            elif score < -0.4:
                advice['action'] = '减仓'
                advice['operations'].append(f"前{trend_data['trend_days']}天震荡下跌+情绪偏空，建议减仓10%")
            else:
                advice['action'] = '观望'
                advice['operations'].append("下跌信号较弱，暂停加仓，观察走势")
            advice['reason'].append(f"情绪面: {sentiment['level']} (分数:{sentiment['score']:+.2f})")
        else:
            advice['action'] = '持有'
            advice['operations'].append(f"前{trend_data['trend_days']}天震荡走势+情绪中性，建议持有不动或网格交易")
        
        if sentiment['keywords']:
            advice['reason'].append(f"核心影响因素: {', '.join(sentiment['keywords'][:3])}")
        
        if trend_data['max_change'] > 2 or trend_data['min_change'] < -2:
            advice['reason'].append(f"前{trend_data['trend_days']}天波动较大（最大{trend_data['max_change']:+.2f}%），注意风险控制")
        
        return advice
    
    def _summarize_news(self, news_list):
        """总结前5天相关新闻和政策"""
        if not news_list:
            return "无重大新闻和政策影响"
        
        summaries = []
        for n in news_list[:5]:
            title = n.get('title', '')[:50]
            source = n.get('source', '未知')
            news_type = '【政策】' if n.get('type') == 'policy' else '【公告】'
            summaries.append(f"• {news_type}{source}: {title}...")
        
        return "<br>".join(summaries)


# ==================== 推送模块 ====================

class PushNotifier:
    """Pushplus推送"""
    
    def __init__(self, token):
        self.token = token
        self.http = HttpClient()
        self.url = "http://www.pushplus.plus/send"
    
    def send(self, title, content, template='html'):
        if not self.token:
            logger.warning("未配置Pushplus Token，跳过推送")
            return False
        
        data = {
            'token': self.token,
            'title': title[:100],
            'content': content,
            'template': template
        }
        
        try:
            resp = self.http.post(self.url, data, timeout=10)
            try:
                result = json.loads(resp) if isinstance(resp, str) else {'code': 200}
            except:
                result = {'code': 200} if '200' in str(resp) or 'success' in str(resp).lower() else {'code': 0}
            
            if result.get('code') == 200:
                logger.info(f"推送成功: {title}")
                return True
            else:
                logger.error(f"推送失败: {resp[:200]}")
                return False
        except Exception as e:
            logger.error(f"推送请求失败: {e}")
            return False


# ==================== 基金监控主程序 ====================

class FundMonitor:
    """基金监控主程序"""
    
    def __init__(self):
        self.config = Config()
        self.fetcher = FundDataFetcher()
        self.analyzer = AIFundAnalyzer(self.config)
        self.notifier = PushNotifier(self.config.pushplus_token)
        if os.environ.get('GITHUB_ACTIONS'):
            self.prediction_file = '/tmp/fund_predictions.json'
        else:
            self.prediction_file = os.path.join(os.getcwd(), 'fund_predictions.json')
    
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
            logger.error(f"未知模式: {mode}")
            print(f"未知模式: {mode}")
    
    def morning_analysis(self):
        """早盘分析"""
        logger.info("开始早盘AI分析...")
        print("开始早盘AI分析...")
        
        funds = self.config.get_funds()
        if not funds:
            logger.warning("没有启用的基金")
            print("没有启用的基金")
            return
        
        predictions = []
        
        for fund in funds:
            logger.info(f"分析 {fund['name']} ({fund['code']}) 前5天数据...")
            print(f"分析 {fund['name']} ({fund['code']}) 前5天数据...")
            pred = self.analyzer.predict_today(fund)
            if pred:
                predictions.append(pred)
                logger.info(f"  预测: {pred['prediction']} (概率{pred['probability']}%)")
                print(f"  预测: {pred['prediction']} (概率{pred['probability']}%)")
        
        if not predictions:
            self.notifier.send("⚠️ 早盘分析失败", "无法获取基金数据")
            return
        
        html = self._build_morning_html(predictions)
        current_time = datetime.now().strftime('%H:%M')
        title = f"🌅 {current_time} AI早盘预测 | {datetime.now().strftime('%m-%d')}"
        
        self.notifier.send(title, html)
        self._save_predictions(predictions)
        logger.info(f"{current_time} 早盘分析完成并已推送")
        print(f"{current_time} 早盘分析完成并已推送")
    
    def evening_summary(self):
        """收盘复盘"""
        logger.info("开始收盘AI复盘...")
        print("开始收盘AI复盘...")
        
        morning_preds = self._load_predictions()
        if not morning_preds:
            logger.warning("未找到早盘预测数据，跳过复盘")
            print("未找到早盘预测数据，跳过复盘")
            return
        
        funds = self.config.get_funds()
        summaries = []
        
        for fund in funds:
            code = fund['code']
            morning_pred = morning_preds.get(code, {})
            
            if not morning_pred:
                continue
            
            logger.info(f"复盘 {fund['name']} ({code}) 今日表现...")
            print(f"复盘 {fund['name']} ({code}) 今日表现...")
            
            realtime = self.fetcher.get_realtime_data(code)
            if realtime:
                actual_direction = '上涨' if realtime['change_percent'] > 0.1 else '下跌' if realtime['change_percent'] < -0.1 else '震荡'
                pred_correct = (morning_pred.get('prediction') == actual_direction)
                
                summaries.append({
                    'fund': fund,
                    'realtime': realtime,
                    'morning_prediction': morning_pred,
                    'actual_direction': actual_direction,
                    'prediction_correct': pred_correct
                })
        
        if not summaries:
            self.notifier.send("⚠️ 收盘复盘失败", "无法获取数据")
            return
        
        html = self._build_evening_html(summaries)
        correct_count = sum(1 for s in summaries if s['prediction_correct'])
        accuracy = correct_count / len(summaries) * 100 if summaries else 0
        current_time = datetime.now().strftime('%H:%M')
        
        title = f"🌙 {current_time} AI收盘复盘 | 准确率{accuracy:.0f}%"
        self.notifier.send(title, html)
        logger.info(f"{current_time} 收盘复盘完成并已推送")
        print(f"{current_time} 收盘复盘完成并已推送")
    
    def get_daily_change_summary(self):
        """获取所有基金当日涨跌汇总"""
        logger.info("开始生成当日涨跌汇总")
        print(f"\n{'='*80}")
        print(f"📊 基金当日涨跌情况汇总 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}")
        
        funds = self.config.get_funds()
        if not funds:
            print("⚠️ 未配置任何基金")
            return
        
        fund_changes = []
        failed_funds = []
        
        for fund in funds:
            try:
                realtime_data = self.fetcher.get_realtime_data(fund['code'])
                if realtime_data:
                    fund_changes.append({
                        'name': fund['name'],
                        'code': fund['code'],
                        'price': realtime_data['price'],
                        'previous': realtime_data['previous'],
                        'change_percent': realtime_data['change_percent'],
                        'change_amount': realtime_data['change_amount'],
                        'update_time': realtime_data['time']
                    })
                else:
                    failed_funds.append(fund['name'])
            except Exception as e:
                logger.error(f"获取基金 {fund['code']} 数据失败: {e}")
                failed_funds.append(fund['name'])
        
        if not fund_changes:
            print("❌ 无法获取任何基金数据")
            return
        
        fund_changes.sort(key=lambda x: x['change_percent'], reverse=True)
        
        print(f"{'基金名称':<30} {'代码':<10} {'当前价':<10} {'昨日净值':<10} {'涨跌额':<10} {'涨跌幅(%)':<10} {'更新时间'}")
        print(f"{'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*20}")
        
        for fc in fund_changes:
            if fc['change_percent'] > 0:
                change_str = f"+{fc['change_percent']:.2f}"
                color_mark = "🟢"
            elif fc['change_percent'] < 0:
                change_str = f"{fc['change_percent']:.2f}"
                color_mark = "🔴"
            else:
                change_str = "0.00"
                color_mark = "⚪"
            
            print(f"{fc['name']:<30} {fc['code']:<10} {fc['price']:<10.4f} {fc['previous']:<10.4f} {fc['change_amount']:<10.4f} {color_mark} {change_str:<9} {fc['update_time']}")
        
        total_funds = len(fund_changes)
        up_funds = len([f for f in fund_changes if f['change_percent'] > 0])
        down_funds = len([f for f in fund_changes if f['change_percent'] < 0])
        flat_funds = total_funds - up_funds - down_funds
        avg_change = sum([f['change_percent'] for f in fund_changes]) / total_funds if total_funds > 0 else 0
        
        print(f"{'-'*80}")
        print(f"📈 上涨: {up_funds} 只 | 📉 下跌: {down_funds} 只 | ⚖️ 持平: {flat_funds} 只")
        print(f"📊 平均涨跌幅: {avg_change:.2f}%")
        
        if failed_funds:
            print(f"⚠️ 以下基金数据获取失败: {', '.join(failed_funds[:5])}")
        
        print(f"{'='*80}\n")
    
    def _build_morning_html(self, predictions):
        """构建早盘分析HTML"""
        current_time = datetime.now().strftime('%H:%M')
        html = f"<h2>🤖 {current_time} AI早盘预测报告</h2>"
        html += f"<p style='color:#666'>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>"
        html += "<p><b>分析依据：前5天涨跌盘面 + 相关新闻政策</b></p><hr>"
        
        for pred in predictions:
            fund = pred['fund']
            advice = pred['advice']
            color = advice['action_color']
            pred_color = "red" if pred["prediction"]=="上涨" else "green" if pred["prediction"]=="下跌" else "gray"
            
            html += f"""
            <div style='margin:15px 0;padding:10px;border-left:4px solid {color};background:#f9f9f9'>
                <h3>{fund['name']} ({fund['code']})</h3>
                <p><b>今日预测:</b> <span style='color:{pred_color};font-size:16px'>{pred['prediction']} (概率{pred['probability']}%)</span></p>
                <p><b>持仓建议:</b> <span style='color:{color};font-weight:bold'>{advice['action']}</span></p>
                <ul>{"".join(f"<li>{r}</li>" for r in advice['reason'])}</ul>
                <p><b>操作建议:</b><br>{"".join(f"• {op}<br>" for op in advice['operations'])}</p>
                <p style='color:#666;font-size:12px'><b>相关新闻政策:</b><br>{pred['news_summary']}</p>
            </div>
            """
        
        return html
    
    def _build_evening_html(self, summaries):
        """构建复盘HTML"""
        current_time = datetime.now().strftime('%H:%M')
        html = f"<h2>🌙 {current_time} AI收盘复盘报告</h2>"
        html += f"<p style='color:#666'>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p><hr>"
        
        correct = sum(1 for s in summaries if s['prediction_correct'])
        total = len(summaries)
        accuracy = correct/total*100 if total else 0
        
        html += f"<p><b>预测准确率:</b> {correct}/{total} ({accuracy:.0f}%)</p>"
        
        for summary in summaries:
            fund = summary['fund']
            rt = summary['realtime']
            morning = summary['morning_prediction']
            
            pred_status = "✅ 准确" if summary['prediction_correct'] else "❌ 偏差"
            pred_color = "green" if summary['prediction_correct'] else "red"
            actual_color = "red" if rt["change_percent"]>0 else "green"
            
            html += f"""
            <div style='margin:15px 0;padding:10px;border-left:4px solid {pred_color};background:#f9f9f9'>
                <h3>{fund['name']} ({fund['code']})</h3>
                <p><b>早盘预测:</b> {morning.get('prediction', '未知')} | 
                <b>今日实际:</b> <span style='color:{actual_color}'>{rt['change_percent']:+.2f}%</span>
                <span style='color:{pred_color};margin-left:10px'>{pred_status}</span></p>
            </div>
            """
        
        return html
    
    def _save_predictions(self, predictions):
        """保存早盘预测数据"""
        try:
            data = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'predictions': {p['fund']['code']: p for p in predictions}
            }
            with open(self.prediction_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, default=str, ensure_ascii=False, indent=2)
            logger.info(f"早盘预测数据已保存至 {self.prediction_file}")
        except Exception as e:
            logger.error(f"保存预测失败: {e}")
    
    def _load_predictions(self):
        """加载当日早盘预测数据"""
        try:
            with open(self.prediction_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    return data.get('predictions', {})
        except Exception as e:
            logger.error(f"加载预测数据失败: {e}")
        return {}


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

def setup_logging():
    """设置日志"""
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

def main():
    parser = argparse.ArgumentParser(description='基金AI盯盘系统 - 修复版')
    parser.add_argument('--mode', choices=['morning', 'evening', 'init', 'query', 'auto'],
                       default='auto', help='运行模式')
    parser.add_argument('--config', default='config.json', help='配置文件路径')
    parser.add_argument('--validate', action='store_true', help='验证配置')
    args = parser.parse_args()
    
    setup_logging()
    
    try:
        if args.mode == 'init':
            config = ConfigManager(args.config)
            config.save()
            print(f"✅ 已创建配置文件: {args.config}")
            print("请编辑配置文件添加PushPlus Token和基金信息")
            return
        
        if args.validate:
            config = ConfigManager(args.config)
            errors = config.validate()
            if errors:
                print("❌ 配置验证失败:")
                for error in errors:
                    print(f"  - {error}")
            else:
                print("✅ 配置验证通过")
            return
        
        if args.mode == 'auto':
            detected_mode = get_current_mode()
            args.mode = detected_mode
        
        monitor = FundMonitor()
        monitor.run(args.mode)
            
    except KeyboardInterrupt:
        print("\n⚠️ 程序被用户中断")
    except Exception as e:
        logger.error(f"程序运行失败: {e}", exc_info=True)
        print(f"❌ 程序运行失败: {e}")

if __name__ == '__main__':
    main()
