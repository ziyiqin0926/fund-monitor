#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基金AI盯盘系统 - 本地部署版
功能：实时监控、AI早盘预测、AI收盘复盘、新闻情绪分析、持仓建议
"""

import argparse
import json
import os
import sys
import re
import math
import time
import schedule
from datetime import datetime, timedelta, time
from urllib import request, parse
from urllib.error import URLError, HTTPError

# 兼容处理：如果没有 requests，使用 urllib
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("警告: 未安装 requests，使用 urllib 替代")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("警告: 未安装 beautifulsoup4，HTML解析功能受限")


# ==================== HTTP请求封装 ====================

class HttpClient:
    """统一HTTP客户端，兼容 requests 和 urllib"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        self.cookie_jar = {}
    
    def get(self, url, timeout=10):
        """GET请求"""
        if HAS_REQUESTS:
            try:
                resp = requests.get(url, headers=self.headers, timeout=timeout, cookies=self.cookie_jar)
                self.cookie_jar.update(resp.cookies.get_dict())
                return resp.text
            except Exception as e:
                print(f"requests GET失败: {e}，尝试urllib")
                return self._urllib_get(url, timeout)
        else:
            return self._urllib_get(url, timeout)
    
    def post(self, url, data, timeout=10):
        """POST请求"""
        if HAS_REQUESTS:
            try:
                resp = requests.post(url, data=data, headers=self.headers, timeout=timeout)
                return resp.text
            except Exception as e:
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
            print(f"urllib GET失败: {e}")
            return ""
    
    def _urllib_post(self, url, data, timeout):
        """使用 urllib 的 POST"""
        try:
            encoded_data = parse.urlencode(data).encode('utf-8')
            req = request.Request(url, data=encoded_data, headers=self.headers)
            with request.urlopen(req, timeout=timeout) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"urllib POST失败: {e}")
            return ""


# ==================== 配置管理 ====================

class Config:
    """配置管理类"""
    
    DEFAULT_CONFIG = {
        "funds": [
            {
                "code": "000001",
                "name": "华夏成长混合",
                "type": "hybrid",
                "holdings": 1000,
                "cost_price": 1.2,
                "weight": 0.3,
                "alert_threshold": 2.0,
                "enabled": True
            },
            {
                "code": "110022",
                "name": "易方达消费行业",
                "type": "stock",
                "holdings": 500,
                "cost_price": 3.5,
                "weight": 0.3,
                "alert_threshold": 3.0,
                "enabled": True
            }
        ],
        "settings": {
            "pushplus_token": "",  # 请在这里填写你的PushPlus Token
            "morning_analysis_time": "09:00",
            "evening_summary_time": "16:00",
            "monitor_interval": 10,
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
        # 本地运行：优先读取配置文件中的token，而非环境变量
        # 移除Gitee Go的环境变量依赖
        self.pushplus_token = self.data['settings']['pushplus_token']
    
    def load(self):
        """加载配置"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 合并默认配置
                for key, value in self.DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                return config
        except FileNotFoundError:
            # 创建默认配置（本地路径）
            self.save(self.DEFAULT_CONFIG)
            print(f"配置文件不存在，已创建默认配置: {self.config_path}")
            return self.DEFAULT_CONFIG.copy()
        except Exception as e:
            print(f"加载配置失败: {e}，使用默认配置")
            return self.DEFAULT_CONFIG.copy()
    
    def save(self, data=None):
        """保存配置"""
        if data is None:
            data = self.data
        try:
            # 确保配置文件保存在当前目录（而非/tmp）
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def get_funds(self, enabled_only=True):
        funds = self.data.get('funds', [])
        if enabled_only:
            funds = [f for f in funds if f.get('enabled', True)]
        return funds
    
    def get_setting(self, key, default=None):
        return self.data.get('settings', {}).get(key, default)
    
    def get_ai_setting(self, key, default=None):
        return self.data.get('ai_settings', {}).get(key, default)


# ==================== 数据获取模块 ====================

class FundDataFetcher:
    """基金数据获取"""
    
    def __init__(self):
        self.http = HttpClient()
        self.cache = {}
    
    def get_realtime_data(self, fund_code):
        """获取实时估值"""
        cache_key = f"rt_{fund_code}"
        if cache_key in self.cache:
            cache_time, data = self.cache[cache_key]
            if datetime.now() - cache_time < timedelta(minutes=5):
                return data
        
        try:
            # 天天基金实时估值
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
                    'time': data.get('gztime', ''),
                    'source': 'eastmoney'
                }
                self.cache[cache_key] = (datetime.now(), result)
                return result
        except Exception as e:
            print(f"获取实时数据失败 {fund_code}: {e}")
        
        return None
    
    def get_history_data(self, fund_code, days=10):
        """获取历史净值"""
        try:
            url = f"http://fund.eastmoney.com/f10/F10DataApi.aspx?type=lsjz&code={fund_code}&page=1&per={days}"
            resp = self.http.get(url, timeout=10)
            
            match = re.search(r'var apidata=\{content:"(.+?)",records', resp)
            if not match:
                return []
            
            html = match.group(1).replace('\\', '')
            
            if HAS_BS4:
                soup = BeautifulSoup(html, 'html.parser')
                rows = soup.find_all('tr')
            else:
                # 简易正则解析
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
            
            return history
        except Exception as e:
            print(f"获取历史数据失败 {fund_code}: {e}")
            return []


# ==================== 新闻与情绪分析 ====================

class NewsAnalyzer:
    """新闻获取与情绪分析"""
    
    def __init__(self, config):
        self.config = config
        self.http = HttpClient()
        # 本地缓存路径调整（Windows兼容）
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
    
    def fetch_news(self, fund_codes, hours=48):
        """获取新闻"""
        all_news = []
        cache = self.load_cache()
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        # 获取基金公告
        for code in fund_codes:
            try:
                news = self._fetch_fund_announcement(code)
                for n in news:
                    if self._is_new_news(n, cache, cutoff_time):
                        all_news.append(n)
                        cache[n['id']] = datetime.now().isoformat()
            except Exception as e:
                print(f"获取新闻失败 {code}: {e}")
        
        self.save_cache(cache)
        return all_news
    
    def _is_new_news(self, news, cache, cutoff_time):
        """检查是否为新新闻"""
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
            print(f"获取公告失败: {e}")
        
        return news
    
    def analyze_sentiment(self, news_list, fund_info=None):
        """情绪分析"""
        if not news_list:
            return {'score': 0, 'level': '中性', 'keywords': [], 'relevant_news': []}
        
        # 情绪词典
        positive_words = ['利好', '上涨', '反弹', '增长', '增持', '买入', '降准', '降息', '刺激', '支持', '分红', '超预期', '盈利', '增长']
        negative_words = ['利空', '下跌', '调整', '减持', '卖出', '限购', '监管', '处罚', '违约', '暴雷', '亏损', '不及预期', '回撤']
        strong_positive = ['涨停', '暴涨', '牛市', '大放水', '创新高']
        strong_negative = ['跌停', '暴跌', '熊市', '崩盘', '清盘', '腰斩']
        
        score = 0
        keywords = []
        relevant_news = []
        
        for news in news_list:
            title = news.get('title', '')
            text = title
            
            news_score = 0
            matched = []
            
            for word in strong_positive:
                if word in text:
                    news_score += 2
                    matched.append(word)
            for word in positive_words:
                if word in text:
                    news_score += 1
                    matched.append(word)
            for word in strong_negative:
                if word in text:
                    news_score -= 2
                    matched.append(word)
            for word in negative_words:
                if word in text:
                    news_score -= 1
                    matched.append(word)
            
            # 加权
            if news.get('type') == 'policy':
                news_score *= 1.5
            elif news.get('type') == 'announcement':
                news_score *= 1.2
            
            score += news_score
            
            if matched:
                keywords.extend(matched)
                relevant_news.append(news)
        
        # 归一化
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
        """分析基金走势"""
        history = self.fetcher.get_history_data(fund_code, days + 5)
        if len(history) < days:
            return None
        
        recent = history[:days]
        navs = [d['nav'] for d in recent]
        
        # 计算涨跌幅
        changes = []
        for i in range(len(navs)-1):
            change = (navs[i] - navs[i+1]) / navs[i+1] * 100
            changes.append(change)
        
        avg_change = sum(changes) / len(changes) if changes else 0
        
        # 动量
        if len(changes) >= 3:
            recent_momentum = sum(changes[:3]) / 3
            past_momentum = sum(changes[3:]) / max(len(changes)-3, 1)
            momentum = (recent_momentum - past_momentum) / 10
        else:
            momentum = avg_change / 10
        
        momentum = max(-1, min(1, momentum))
        
        # 波动率
        if len(changes) > 1:
            variance = sum((x - avg_change) ** 2 for x in changes) / len(changes)
            volatility = math.sqrt(variance)
        else:
            volatility = 0
        
        # 趋势判断
        if avg_change > 0.5 and momentum > 0:
            trend = '上升'
        elif avg_change < -0.5 and momentum < 0:
            trend = '下降'
        else:
            trend = '震荡'
        
        return {
            'trend': trend,
            'momentum': round(momentum, 2),
            'volatility': round(volatility, 2),
            'avg_change': round(avg_change, 2),
            'data': recent
        }
    
    def predict_today(self, fund):
        """早盘预测"""
        code = fund['code']
        
        # 趋势分析
        trend_data = self.analyze_trend(code, self.config.get_ai_setting('trend_days', 5))
        if not trend_data:
            return None
        
        # 新闻情绪
        news = self.news_analyzer.fetch_news([code], hours=48)
        sentiment = self.news_analyzer.analyze_sentiment(news, fund)
        
        # 综合预测
        trend_score = trend_data['momentum'] * self.config.get_ai_setting('trend_weight', 0.6)
        news_score = sentiment['score'] * self.config.get_ai_setting('news_weight', 0.4)
        total_score = trend_score + news_score
        
        if total_score > 0.5:
            prediction = '上涨'
            prob = min(95, 50 + total_score * 50)
        elif total_score < -0.5:
            prediction = '下跌'
            prob = min(95, 50 - total_score * 50)
        else:
            prediction = '震荡'
            prob = 50
        
        # 生成建议
        advice = self._generate_advice(fund, prediction, total_score, trend_data, sentiment)
        
        return {
            'fund': fund,
            'prediction': prediction,
            'probability': round(prob, 1),
            'confidence': '高' if abs(total_score) > 0.6 else '中' if abs(total_score) > 0.3 else '低',
            'trend': trend_data,
            'sentiment': sentiment,
            'total_score': round(total_score, 2),
            'advice': advice,
            'news_summary': self._summarize_news(news[:3])
        }
    
    def summarize_day(self, fund, morning_prediction):
        """收盘复盘"""
        code = fund['code']
        
        realtime = self.fetcher.get_realtime_data(code)
        if not realtime:
            return None
        
        actual_change = realtime['change_percent']
        actual_direction = '上涨' if actual_change > 0.1 else '下跌' if actual_change < -0.1 else '震荡'
        
        pred = morning_prediction.get('prediction', '震荡')
        pred_correct = (pred == actual_direction) or (pred == '震荡' and abs(actual_change) < 0.5)
        
        deviation_reason = self._analyze_deviation(morning_prediction, actual_change, actual_direction)
        updated_advice = self._update_advice(fund, morning_prediction, actual_change, actual_direction)
        
        return {
            'fund': fund,
            'realtime': realtime,
            'morning_prediction': morning_prediction,
            'actual_direction': actual_direction,
            'actual_change': actual_change,
            'prediction_correct': pred_correct,
            'deviation_analysis': deviation_reason,
            'updated_advice': updated_advice,
            'accuracy_score': 100 if pred_correct else 0
        }
    
    def _generate_advice(self, fund, prediction, score, trend, sentiment):
        """生成持仓建议"""
        holdings = fund.get('holdings', 0)
        cost = fund.get('cost_price', 0)
        
        advice = {
            'action': '持有',
            'action_color': 'blue',
            'reason': [],
            'operations': []
        }
        
        if prediction == '上涨':
            if score > 0.8:
                advice['action'] = '加仓'
                advice['action_color'] = 'red'
                advice['operations'].append('今日可逢低加仓10-20%')
            else:
                advice['action'] = '持有'
                advice['operations'].append('继续持有，等待上涨')
            advice['reason'].append(f'技术面向好，{sentiment["level"]}情绪支撑')
            
        elif prediction == '下跌':
            if score < -0.8:
                advice['action'] = '减仓'
                advice['action_color'] = 'green'
                advice['operations'].append('建议减仓20-30%避险')
            else:
                advice['action'] = '观望'
                advice['operations'].append('暂停加仓，观察走势')
            advice['reason'].append(f'技术面走弱，{sentiment["level"]}情绪压制')
        else:
            advice['action'] = '持有'
            advice['operations'].append('震荡市，网格交易或持有不动')
            advice['reason'].append('趋势不明，等待方向选择')
        
        if sentiment['keywords']:
            advice['reason'].append(f"关键词: {', '.join(sentiment['keywords'][:3])}")
        
        if trend['volatility'] > 2:
            advice['reason'].append('近期波动较大，注意风险控制')
        
        # 盈亏建议
        if holdings > 0 and cost > 0:
            current = self.fetcher.get_realtime_data(fund['code'])
            if current:
                profit_pct = (current['price'] - cost) / cost * 100
                if profit_pct > 10:
                    advice['operations'].append(f'目前盈利{profit_pct:.1f}%，可考虑部分止盈')
                elif profit_pct < -10:
                    advice['operations'].append(f'目前亏损{abs(profit_pct):.1f}%，谨慎补仓')
        
        return advice
    
    def _update_advice(self, fund, morning_pred, actual_change, actual_direction):
        """更新建议"""
        advice = {
            'action': '维持',
            'reason': [],
            'operations': []
        }
        
        pred = morning_pred.get('prediction', '震荡')
        
        if (pred == actual_direction) or (pred == '震荡' and abs(actual_change) < 0.5):
            advice['reason'].append('✅ 早盘预测准确，策略有效')
            if actual_direction == '上涨':
                advice['operations'].append('趋势确认，可继续持有')
            else:
                advice['operations'].append('风险释放，明日观察企稳信号')
        else:
            advice['reason'].append('⚠️ 走势与预测不符，需调整策略')
            if pred == '上涨' and actual_direction == '下跌':
                advice['action'] = '止损'
                advice['operations'].append('利好兑现变利空，考虑止损')
            elif pred == '下跌' and actual_direction == '上涨':
                advice['action'] = '追涨'
                advice['operations'].append('强势反转，明日可追涨')
        
        if abs(actual_change) > 3:
            advice['operations'].append(f'今日波动较大({actual_change:+.2f}%)，注意仓位管理')
        
        return advice
    
    def _analyze_deviation(self, morning_pred, actual_change, actual_direction):
        """分析偏差"""
        reasons = []
        sentiment = morning_pred.get('sentiment', {})
        trend = morning_pred.get('trend', {})
        
        if sentiment.get('score', 0) * actual_change < 0:
            reasons.append('盘中情绪发生反转')
        
        if abs(actual_change) > trend.get('volatility', 1) * 3:
            reasons.append('出现超预期波动')
        
        if trend.get('trend') == '上升' and actual_direction == '下跌':
            reasons.append('上升趋势被打破')
        elif trend.get('trend') == '下降' and actual_direction == '上涨':
            reasons.append('下跌趋势逆转')
        
        if not reasons:
            reasons.append('正常波动范围内')
        
        return reasons
    
    def _summarize_news(self, news_list):
        """总结新闻"""
        if not news_list:
            return "无重大新闻"
        
        summaries = []
        for n in news_list[:3]:
            title = n.get('title', '')[:40]
            summaries.append(f"• {n.get('source', '未知')}: {title}...")
        
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
            print("提示: 未配置Pushplus Token，跳过推送")
            return False
        
        data = {
            'token': self.token,
            'title': title[:100],
            'content': content,
            'template': template
        }
        
        try:
            resp = self.http.post(self.url, data, timeout=10)
            # 解析响应
            try:
                result = json.loads(resp) if isinstance(resp, str) else {'code': 200}
            except:
                result = {'code': 200} if '200' in str(resp) or 'success' in str(resp).lower() else {'code': 0}
            
            if result.get('code') == 200:
                print(f"推送成功: {title}")
                return True
            else:
                print(f"推送失败: {resp[:200]}")
                return False
        except Exception as e:
            print(f"推送请求失败: {e}")
            return False


# ==================== 主程序 ====================

class FundMonitor:
    """基金监控主程序"""
    
    def __init__(self):
        self.config = Config()
        self.fetcher = FundDataFetcher()
        self.analyzer = AIFundAnalyzer(self.config)
        self.notifier = PushNotifier(self.config.pushplus_token)
        # 本地预测数据保存路径（Windows兼容）
        self.prediction_file = os.path.join(os.getcwd(), 'fund_predictions.json')
    
    def run(self, mode):
        """运行指定模式"""
        print(f"\n{'='*50}")
        print(f"基金AI盯盘系统 - 模式: {mode}")
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*50}\n")
        
        if mode == 'morning':
            self.morning_analysis()
        elif mode == 'evening':
            self.evening_summary()
        elif mode == 'monitor':
            self.realtime_monitor()
        elif mode == 'daily':
            self.daily_report()
        elif mode == 'daemon':  # 新增：后台守护模式
            self.run_as_daemon()
        else:
            print(f"未知模式: {mode}")
    
    def morning_analysis(self):
        """早盘分析"""
        print("开始早盘AI分析...")
        
        funds = self.config.get_funds()
        if not funds:
            print("没有启用的基金")
            return
        
        predictions = []
        
        for fund in funds:
            print(f"分析 {fund['name']} ({fund['code']})...")
            pred = self.analyzer.predict_today(fund)
            if pred:
                predictions.append(pred)
                print(f"  预测: {pred['prediction']} (概率{pred['probability']}%)")
        
        if not predictions:
            self.notifier.send("⚠️ 早盘分析失败", "无法获取基金数据")
            return
        
        html = self._build_morning_html(predictions)
        title = f"🌅 AI早盘预测 | {datetime.now().strftime('%m-%d')} | {len(predictions)}只基金"
        
        self.notifier.send(title, html)
        self._save_predictions(predictions)
        print("早盘分析完成并已推送")
    
    def evening_summary(self):
        """收盘复盘"""
        print("开始收盘AI复盘...")
        
        morning_preds = self._load_predictions()
        if not morning_preds:
            print("未找到早盘预测数据，跳过复盘")
            return
        
        funds = self.config.get_funds()
        summaries = []
        
        for fund in funds:
            code = fund['code']
            morning_pred = morning_preds.get(code, {})
            
            if not morning_pred:
                continue
            
            print(f"复盘 {fund['name']} ({code})...")
            summary = self.analyzer.summarize_day(fund, morning_pred)
            if summary:
                summaries.append(summary)
                status = "✅准确" if summary['prediction_correct'] else "❌偏差"
                print(f"  预测{status}: 预计{morning_pred.get('prediction','?')} vs 实际{summary['actual_direction']}")
        
        if not summaries:
            self.notifier.send("⚠️ 收盘复盘失败", "无法获取数据")
            return
        
        html = self._build_evening_html(summaries)
        correct_count = sum(1 for s in summaries if s['prediction_correct'])
        accuracy = correct_count / len(summaries) * 100 if summaries else 0
        
        title = f"🌙 AI收盘复盘 | 准确率{accuracy:.0f}% | {len(summaries)}只基金"
        self.notifier.send(title, html)
        print("收盘复盘完成并已推送")
    
    def realtime_monitor(self):
        """实时监控"""
        if not self._is_trading_time():
            print("非交易时间，跳过监控")
            return
        
        print("开始实时监控...")
        
        funds = self.config.get_funds()
        alerts = []
        
        for fund in funds:
            data = self.fetcher.get_realtime_data(fund['code'])
            if not data:
                continue
            
            change = data['change_percent']
            threshold = fund.get('alert_threshold', 2.0)
            
            if abs(change) >= threshold:
                alerts.append({
                    'fund': fund,
                    'data': data,
                    'type': 'up' if change > 0 else 'down'
                })
                print(f"  🚨 {fund['name']}: {change:+.2f}% (触发阈值{threshold}%)")
        
        if alerts:
            html = self._build_alert_html(alerts)
            title = f"🚨 基金异动 | {len(alerts)}只触发阈值"
            self.notifier.send(title, html)
            print(f"已发送异动提醒: {len(alerts)}条")
        else:
            print("无异常波动")
    
    def daily_report(self):
        """收盘日报"""
        print("生成收盘日报...")
        
        funds = self.config.get_funds()
        holdings = []
        total_profit = 0
        
        for fund in funds:
            data = self.fetcher.get_realtime_data(fund['code'])
            if not data:
                continue
            
            profit = 0
            if fund.get('holdings', 0) > 0 and fund.get('cost_price', 0) > 0:
                profit = (data['price'] - fund['cost_price']) * fund['holdings']
                total_profit += profit
            
            holdings.append({
                'fund': fund,
                'data': data,
                'profit': profit
            })
            print(f"  {fund['name']}: {data['change_percent']:+.2f}% | 盈亏{profit:+.2f}元")
        
        html = self._build_daily_html(holdings, total_profit)
        title = f"📋 收盘日报 | 总盈亏{total_profit:+.2f}元"
        self.notifier.send(title, html)
        print("收盘日报已推送")
    
    def run_as_daemon(self):
        """后台守护模式（本地持续运行）"""
        print("启动后台守护模式，按 Ctrl+C 退出")
        
        # 读取配置的定时时间
        morning_time = self.config.get_setting('morning_analysis_time', '09:00')
        evening_time = self.config.get_setting('evening_summary_time', '16:00')
        monitor_interval = self.config.get_setting('monitor_interval', 10)
        
        # 设置定时任务
        schedule.every().day.at(morning_time).do(self.morning_analysis)
        schedule.every().day.at(evening_time).do(self.evening_summary)
        schedule.every(monitor_interval).minutes.do(self.realtime_monitor)
        
        # 立即执行一次监控
        self.realtime_monitor()
        
        # 循环执行定时任务
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # 每分钟检查一次
        except KeyboardInterrupt:
            print("\n程序已退出")
            sys.exit(0)
    
    # HTML构建方法
    def _build_morning_html(self, predictions):
        html = "<h2>🤖 AI早盘预测报告</h2>"
        html += f"<p style='color:#666'>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p><hr>"
        
        for pred in predictions:
            fund = pred['fund']
            advice = pred['advice']
            color = advice['action_color']
            pred_color = "red" if pred["prediction"]=="上涨" else "green" if pred["prediction"]=="下跌" else "gray"
            
            html += f"""
            <div style='margin:15px 0;padding:10px;border-left:4px solid {color};background:#f9f9f9'>
                <h3>{fund['name']} ({fund['code']})</h3>
                <p><b>预测:</b> <span style='color:{pred_color};font-size:16px'>{pred['prediction']} (概率{pred['probability']}%)</span> 
                <span style='color:#999'>置信度:{pred['confidence']}</span></p>
                <p><b>技术面:</b> {pred['trend']['trend']} (动量:{pred['trend']['momentum']:+.2f})</p>
                <p><b>情绪面:</b> {pred['sentiment']['level']} (分数:{pred['sentiment']['score']:+.2f})</p>
                <p><b>建议:</b> <span style='color:{color};font-weight:bold'>{advice['action']}</span></p>
                <ul>{"".join(f"<li>{r}</li>" for r in advice['reason'])}</ul>
                <p style='color:#0066cc'><b>操作:</b><br>{"".join(f"• {op}<br>" for op in advice['operations'])}</p>
                <p style='color:#666;font-size:12px'><b>相关新闻:</b><br>{pred['news_summary']}</p>
            </div>
            """
        
        html += "<hr><h3>📊 组合策略建议</h3>"
        html += self._generate_portfolio_advice(predictions)
        return html
    
    def _build_evening_html(self, summaries):
        html = "<h2>🌙 AI收盘复盘报告</h2>"
        html += f"<p style='color:#666'>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p><hr>"
        
        correct = sum(1 for s in summaries if s['prediction_correct'])
        total = len(summaries)
        accuracy = correct/total*100 if total else 0
        
        html += f"<p><b>预测准确率:</b> {correct}/{total} ({accuracy:.0f}%)</p>"
        
        for summary in summaries:
            fund = summary['fund']
            rt = summary['realtime']
            morning = summary['morning_prediction']
            updated = summary['updated_advice']
            
            pred_status = "✅ 准确" if summary['prediction_correct'] else "❌ 偏差"
            pred_color = "green" if summary['prediction_correct'] else "red"
            actual_color = "red" if rt["change_percent"]>0 else "green"
            
            html += f"""
            <div style='margin:15px 0;padding:10px;border-left:4px solid {pred_color};background:#f9f9f9'>
                <h3>{fund['name']} ({fund['code']})</h3>
                <p><b>早盘预测:</b> {morning.get('prediction', '未知')} | 
                <b>实际:</b> <span style='color:{actual_color}'>{rt['change_percent']:+.2f}%</span>
                <span style='color:{pred_color};margin-left:10px'>{pred_status}</span></p>
                <p><b>偏差分析:</b></p>
                <ul>{"".join(f"<li>{r}</li>" for r in summary['deviation_analysis'])}</ul>
                <p style='color:#0066cc'><b>更新建议:</b> {updated['action']}<br>
                {"".join(f"• {op}<br>" for op in updated['operations'])}</p>
            </div>
            """
        
        return html
    
    def _build_alert_html(self, alerts):
        html = "<h2>🚨 基金异动提醒</h2>"
        
        for alert in alerts:
            fund = alert['fund']
            data = alert['data']
            color = "red" if alert['type'] == 'up' else "green"
            
            html += f"""
            <div style='margin:10px 0;padding:10px;border-left:4px solid {color}'>
                <h3>{fund['name']} ({fund['code']})</h3>
                <p style='font-size:18px;color:{color}'><b>{data['change_percent']:+.2f}%</b></p>
                <p>净值: {data['price']:.4f} | 时间: {data['time']}</p>
            </div>
            """
        
        return html
    
    def _build_daily_html(self, holdings, total_profit):
        color = "red" if total_profit > 0 else "green"
        html = f"<h2>📋 收盘日报</h2>"
        html += f"<p style='font-size:16px'>总盈亏: <span style='color:{color};font-weight:bold'>{total_profit:+.2f}元</span></p>"
        html += "<table border='1' cellpadding='5' style='border-collapse:collapse;width:100%'>"
        html += "<tr style='background:#f0f0f0'><th>基金</th><th>净值</th><th>涨跌</th><th>盈亏</th></tr>"
        
        for h in holdings:
            fund = h['fund']
            data = h['data']
            profit = h['profit']
            c = "red" if data['change_percent'] > 0 else "green"
            
            html += f"<tr><td>{fund['name']}</td><td>{data['price']:.4f}</td>"
            html += f"<td style='color:{c}'>{data['change_percent']:+.2f}%</td>"
            html += f"<td>{profit:+.2f}</td></tr>"
        
        html += "</table>"
        return html
    
    def _generate_portfolio_advice(self, predictions):
        up = sum(1 for p in predictions if p['prediction'] == '上涨')
        down = sum(1 for p in predictions if p['prediction'] == '下跌')
        neutral = len(predictions) - up - down
        
        html = f"<p><b>市场情绪:</b> 看多{up}只 / 看空{down}只 / 震荡{neutral}只</p>"
        
        if up > down + neutral:
            html += "<p style='color:red'><b>策略:</b> 市场偏乐观，保持较高仓位</p>"
        elif down > up + neutral:
            html += "<p style='color:green'><b>策略:</b> 市场偏谨慎，降低仓位防御</p>"
        else:
            html += "<p><b>策略:</b> 市场分化，均衡配置</p>"
        
        return html
    
    def _is_trading_time(self):
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.time()
        # 放宽交易时间判断（包含早盘竞价和尾盘）
        return (time(9, 0) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 30))
    
    def _save_predictions(self, predictions):
        try:
            data = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'predictions': {p['fund']['code']: p for p in predictions}
            }
            with open(self.prediction_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, default=str, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存预测失败: {e}")
    
    def _load_predictions(self):
        try:
            with open(self.prediction_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    return data.get('predictions', {})
        except:
            pass
        return {}


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(description='基金AI盯盘系统 - 本地版')
    parser.add_argument('--mode', choices=['morning', 'evening', 'monitor', 'daily', 'init', 'daemon'],
                       default='monitor', help='运行模式: init(初始化配置), monitor(单次监控), morning(早盘分析), evening(收盘复盘), daily(日报), daemon(后台守护)')
    args = parser.parse_args()
    
    # 初始化配置
    if args.mode == 'init':
        config = Config()
        config.save()
        print("✅ 已创建默认配置文件 config.json")
        print("请编辑 config.json 添加你的基金信息和PushPlus Token")
        return
    
    # 运行其他模式
    monitor = FundMonitor()
    
    # 检查PushPlus Token（非必须，无token则仅控制台输出）
    if not monitor.config.pushplus_token:
        print("提示: 未配置PushPlus Token，将不会发送推送通知")
        print("获取Token: http://www.pushplus.plus → 登录后在「一对一推送」中获取")
    
    monitor.run(args.mode)


if __name__ == '__main__':
    main()
