#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金AI盯盘系统 - 增强版
功能：6:00-12:00早盘预测、16:00-18:00收盘复盘、非指定时间输出当日涨跌情况
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
        return max(-20, min(20, value))  # 限制涨跌幅在±20%内
    
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
                # 合并默认配置
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
        
        # 验证基金配置
        for i, fund in enumerate(self.data.get('funds', [])):
            if 'code' not in fund:
                errors.append(f"基金 #{i+1} 缺少code字段")
            elif not re.match(r'^\d{6}$', str(fund['code'])):
                errors.append(f"基金 {fund.get('code', 'unknown')} 代码格式不正确")
            
            if 'weight' in fund and not (0 < fund['weight'] <= 1):
                errors.append(f"基金 {fund.get('code', 'unknown')} 权重应在0-1之间")
        
        # 验证设置
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


# ==================== 数据获取模块 ====================

class FundDataFetcher:
    """基金数据获取"""
    
    def __init__(self):
        self.http = HttpClient()
        self.cache = {}
        self.validator = DataValidator()
    
    @lru_cache(maxsize=50)
    def get_realtime_data(self, fund_code):
        """获取实时估值"""
        if not self.validator.validate_fund_code(fund_code):
            logger.error(f"无效的基金代码: {fund_code}")
            return None
            
        cache_key = f"rt_{fund_code}"
        if cache_key in self.cache:
            cache_time, data = self.cache[cache_key]
            if datetime.now() - cache_time < timedelta(minutes=5):
                return data
        
        try:
            url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js"
            resp = self.http.get(url, timeout=10)
            
            match = re.search(r'jsonpgz\((.+)\);', resp)
            if match:
                data = json.loads(match.group(1))
                result = {
                    'code': fund_code,
                    'name': data.get('name', ''),
                    'price': float(data.get('gsz', 0)),
                    'previous': float(data.get('dwjz', 0)),
                    'change_percent': float(data.get('gszzl', 0)),
                    'change_amount': round(float(data.get('gsz', 0)) - float(data.get('dwjz', 0)), 4),
                    'time': data.get('gztime', ''),
                    'source': 'eastmoney'
                }
                self.cache[cache_key] = (datetime.now(), result)
                return result
        except Exception as e:
            logger.error(f"获取实时数据失败 {fund_code}: {e}")
        
        return None
    
    @lru_cache(maxsize=50)
    def get_history_data(self, fund_code, days=5):
        """获取前N天历史净值"""
        try:
            url = f"http://fund.eastmoney.com/f10/F10DataApi.aspx?type=lsjz&code={fund_code}&page=1&per={days + 5}"
            resp = self.http.get(url, timeout=10)
            
            match = re.search(r'var apidata=\{content:"(.+?)",records', resp)
            if not match:
                return []
            
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
            return []


# ==================== 新闻与情绪分析（基础版）====================

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


# ==================== 增强版新闻分析器 ====================

class EnhancedNewsAnalyzer(NewsAnalyzer):
    """增强版新闻分析器"""
    
    def __init__(self, config):
        super().__init__(config)
        # 扩展情绪词典
        self.sentiment_dict = {
            'strong_positive': ['涨停', '暴涨', '牛市', '大放水', '创新高', '重磅利好', '政策底', '估值修复'],
            'positive': ['利好', '上涨', '反弹', '增长', '增持', '买入', '降准', '降息', '刺激', '支持', 
                        '分红', '超预期', '盈利', '宽松', '扶持', '创新', '升级', '碳中和', '先进制造'],
            'neutral': ['震荡', '横盘', '调整', '观望', '平稳', '正常波动'],
            'negative': ['利空', '下跌', '减持', '卖出', '限购', '监管', '处罚', '违约', '暴雷', 
                        '亏损', '不及预期', '回撤', '紧缩', '加息', '风险', '波动'],
            'strong_negative': ['跌停', '暴跌', '熊市', '崩盘', '清盘', '腰斩', '重磅利空', '系统性风险']
        }
    
    def analyze_sentiment_advanced(self, news_list: List[Dict], fund_info: Optional[Dict] = None) -> Dict:
        """高级情绪分析，考虑新闻时效性和相关性"""
        if not news_list:
            return {'score': 0, 'level': '中性', 'confidence': 0, 'keywords': [], 'news_count': 0}
        
        total_score = 0
        total_weight = 0
        keywords = []
        relevant_news = []
        
        for news in news_list:
            # 基础情绪分
            base_score = self._calculate_base_sentiment(news.get('title', ''))
            
            # 权重计算
            weight = 1.0
            
            # 时效性权重（越新的新闻权重越高）
            days_old = self._get_news_age_days(news)
            if days_old <= 1:
                weight *= 1.5
            elif days_old <= 3:
                weight *= 1.2
            elif days_old <= 5:
                weight *= 0.8
            else:
                weight *= 0.5
            
            # 相关性权重
            if fund_info:
                relevance = self._calculate_relevance(news, fund_info)
                weight *= relevance
            
            # 来源权威性
            source_weight = self._get_source_weight(news.get('source', ''))
            weight *= source_weight
            
            total_score += base_score * weight
            total_weight += weight
            
            if abs(base_score) > 0.3:
                keywords.extend(self._extract_keywords(news.get('title', '')))
                relevant_news.append(news)
        
        # 计算加权平均分
        avg_score = total_score / total_weight if total_weight > 0 else 0
        avg_score = max(-1, min(1, avg_score))
        
        # 置信度计算
        confidence = min(1, len(news_list) / 10) * (0.5 + 0.5 * abs(avg_score))
        
        # 情绪等级
        level = self._get_sentiment_level(avg_score, confidence)
        
        return {
            'score': round(avg_score, 2),
            'level': level,
            'confidence': round(confidence, 2),
            'keywords': list(set(keywords))[:10],
            'relevant_news': relevant_news[:5],
            'news_count': len(news_list)
        }
    
    def _calculate_base_sentiment(self, text: str) -> float:
        """计算基础情绪分"""
        score = 0
        for word in self.sentiment_dict['strong_positive']:
            if word in text:
                score += 2
        for word in self.sentiment_dict['positive']:
            if word in text:
                score += 1
        for word in self.sentiment_dict['neutral']:
            if word in text:
                score += 0
        for word in self.sentiment_dict['negative']:
            if word in text:
                score -= 1
        for word in self.sentiment_dict['strong_negative']:
            if word in text:
                score -= 2
        
        return max(-1, min(1, score / 5))
    
    def _get_news_age_days(self, news: Dict) -> int:
        """获取新闻年龄（天数）"""
        try:
            time_str = news.get('time', '')
            if 'T' in time_str:
                news_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            else:
                news_time = datetime.strptime(time_str, '%Y-%m-%d %H:%M')
            return (datetime.now() - news_time).days
        except:
            return 5
    
    def _get_source_weight(self, source: str) -> float:
        """获取来源权重"""
        authoritative_sources = ['证监会', '央行', '国务院', '新华社', '人民日报']
        reliable_sources = ['东方财富', '新浪财经', '腾讯财经', '证券时报']
        
        if any(s in source for s in authoritative_sources):
            return 1.5
        elif any(s in source for s in reliable_sources):
            return 1.2
        else:
            return 0.8
    
    def _calculate_relevance(self, news: Dict, fund_info: Dict) -> float:
        """计算新闻与基金的相关性"""
        title = news.get('title', '')
        fund_name = fund_info.get('name', '')
        fund_type = fund_info.get('type', '')
        
        relevance = 1.0
        
        # 基金名称匹配
        if fund_name and any(word in title for word in fund_name.split()):
            relevance *= 1.3
        
        # 基金类型匹配
        type_keywords = {
            'index': ['指数', 'ETF', '成分'],
            'hybrid': ['混合', '配置'],
            'stock': ['股票', '股基'],
            'QDII': ['QDII', '海外', '全球']
        }
        
        if fund_type in type_keywords:
            if any(kw in title for kw in type_keywords[fund_type]):
                relevance *= 1.2
        
        return min(2.0, relevance)
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        for category in self.sentiment_dict.values():
            for word in category:
                if word in text and word not in keywords:
                    keywords.append(word)
        return keywords[:3]
    
    def _get_sentiment_level(self, score: float, confidence: float) -> str:
        """获取情绪等级"""
        if confidence < 0.3:
            return '不确定'
        
        if score > 0.6:
            return '强烈看多'
        elif score > 0.2:
            return '看多'
        elif score < -0.6:
            return '强烈看空'
        elif score < -0.2:
            return '看空'
        else:
            return '中性'


# ==================== AI分析引擎（基础版）====================

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
            change = (current - prev) / prev * 100
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
        
        # 关键信息补充
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


# ==================== 增强版AI分析引擎 ====================

class EnhancedAIFundAnalyzer(AIFundAnalyzer):
    """增强版AI分析引擎"""
    
    def __init__(self, config):
        super().__init__(config)
        self.news_analyzer = EnhancedNewsAnalyzer(config)
        self.validator = DataValidator()
        # 尝试导入numpy，如果不支持则使用简单计算
        try:
            import numpy as np
            self.np = np
            self.has_numpy = True
        except ImportError:
            self.has_numpy = False
            logger.warning("未安装numpy，使用简化版技术分析")
    
    def predict_today_enhanced(self, fund: Dict) -> Optional[Dict]:
        """增强版早盘预测"""
        code = fund['code']
        
        # 验证基金代码
        if not self.validator.validate_fund_code(code):
            logger.error(f"无效的基金代码: {code}")
            return None
        
        try:
            # 1. 技术面分析
            tech_analysis = self._technical_analysis(code)
            
            # 2. 资金面分析（模拟）
            money_flow = self._money_flow_analysis(code)
            
            # 3. 新闻情绪分析
            news = self.news_analyzer.fetch_news([code], days=5)
            sentiment = self.news_analyzer.analyze_sentiment_advanced(news, fund)
            
            # 4. 综合评分
            total_score = self._calculate_composite_score(
                tech_analysis,
                money_flow,
                sentiment
            )
            
            # 5. 预测生成
            prediction = self._generate_prediction(total_score, tech_analysis, sentiment)
            
            # 6. 风险评估
            risk_level = self._assess_risk(tech_analysis, money_flow)
            
            # 7. 操作建议
            advice = self._generate_detailed_advice(
                fund, 
                prediction, 
                total_score,
                tech_analysis,
                sentiment,
                risk_level
            )
            
            return {
                'fund': fund,
                'prediction': prediction['direction'],
                'probability': prediction['probability'],
                'confidence': prediction['confidence'],
                'tech_analysis': tech_analysis,
                'money_flow': money_flow,
                'sentiment': sentiment,
                'risk_level': risk_level,
                'advice': advice,
                'total_score': total_score
            }
            
        except Exception as e:
            logger.error(f"分析基金 {code} 失败: {e}")
            return None
    
    def _technical_analysis(self, code: str) -> Dict:
        """技术面分析"""
        history = self.fetcher.get_history_data(code, days=10)  # 获取10天数据做技术分析
        
        if not history or len(history) < 5:
            return {'trend': 'unknown', 'strength': 0, 'volatility': 0, 'rsi': 50, 'indicators': {}}
        
        # 计算各项技术指标
        closes = [h['nav'] for h in history]
        
        # 趋势强度
        if self.has_numpy:
            x = self.np.arange(len(closes))
            z = self.np.polyfit(x, closes, 1)
            trend_strength = z[0] * 100 / closes[0] if closes[0] != 0 else 0
        else:
            # 简化版趋势计算
            first_price = closes[0]
            last_price = closes[-1]
            trend_strength = ((last_price - first_price) / first_price * 100) / len(closes) if first_price != 0 else 0
        
        # 波动率
        returns = []
        for i in range(len(closes)-1):
            ret = (closes[i] - closes[i+1]) / closes[i+1] * 100 if closes[i+1] != 0 else 0
            returns.append(ret)
        
        if self.has_numpy:
            volatility = self.np.std(returns) if returns else 0
        else:
            # 简化版标准差计算
            if returns:
                mean_ret = sum(returns) / len(returns)
                variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
                volatility = variance ** 0.5
            else:
                volatility = 0
        
        # 相对强弱 (RSI) - 简化版
        if len(returns) >= 5:
            gains = [r for r in returns if r > 0]
            losses = [abs(r) for r in returns if r < 0]
            avg_gain = sum(gains) / len(gains) if gains else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            rs = avg_gain / avg_loss if avg_loss != 0 else 100
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50
        
        return {
            'trend': 'up' if trend_strength > 0.1 else 'down' if trend_strength < -0.1 else 'sideways',
            'strength': round(abs(trend_strength), 2),
            'volatility': round(volatility, 2),
            'rsi': round(rsi, 2),
            'indicators': {
                'trend_slope': round(trend_strength, 4),
                'volatility_level': 'high' if volatility > 2 else 'medium' if volatility > 1 else 'low'
            }
        }
    
    def _money_flow_analysis(self, code: str) -> Dict:
        """资金面分析"""
        # 这里可以接入真实的资金流向数据
        # 目前返回模拟数据
        return {
            'main_force_flow': 0,  # 主力资金流向
            'retail_flow': 0,       # 散户资金流向
            'north_flow': 0,        # 北向资金流向
            'sentiment': 'neutral'
        }
    
    def _calculate_composite_score(self, tech: Dict, money: Dict, sentiment: Dict) -> float:
        """计算综合评分"""
        weights = {
            'tech': 0.4,
            'sentiment': 0.4,
            'money': 0.2
        }
        
        score = 0
        
        # 技术面评分
        if tech['trend'] == 'up':
            score += weights['tech'] * 0.5
        elif tech['trend'] == 'down':
            score -= weights['tech'] * 0.5
        
        # 情绪面评分
        score += weights['sentiment'] * sentiment['score']
        
        # 资金面评分（模拟）
        if money['main_force_flow'] > 0:
            score += weights['money'] * 0.3
        
        return max(-1, min(1, score))
    
    def _generate_prediction(self, total_score: float, tech: Dict, sentiment: Dict) -> Dict:
        """生成预测结果"""
        if total_score > 0.3:
            direction = '上涨'
            base_prob = 60 + total_score * 30
        elif total_score < -0.3:
            direction = '下跌'
            base_prob = 60 - total_score * 30
        else:
            direction = '震荡'
            base_prob = 50
        
        # 根据技术指标调整概率
        if tech['trend'] == 'up' and direction == '上涨':
            base_prob *= 1.1
        elif tech['trend'] == 'down' and direction == '下跌':
            base_prob *= 1.1
        
        # 根据情绪置信度调整
        confidence = '高' if sentiment.get('confidence', 0) > 0.7 else '中' if sentiment.get('confidence', 0) > 0.4 else '低'
        
        return {
            'direction': direction,
            'probability': min(95, int(base_prob)),
            'confidence': confidence
        }
    
    def _assess_risk(self, tech: Dict, money: Dict) -> Dict:
        """风险评估"""
        risk_score = 0
        
        # 波动率风险
        if tech['volatility'] > 3:
            risk_score += 30
        elif tech['volatility'] > 2:
            risk_score += 20
        elif tech['volatility'] > 1:
            risk_score += 10
        
        # RSI风险
        if tech['rsi'] > 80:
            risk_score += 20  # 超买风险
        elif tech['rsi'] < 20:
            risk_score += 15  # 超卖风险
        
        level = '高' if risk_score > 40 else '中' if risk_score > 20 else '低'
        
        return {
            'level': level,
            'score': risk_score,
            'factors': {
                'volatility': tech['volatility'],
                'rsi': tech['rsi']
            }
        }
    
    def _generate_detailed_advice(self, fund: Dict, prediction: Dict, score: float,
                                  tech: Dict, sentiment: Dict, risk: Dict) -> Dict:
        """生成详细的操作建议"""
        advice = {
            'action': '持有',
            'action_color': 'blue',
            'reasons': [],
            'operations': [],
            'stop_loss': None,
            'take_profit': None
        }
        
        # 基础建议
        if prediction['direction'] == '上涨' and prediction['probability'] > 70:
            advice['action'] = '加仓'
            advice['action_color'] = 'red'
            advice['operations'].append("建议加仓10-20%，止损位设置在-3%")
            advice['stop_loss'] = -3.0
        elif prediction['direction'] == '下跌' and prediction['probability'] > 70:
            advice['action'] = '减仓'
            advice['action_color'] = 'green'
            advice['operations'].append("建议减仓20-30%，等待企稳信号")
        else:
            advice['operations'].append("建议持有观望，等待明确信号")
        
        # 技术面理由
        if tech['trend'] == 'up':
            advice['reasons'].append(f"技术面: 上升趋势，RSI{tech['rsi']}")
        elif tech['trend'] == 'down':
            advice['reasons'].append(f"技术面: 下降趋势，RSI{tech['rsi']}")
        
        # 情绪面理由
        if sentiment.get('score', 0) > 0.3:
            advice['reasons'].append(f"情绪面: {sentiment['level']}，置信度{sentiment.get('confidence', 0)}")
        
        # 风险提示
        if risk['level'] == '高':
            advice['operations'].append(f"⚠️ 高风险警示：波动率{tech['volatility']:.1f}%，建议严控仓位")
        
        # 止盈止损建议
        if fund.get('holdings', 0) > 0:
            advice['operations'].append("建议止盈位+5%，止损位-3%")
            advice['take_profit'] = 5.0
            advice['stop_loss'] = -3.0
        
        return advice


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
        for fund in funds:
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
        
        fund_changes.sort(key=lambda x: x['change_percent'], reverse=True)
        
        print(f"{'基金名称':<30} {'代码':<10} {'当前价':<10} {'昨日净值':<10} {'涨跌额':<10} {'涨跌幅(%)':<10}")
        print(f"{'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
        
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
            
            print(f"{fc['name']:<30} {fc['code']:<10} {fc['price']:<10.4f} {fc['previous']:<10.4f} {fc['change_amount']:<10.4f} {color_mark} {change_str}")
        
        total_funds = len(fund_changes)
        up_funds = len([f for f in fund_changes if f['change_percent'] > 0])
        down_funds = len([f for f in fund_changes if f['change_percent'] < 0])
        flat_funds = total_funds - up_funds - down_funds
        avg_change = sum([f['change_percent'] for f in fund_changes]) / total_funds if total_funds > 0 else 0
        
        print(f"{'-'*80}")
        print(f"📈 上涨: {up_funds} 只 | 📉 下跌: {down_funds} 只 | ⚖️ 持平: {flat_funds} 只")
        print(f"📊 平均涨跌幅: {avg_change:.2f}%")
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


# ==================== 增强版主程序 ====================

class EnhancedFundMonitor(FundMonitor):
    """增强版主程序"""
    
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        self.enhanced_analyzer = EnhancedAIFundAnalyzer(self.config)
        
        # 验证配置
        config_errors = self.config_manager.validate()
        if config_errors:
            for error in config_errors:
                logger.warning(f"配置警告: {error}")
    
    def run_enhanced_morning(self):
        """增强版早盘分析"""
        logger.info("开始增强版早盘分析...")
        print("开始增强版早盘分析...")
        
        funds = self.config.get_funds()
        if not funds:
            logger.warning("没有启用的基金")
            return
        
        predictions = []
        success_count = 0
        fail_count = 0
        
        for fund in funds:
            try:
                logger.info(f"分析 {fund['name']} ({fund['code']})...")
                print(f"分析 {fund['name']} ({fund['code']})...")
                pred = self.enhanced_analyzer.predict_today_enhanced(fund)
                if pred:
                    predictions.append(pred)
                    success_count += 1
                    logger.info(f"  预测: {pred['prediction']} (概率{pred['probability']}%)")
                    print(f"  预测: {pred['prediction']} (概率{pred['probability']}%)")
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"分析 {fund['code']} 失败: {e}")
                fail_count += 1
        
        if predictions:
            html = self._build_enhanced_morning_html(predictions)
            current_time = datetime.now().strftime('%H:%M')
            title = f"🌅 增强版AI早盘预测 | 成功{success_count}/{len(funds)} | {current_time}"
            self.notifier.send(title, html)
            self._save_predictions(predictions)
            logger.info(f"早盘分析完成: 成功{success_count}, 失败{fail_count}")
        else:
            logger.error("所有基金分析失败")
            self.notifier.send("⚠️ 早盘分析失败", "所有基金均无法获取数据")
    
    def _build_enhanced_morning_html(self, predictions: List[Dict]) -> str:
        """构建增强版HTML报告"""
        current_time = datetime.now().strftime('%H:%M')
        html = f"""
        <h2>🤖 增强版AI早盘预测报告</h2>
        <p style='color:#666'>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
        <p><b>分析维度:</b> 技术面(40%) + 情绪面(40%) + 资金面(20%)</p>
        <hr>
        """
        
        for pred in predictions:
            fund = pred['fund']
            advice = pred['advice']
            tech = pred['tech_analysis']
            sentiment = pred['sentiment']
            risk = pred['risk_level']
            
            pred_color = "red" if pred["prediction"]=="上涨" else "green" if pred["prediction"]=="下跌" else "gray"
            risk_color = "red" if risk['level']=='高' else "orange" if risk['level']=='中' else "green"
            
            html += f"""
            <div style='margin:15px 0;padding:15px;border-left:4px solid {advice["action_color"]};background:#f9f9f9'>
                <h3>{fund['name']} ({fund['code']})</h3>
                
                <table style='width:100%;border-collapse:collapse'>
                <tr>
                    <td style='width:33%'><b>今日预测:</b><br>
                        <span style='color:{pred_color};font-size:18px'>{pred['prediction']}</span><br>
                        <span style='color:#666'>概率{pred['probability']}%</span>
                    </td>
                    <td style='width:33%'><b>技术面:</b><br>
                        趋势:{tech['trend']}<br>
                        RSI:{tech['rsi']}
                    </td>
                    <td style='width:33%'><b>情绪面:</b><br>
                        {sentiment['level']}<br>
                        置信度:{sentiment.get('confidence', 0)}
                    </td>
                </tr>
                </table>
                
                <p><b>风险等级:</b> <span style='color:{risk_color}'>{risk['level']}</span></p>
                
                <p style='color:#0066cc'><b>操作建议 [{advice['action']}]:</b></p>
                <ul>
                {"".join(f"<li>{op}</li>" for op in advice['operations'])}
                </ul>
                
                <p><b>分析理由:</b></p>
                <ul>
                {"".join(f"<li>{r}</li>" for r in advice['reasons'])}
                </ul>
                
                <p style='color:#666;font-size:12px'><b>相关新闻:</b> {sentiment.get('news_count', 0)}条</p>
            </div>
            """
        
        return html


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
    parser = argparse.ArgumentParser(description='基金AI盯盘系统 - 增强版')
    parser.add_argument('--mode', choices=['morning', 'evening', 'init', 'query', 'auto', 'enhanced'],
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
        
        if args.mode == 'enhanced':
            monitor = EnhancedFundMonitor()
            monitor.run_enhanced_morning()
        else:
            monitor = FundMonitor()
            monitor.run(args.mode)
            
    except KeyboardInterrupt:
        print("\n⚠️ 程序被用户中断")
    except Exception as e:
        logger.error(f"程序运行失败: {e}", exc_info=True)
        print(f"❌ 程序运行失败: {e}")

if __name__ == '__main__':
    main()
