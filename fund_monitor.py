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

# ==================== 改进点1：更完善的错误处理 ====================

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
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
                time.sleep(self.retry_delay * (attempt + 1))
        return ""

# ==================== 改进点3：增强的数据验证 ====================

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

# ==================== 改进点4：增强的新闻分析 ====================

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
            return {'score': 0, 'level': '中性', 'confidence': 0, 'keywords': []}
        
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
        
        # 归一化到[-1, 1]
        return max(-1, min(1, score / 5))
    
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

# ==================== 改进点5：增强的AI分析引擎 ====================

class EnhancedAIFundAnalyzer(AIFundAnalyzer):
    """增强版AI分析引擎"""
    
    def __init__(self, config):
        super().__init__(config)
        self.news_analyzer = EnhancedNewsAnalyzer(config)
        self.validator = DataValidator()
    
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
            
            # 2. 资金面分析
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
            return {'trend': 'unknown', 'strength': 0, 'indicators': {}}
        
        # 计算各项技术指标
        closes = [h['nav'] for h in history]
        
        # 趋势强度（使用线性回归）
        import numpy as np
        x = np.arange(len(closes))
        z = np.polyfit(x, closes, 1)
        trend_strength = z[0] * 100 / closes[0]  # 归一化趋势强度
        
        # 波动率
        returns = [(closes[i] - closes[i+1]) / closes[i+1] * 100 
                  for i in range(len(closes)-1)]
        volatility = np.std(returns) if returns else 0
        
        # 相对强弱
        if len(returns) >= 5:
            gains = [r for r in returns if r > 0]
            losses = [-r for r in returns if r < 0]
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
        confidence = '高' if sentiment['confidence'] > 0.7 else '中' if sentiment['confidence'] > 0.4 else '低'
        
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
            advice['operations'].append(f"建议加仓10-20%，止损位设置在-3%")
            advice['stop_loss'] = -3.0
        elif prediction['direction'] == '下跌' and prediction['probability'] > 70:
            advice['action'] = '减仓'
            advice['action_color'] = 'green'
            advice['operations'].append(f"建议减仓20-30%，等待企稳信号")
        else:
            advice['operations'].append("建议持有观望，等待明确信号")
        
        # 技术面理由
        if tech['trend'] == 'up':
            advice['reasons'].append(f"技术面: 上升趋势，RSI{tech['rsi']}")
        elif tech['trend'] == 'down':
            advice['reasons'].append(f"技术面: 下降趋势，RSI{tech['rsi']}")
        
        # 情绪面理由
        if sentiment['score'] > 0.3:
            advice['reasons'].append(f"情绪面: {sentiment['level']}，置信度{sentiment['confidence']}")
        
        # 风险提示
        if risk['level'] == '高':
            advice['operations'].append(f"⚠️ 高风险警示：波动率{tech['volatility']:.1f}%，建议严控仓位")
        
        # 止盈止损建议
        if fund.get('holdings', 0) > 0:
            advice['operations'].append(f"建议止盈位+5%，止损位-3%")
            advice['take_profit'] = 5.0
            advice['stop_loss'] = -3.0
        
        return advice

# ==================== 改进点6：配置验证和迁移 ====================

class ConfigManager(Config):
    """配置管理器，增加验证和迁移功能"""
    
    CONFIG_VERSION = '2.0'
    
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
        
        # 添加版本号
        self.data['version'] = self.CONFIG_VERSION
        
        # 确保所有必需字段存在
        if 'funds' in self.data:
            for fund in self.data['funds']:
                if 'alert_threshold' not in fund:
                    fund['alert_threshold'] = 2.0
                if 'enabled' not in fund:
                    fund['enabled'] = True
        
        # 保存迁移后的配置
        self.save()
        logger.info("配置迁移完成")

# ==================== 改进点7：主程序增强 ====================

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
                pred = self.enhanced_analyzer.predict_today_enhanced(fund)
                if pred:
                    predictions.append(pred)
                    success_count += 1
                    self._log_prediction(pred)
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"分析 {fund['code']} 失败: {e}")
                fail_count += 1
        
        if predictions:
            html = self._build_enhanced_morning_html(predictions)
            summary = self._build_analysis_summary(predictions, success_count, fail_count)
            
            # 发送推送
            title = f"🌅 AI早盘预测 | 成功{success_count}/{len(funds)} | {datetime.now().strftime('%m-%d %H:%M')}"
            self.notifier.send(title, html)
            
            # 保存结果
            self._save_predictions(predictions)
            
            logger.info(f"早盘分析完成: 成功{success_count}, 失败{fail_count}")
            print(summary)
        else:
            logger.error("所有基金分析失败")
            self.notifier.send("⚠️ 早盘分析失败", "所有基金均无法获取数据")
    
    def _log_prediction(self, pred: Dict):
        """记录预测结果"""
        logger.info(f"  {pred['fund']['name']}: {pred['prediction']} "
                   f"(概率{pred['probability']}%, 置信度{pred['confidence']})")
        logger.info(f"  建议: {pred['advice']['action']}")
    
    def _build_analysis_summary(self, predictions: List[Dict], success: int, total: int) -> str:
        """构建分析摘要"""
        summary = f"\n{'='*60}\n"
        summary += f"📊 早盘分析完成 (成功{success}/{total})\n"
        summary += f"{'='*60}\n"
        
        up = sum(1 for p in predictions if p['prediction'] == '上涨')
        down = sum(1 for p in predictions if p['prediction'] == '下跌')
        neutral = len(predictions) - up - down
        
        summary += f"📈 看涨: {up} | 📉 看跌: {down} | ⚖️ 震荡: {neutral}\n"
        summary += f"📊 平均置信度: {sum(p['confidence'] for p in predictions if p['confidence']!='低')/len(predictions):.1f}\n"
        summary += f"{'='*60}\n"
        
        return summary
    
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
            
            # 颜色标记
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
                        置信度:{sentiment['confidence']}
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
                
                <p style='color:#666;font-size:12px'><b>相关新闻:</b> {sentiment['news_count']}条</p>
            </div>
            """
        
        return html


# ==================== 入口增强 ====================

def main():
    parser = argparse.ArgumentParser(description='基金AI盯盘系统 - 增强版')
    parser.add_argument('--mode', choices=['morning', 'evening', 'init', 'query', 'auto', 'enhanced'],
                       default='auto', help='运行模式')
    parser.add_argument('--config', default='config.json', help='配置文件路径')
    parser.add_argument('--validate', action='store_true', help='验证配置')
    args = parser.parse_args()
    
    # 初始化日志
    setup_logging()
    
    try:
        if args.mode == 'init':
            # 初始化配置
            config = ConfigManager(args.config)
            config.save()
            print(f"✅ 已创建配置文件: {args.config}")
            print("请编辑配置文件添加PushPlus Token和基金信息")
            return
        
        if args.validate:
            # 验证配置
            config = ConfigManager(args.config)
            errors = config.validate()
            if errors:
                print("❌ 配置验证失败:")
                for error in errors:
                    print(f"  - {error}")
            else:
                print("✅ 配置验证通过")
            return
        
        # 自动模式判断
        if args.mode == 'auto':
            detected_mode = get_current_mode()
            args.mode = detected_mode
        
        # 运行增强版或标准版
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

if __name__ == '__main__':
    main()
