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


# ==================== 改进点1：自定义异常类 ====================

class FundMonitorError(Exception):
    """自定义异常类"""
    pass

class DataFetchError(FundMonitorError):
    """数据获取异常"""
    pass

class ConfigError(FundMonitorError):
    """配置异常"""
    pass


# ==================== 改进点2：增强的HTTP客户端 ====================

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


# ==================== 改进点3：数据验证器 ====================

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
                "name": "天弘国证 2000 指数增强 C",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "021620",
                "name": "天弘中证油气产业指数 C",
                "type": "index",
                "holdings": 0,
                "cost_price": 0.0,
                "weight": 0.07,
                "alert_threshold": 3.0,
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


# ==================== 改进点4：配置管理器 ====================

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


# ==================== 改进点5：增强版新闻分析器 ====================

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
                advice['operations'].append(f"上涨信号较弱，建议观望为主")
            advice['reason'].append(f"情绪面: {sentiment['level']} (分数:{sentiment['score']:+.2f})")
            
        elif prediction == '下跌':
            if score < -0.8:
                advice['action'] = '减仓'
                advice['action_color'] = 'green'
                advice['operations'].append(f"前{trend_data['trend_days']}天趋势走弱+情绪{sentiment['level']}，建议减仓20-30%")
            elif score < -0.4:
                advice['action'] = '减仓'
                advice['operations'].append(f"前{trend_data['
