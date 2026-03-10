# 工作流名称
name: 基金AI盯盘系统

# 触发条件：定时触发 + 手动触发
on:
  schedule:
    - cron: "0 1 * * 1-5"    # 北京时间 09:00（UTC 1:00）执行早盘分析
    - cron: "0 8 * * 1-5"    # 北京时间 16:00（UTC 8:00）执行收盘复盘
    - cron: "*/10 * * * 1-5" # 每 10 分钟执行一次涨跌查询（替代原monitor）
  workflow_dispatch:

jobs:
  run-fund-monitor:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.10"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests beautifulsoup4 schedule

      - name: Inject PushPlus Token
        run: |
          jq --arg token "${{ secrets.PUSHPLUS_TOKEN }}" '.settings.pushplus_token = $token' config.json > temp.json
          mv temp.json config.json

      # 核心修复：仅调用有效模式，彻底删除monitor/daily
      - name: Run fund monitor
        run: |
          HOUR=$(date +%H)
          MINUTE=$(date +%M)
          if [ $HOUR -eq 1 ] && [ $MINUTE -eq 0 ]; then
            # UTC1=北京时间9点：早盘分析
            python fund_monitor.py --mode morning
          elif [ $HOUR -eq 8 ] && [ $MINUTE -eq 0 ]; then
            # UTC8=北京时间16点：收盘复盘
            python fund_monitor.py --mode evening
          else
            # 所有其他时间：涨跌查询（替代monitor/daily）
            python fund_monitor.py --mode query
          fi
        shell: /usr/bin/bash -e {0}
        env:
          pythonLocation: /opt/hostedtoolcache/Python/3.10.19/x64
          PKG_CONFIG_PATH: /opt/hostedtoolcache/Python/3.10.19/x64/lib/pkgconfig
          Python_ROOT_DIR: /opt/hostedtoolcache/Python/3.10.19/x64
          Python2_ROOT_DIR: /opt/hostedtoolcache/Python/3.10.19/x64
          Python3_ROOT_DIR: /opt/hostedtoolcache/Python/3.10.19/x64
          LD_LIBRARY_PATH: /opt/hostedtoolcache/Python/3.10.19/x64/lib
          TZ: Asia/Shanghai
