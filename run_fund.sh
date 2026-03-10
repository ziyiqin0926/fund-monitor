#!/bin/bash
# 基金监控脚本 - 自动执行对应模式
# UTC1(北京时间9:00) → morning模式 | UTC8(北京时间16:00) → evening模式 | 其他时间 → query模式

# 获取当前UTC时间的小时和分钟
HOUR=$(date +%H)
MINUTE=$(date +%M)

# 执行对应模式
if [ $HOUR -eq 1 ] && [ $MINUTE -eq 0 ]; then
  # 早盘分析模式
  python fund_monitor.py --mode morning
elif [ $HOUR -eq 8 ] && [ $MINUTE -eq 0 ]; then
  # 收盘复盘模式
  python fund_monitor.py --mode evening
else
  # 非指定时间 → 涨跌查询模式（核心：替换原monitor为query）
  python fund_monitor.py --mode query
fi

# 环境变量配置（若需在脚本内指定，可取消注释以下内容）
# export pythonLocation=/opt/hostedtoolcache/Python/3.10.19/x64
# export PKG_CONFIG_PATH=/opt/hostedtoolcache/Python/3.10.19/x64/lib/pkgconfig
# export Python_ROOT_DIR=/opt/hostedtoolcache/Python/3.10.19/x64
# export Python2_ROOT_DIR=/opt/hostedtoolcache/Python/3.10.19/x64
# export Python3_ROOT_DIR=/opt/hostedtoolcache/Python/3.10.19/x64
# export LD_LIBRARY_PATH=/opt/hostedtoolcache/Python/3.10.19/x64/lib
# export TZ=Asia/Shanghai
