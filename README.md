# 航班轨迹偏差分析工具

从内部系统抓取航班计划/实际轨迹数据，分析飞行偏差（高度、水平、燃油），生成包含交互式地图和图表的 HTML 报告以及 Word 统计报告。

## 功能概览

- **数据抓取** — 从航班历史回放系统抓取计划轨迹、实际轨迹和剖面数据，输出为 CSV 文件
- **偏差分析** — 计算每个实际轨迹点相对于计划航路的侧向偏差、高度偏差、燃油偏差
- **三层过滤** — 仅巡航段 → 幅度阈值 → 持续距离阈值，识别显著偏差事件
- **提前下降检测** — 对比计划 TOD 与实际下降起始点，检测国内航段是否提前下降
- **区域分组** — 基于 GeoJSON 国界数据自动识别航点所在国家/区域（国内段、蒙古、俄罗斯、哈萨克斯坦、欧洲等）
- **Web 可视化界面** — 内置 HTTP 服务器，提供浏览器端的数据管理、分析配置和报告浏览
- **报告生成** — 输出交互式 HTML 报告（Leaflet 地图 + ECharts 图表）和 Word 文档（统计表 + 剖面图附件）

## 项目结构

```
flight-data-scraper/
├── flight_data_scraper.py   # 数据抓取：调用 API 获取计划/实际轨迹，保存 CSV
├── analyze_flight.py        # 核心分析引擎：偏差计算、过滤、HTML 报告
├── flight_server.py         # Web GUI 服务器（3 个标签页：抓取/分析/报告）
├── generate_docx_report.py  # Word 报告生成（主报告 + 剖面图附件）
├── local_report.py          # 确定性报告：统计表、附件、HTML 表格报告
├── flight_keys.py           # 航班文件命名/解析辅助工具
├── countries.geojson        # 国界数据（用于航点国家识别）
├── analysis_config.json     # 分析配置（偏差阈值等）
├── FlightDeviationTool.spec # PyInstaller 打包配置
├── flight_csv/              # 当前工作数据（CSV 文件）
├── flight_csv_archive/      # 已归档的历史数据
├── reports/                 # 生成的报告文件（HTML / Word）
├── tests/                   # 测试用例
├── requirements.txt         # Python 依赖
└── report_integrated.html   # 集成报告模板
```

## 环境要求

- Python 3.10+
- 依赖：`pip install -r requirements.txt`
- 需要能访问内部航班数据 API（默认 `http://192.168.8.18:8082`）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动 Web GUI（推荐）

```bash
python flight_server.py
```

浏览器自动打开 `http://localhost:8765`，通过三个标签页完成完整工作流：

- **数据抓取** — 输入航班号/飞机号，搜索并抓取数据
- **偏差分析** — 调整阈值，勾选航班，一键生成报告
- **报告查看** — 浏览和打开已生成的报告文件

### 3. 命令行抓取

```bash
python flight_data_scraper.py --flight I99806 --date 2026-04-29
```

输出文件（保存在 `flight_csv/` 下）：
- `I99806_2026-04-29_plan_track.csv` — 计划航路点
- `I99806_2026-04-29_plan_profile.csv` — 计划剖面
- `I99806_2026-04-29_actual_track.csv` — 实际轨迹
- `I99806_2026-04-29_actual_profile.csv` — 实际剖面

### 4. 命令行分析

```bash
# 单日分析
python analyze_flight.py --flight I99806 --date 2026-04-29

# 自定义阈值
python analyze_flight.py --flight I99806 --date 2026-04-29 --min-alt-dev 800 --min-dur 40

# 多日对比
python analyze_flight.py --flight I99806 --multi

# 指定多个日期
python analyze_flight.py --flight I99806 --dates 2026-04-02,2026-04-06,2026-04-09
```

生成的 HTML 报告包含：
- 交互式地图（计划航路 + 实际轨迹，颜色标注偏差程度）
- 高度剖面图 & 水平偏差图（ECharts）
- 显著偏差事件列表
- 区域统计表

### 5. 生成 Word 报告

```bash
python generate_docx_report.py --flight I99806 --dates 2026-04-02,2026-04-06,2026-04-09
```

## 分析原理

### 偏差计算

1. 将每个实际轨迹点投影到计划航路上，计算沿程距离和侧向偏差
2. 在对应距离处线性插值计划高度，计算高度偏差（实际 - 计划）
3. 通过 GeoJSON 国界数据识别每个点所在国家/区域

### 三层过滤

| 层级 | 过滤条件 | 说明 |
|------|---------|------|
| 第 1 层 | 仅巡航段 | 通过计划航路点的高度变化率识别巡航段，排除爬升/下降 |
| 第 2 层 | 幅度阈值 | `|高度偏差| ≥ 阈值`（默认 1000 ft） |
| 第 3 层 | 持续距离 | 连续超限点跨越距离 ≥ 阈值（默认 50 nm） |

### 提前下降检测

对比计划 TOD（下降顶点）与实际下降起始点：
- 通过计划航路点的显式 TOD 标签或剖面形状识别计划 TOD
- 通过滑动窗口检测实际高度的持续下降
- 仅对计划目的地为中国的航班执行此检测
- 提前下降阈值默认 30 nm，可在配置中调整

## 配置

`analysis_config.json` 支持以下参数：

```json
{
  "min_alt_deviation_ft": 1000,
  "min_duration_nm": 50,
  "premature_descent_threshold_nm": 30,
  "descent_search_before_nm": 600
}
```

也可通过 Web GUI 界面调整阈值并保存。

## 打包为可执行文件

```bash
pip install pyinstaller
pyinstaller FlightDeviationTool.spec
```

打包后生成 `dist/FlightDeviationTool.exe`（Windows 下含 GUI 的传统 EXE）。

## 运行测试

```bash
pytest tests/
```

## 技术栈

- **后端**: Python 标准库 `http.server`、`requests`、`csv`、`json`
- **前端**: 原生 JavaScript、Leaflet（地图）、ECharts（图表）
- **报告**: `python-docx`（Word）、`matplotlib`（剖面图）
- **地理**: `countries.geojson` + 自实现射线法点面判定
- **数学**: 大圆距离、球面投影
