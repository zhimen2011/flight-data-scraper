# 航班数据抓取 — API 逆向分析报告

> **适用系统**: 航班智能分析-历史回放系统
> **内网地址**: `http://192.168.8.18:8082`
> **报告日期**: 2026-05-03
> **摘要**: 本报告记录了从该系统的 Web API 层抓取计划/实际航班轨迹数据的完整方法，包含 API 端点、参数格式、返回数据结构、数据流和复用指南。

---

## 1. 系统概览

```
┌──────────────────────────────────────────────────────────┐
│  前端 (Web 页面)                                          │
│  http://192.168.8.18:8082                                │
│  航班智能分析 — 历史回放功能                               │
└──────────────┬───────────────────────────────────────────┘
               │ 前端通过 AJAX 调用以下 REST API
               ▼
┌──────────────────────────────────────────────────────────┐
│  后端 REST API (无需认证)                                  │
│                                                          │
│  GET /getFlightListByAN     ← 搜索航班列表                │
│  GET /getFlightPlanPoints   ← 获取计划航路点               │
│  GET /getFlightHisPos       ← 获取实际飞行位置(ADS-B)      │
└──────────────────────────────────────────────────────────┘
```

- **认证**: 无需任何 Token / Cookie / Header 认证
- **协议**: HTTP GET，参数通过 URL query string 传递
- **响应格式**: JSON (`Content-Type: application/json`)
- **跨域**: 无 CORS 限制（内网系统）

---

## 2. API 端点详解

### 2.1 搜索航班 — `/getFlightListByAN`

**用途**: 根据航班号和日期范围搜索航班记录，获取后续 API 调用所需的 `flight_id` 和飞机/机场信息。

**请求**:
```
GET http://192.168.8.18:8082/getFlightListByAN?fi={flight_number}&staDate={start_date}&endDate={end_date}
```

| 参数 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `fi` | string | 是 | 航班号（大小写不敏感） | `I99806` |
| `staDate` | string | 是 | 查询起始日期 | `2026-04-01` |
| `endDate` | string | 是 | 查询结束日期 | `2026-04-30` |

**响应**: JSON 数组，每个元素是一个航班记录对象：

```json
[
  {
    "fLIGHTID":     "1234567",           // 航班唯一 ID — 用于后续两个 API 的核心标识
    "aIRCRAFT":     "B-1234",            // 机尾号/注册号
    "tKO_TIME":     "2026-04-29 22:27:00",   // 计划起飞时间
    "tKO_TIME_OFF": "2026-04-29 22:27:52",   // 实际起飞时间 (松刹车/离地)
    "dES_TIME":     "2026-04-30 08:30:00",   // 计划降落时间
    "tKO_FIELD":    "LGAV",              // 起飞机场 ICAO 四字码
    "dES_FIELD":    "ZHEC",              // 到达机场 ICAO 四字码
    "aRRI":         "武汉天河",           // 到达机场名称(中文)
    "...": "..."                         // 可能还有其他字段，以上为已验证使用的
  }
]
```

**关键字段用途**:
| 字段 | 后续用途 |
|------|---------|
| `fLIGHTID` | 传给 `/getFlightPlanPoints` 和 `/getFlightHisPos` |
| `aIRCRAFT` | 传给 `/getFlightPlanPoints` |
| `tKO_FIELD` / `dES_FIELD` | 传给 `/getFlightPlanPoints` |
| `tKO_TIME` / `dES_TIME` | 传给 `/getFlightHisPos` 作为时间范围 |
| `tKO_TIME_OFF` | 传给 `/getFlightPlanPoints` 作为 `date` 参数 |
| `tKO_TIME[:10]` | 提取日期用于文件命名 |

---
### 2.2 获取计划航路点 — `/getFlightPlanPoints`

**用途**: 获取航班的**计划**飞行轨迹——即飞行计划中预定的航路点序列（含经纬度、高度、距离、燃油、速度等计划剖面数据）。

**请求**:
```
GET http://192.168.8.18:8082/getFlightPlanPoints?fi={flight_id}&an={aircraft}&depAirport={dep}&arrAirport={arr}&date={date}
```

| 参数 | 类型 | 必填 | 说明 | 来源 |
|------|------|------|------|------|
| `fi` | string | 是 | 航班 ID | `fLIGHTID` (来自搜索接口) |
| `an` | string | 是 | 飞机注册号 | `aIRCRAFT` (来自搜索接口) |
| `depAirport` | string | 是 | 起飞机场 | `tKO_FIELD` (来自搜索接口) |
| `arrAirport` | string | 是 | 到达机场 | `dES_FIELD` (来自搜索接口) |
| `date` | string | 是 | 飞行日期(建议用 tKO_TIME_OFF) | `tKO_TIME_OFF` 或 `tKO_TIME` |

**响应**: JSON 数组，每个元素是一个航路点对象：

```json
[
  {
    "alt":       0,              // 计划高度 (英尺 ft)，地面为 0
    "dat":       "2026-04-29 00:00:00.0",  // 数据日期
    "dep":       "LGAV",         // 起飞机场
    "des":       "ZHEC",         // 到达机场
    "dist":      22.224,         // 沿程距离 (海里 nm)，从起飞机场起算
    "eta":       "08:36",        // 预计到达时间 (仅航班级别常量)
    "etd":       "22:40",        // 预计起飞时间 (仅航班级别常量)
    "flyTime":   596,            // 计划总飞行时间 (分钟)
    "ful":       67900,          // 计划燃油余量 (磅 lbs)
    "grs":       362,            // 地速 (节 kts)
    "important": false,          // 是否重要航路点
    "lat":       37.800833,      // 纬度
    "lon":       23.766667,      // 经度
    "name":      "AV210",        // 航路点名称 (SID/航路/STAR/FIX)
    "time":      0,              // 预计经过时间 (unix ms)，通常为 0，仅终点有值
    "tripfuel":  70718           // 总飞行计划燃油 (磅)
  }
]
```

**航路点类型判断**:
- `alt == 0`: 地面点（起飞机场 SID 或到达机场 STAR）
- `alt` 从 0 → 巡航高度: 爬升阶段
- `alt` 保持巡航: 巡航阶段
- `alt` 从巡航高度 → 0: 下降阶段
- `important == true`: 标记为重要航路点（通常为进近关键点）
- `name` 为机场 ICAO 码（如 `ZHEC`）: 目的地机场

**数据特征**（已验证）:
- 航路点数量: ~110-137 个（取决于具体日期和航路）
- 巡航高度: 约 8839-11887 ft 区间（多级巡航）
- 总航程: 约 7800 nm
- `dist` 递增: 0 → ~7800 nm

---
### 2.3 获取实际飞行位置 — `/getFlightHisPos`

**用途**: 获取航班的**实际**飞行轨迹——通过 ADS-B / ACARS 采集的密集位置报告序列（含时间戳、经纬度、高度、燃油、速度等）。

**请求**:
```
GET http://192.168.8.18:8082/getFlightHisPos?fi={flight_id}&beginTime={begin}&endTime={end}
```

| 参数 | 类型 | 必填 | 说明 | 来源 |
|------|------|------|------|------|
| `fi` | string | 是 | 航班 ID | `fLIGHTID` (来自搜索接口) |
| `beginTime` | string | 是 | 查询起始时间 | `tKO_TIME` (来自搜索接口) |
| `endTime` | string | 是 | 查询结束时间 | `dES_TIME` (来自搜索接口) |

**响应**: 直接返回 JSON 数组，或包裹在 `{"data": [...]}` 中（两种格式均需处理）:

```json
[
  {
    "alt":           152,                                // 实际高度 (英尺 ft)
    "dis":           0,                                  // 沿程距离 (海里 nm)，从起飞机场起算
    "fob":           69800,                              // 实际燃油余量 (磅 lbs)
    "gateway_time":  "2026-04-29 22:27:52",              // 数据采集时间 (北京时间)
    "lat":           37.939,                             // 实际纬度
    "lon":           23.936,                             // 实际经度
    "posPointName":  "POS",                              // 位置点名称 (通常为 "POS")
    "sAlt":          "",                                 // 标准高度 (通常为空或 "-1.0")
    "type":          "ACARS"                             // 数据来源类型
  }
]
```

**`type` 字段枚举**:
| 值 | 含义 | 采样频率 |
|----|------|---------|
| `ACARS` | 飞机通信寻址与报告系统 | 稀疏，关键节点 |
| `ADSB` | 广播式自动相关监视 | 密集，约 10-20 秒/点 |

**数据特征**（已验证）:
- 总数据点: ~3,000-3,500 个（约 10 小时航班 × 每 10-20 秒）
- GPS 坐标精度: 小数点后 6-8 位（米级精度）
- `dis` 列在起飞阶段可能为 0（地面阶段），之后递增
- 数据按 `gateway_time` 严格递增

---

## 3. 数据流（完整调用链）

```
用户输入: 航班号=I99806, 日期=2026-04-29
                │
                ▼
┌───────────────────────────────────────────────────┐
│ Step 1: GET /getFlightListByAN                    │
│         fi=I99806, staDate=2026-04-29,            │
│         endDate=2026-04-29                        │
│                                                   │
│ 返回: [{fLIGHTID, aIRCRAFT, tKO_TIME,             │
│         tKO_TIME_OFF, dES_TIME,                    │
│         tKO_FIELD, dES_FIELD, ...}]               │
└───────────────────────┬───────────────────────────┘
                        │
          ┌─────────────┴─────────────┐
          │ 并行调用 (无依赖关系)       │
          ▼                           ▼
┌──────────────────────┐  ┌──────────────────────────┐
│ Step 2:              │  │ Step 3:                  │
│ GET /getFlightPlan   │  │ GET /getFlightHisPos     │
│ Points               │  │                          │
│ fi=fLIGHTID          │  │ fi=fLIGHTID              │
│ an=aIRCRAFT          │  │ beginTime=tKO_TIME       │
│ depAirport=tKO_FIELD │  │ endTime=dES_TIME         │
│ arrAirport=dES_FIELD │  │                          │
│ date=tKO_TIME_OFF    │  │                          │
│                      │  │                          │
│ 返回: 计划航路点数组  │  │ 返回: 实际位置点数组     │
│ (~110-137个)         │  │ (~3000-3500个)           │
└──────────┬───────────┘  └────────────┬─────────────┘
           │                           │
           ▼                           ▼
┌──────────────────────┐  ┌──────────────────────────┐
│ Save as:             │  │ Save as:                 │
│ *_plan_track.csv     │  │ *_actual_track.csv       │
│ *_plan_profile.csv   │  │ *_actual_profile.csv     │
└──────────────────────┘  └──────────────────────────┘
```

**注意**: Step 2 和 Step 3 的参数来自 Step 1 的返回值，但它们之间没有依赖 — 可以并行调用。

---

## 4. CSV 输出文件命名规范

```
{flight_id}_{date}[_{序号}]_{类型}.csv
```

| 组成部分 | 说明 | 示例 |
|---------|------|------|
| `flight_id` | 航班 ID (来自 API 的 fLIGHTID) | `I99806` |
| `date` | 飞行日期，取自 `tKO_TIME[:10]` | `2026-04-29` |
| `_{序号}` | 仅同日多班次时追加（自动去重） | `_2` (如 `I99806_2026-04-13_2_`) |
| `_{类型}` | 四种文件类型 | 见下表 |

**四种输出文件**:

| 后缀 | 来源 API | 字段 | 用途 |
|------|----------|------|------|
| `_plan_track.csv` | `/getFlightPlanPoints` | 全字段 (alt,dist,lat,lon,name,ful,grs,...) | 计划航线全量数据 |
| `_plan_profile.csv` | `/getFlightPlanPoints` | 子集 (name,dist,alt,ful,time,lat,lon) | 计划纵向剖面 |
| `_actual_track.csv` | `/getFlightHisPos` | 全字段 (alt,dis,fob,gateway_time,lat,lon,posPointName,sAlt,type) | 实际轨迹全量数据 |
| `_actual_profile.csv` | `/getFlightHisPos` | 子集 (gateway_time,alt,fob,dis,lat,lon,posPointName,sAlt,type) | 实际纵向剖面 |

**实际输出示例**:
```
flight_csv/
├── I99806_2026-04-02_plan_track.csv
├── I99806_2026-04-02_plan_profile.csv
├── I99806_2026-04-02_actual_track.csv
├── I99806_2026-04-02_actual_profile.csv
├── I99806_2026-04-13_plan_track.csv       ← 当天第一班
├── I99806_2026-04-13_2_plan_track.csv     ← 当天第二班（同航班号）
└── ...
```

---

## 5. 代码实现模式（可复用到其他项目）

### 5.1 核心模式: 无认证内网 REST API 抓取

```python
import requests

BASE_URL = "http://192.168.8.18:8082"

def call_api(endpoint, params):
    """通用 API 调用模式"""
    url = f"{BASE_URL}/{endpoint}"
    resp = requests.get(url, params=params)
    resp.raise_for_status()  # HTTP 错误直接抛异常
    data = resp.json()
    # 处理两种响应格式: 直接数组 或 {"data": [...]}包裹
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data
```

### 5.2 CSV 保存模式（BOM 头处理中文）

```python
import csv

def save_csv(data, filename, fieldnames=None):
    """
    data: list[dict] — API 返回的 JSON 数组
    filename: 输出文件路径
    fieldnames: 要保存的字段列表 (为 None 则保存全部字段)
    """
    if not data:
        return False
    if fieldnames is None:
        fieldnames = list(data[0].keys())
    # encoding="utf-8-sig" 写入 BOM 头，确保 Excel 正确识别中文
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    return True
```

### 5.3 多日批量抓取模式

```python
# 核心思路: 搜索时扩大日期范围，然后逐条处理
flights = search_flights("I99806", "2026-04-01", "2026-04-30")
for flight in flights:
    process_flight(flight)  # 每个 flight 产生 4 个 CSV
```

### 5.4 同日多班次去重策略

```python
prefix_counter = {}
for flight in flights:
    flight_date = tko_time[:10]
    base_prefix = f"{flight_id}_{flight_date}"
    prefix_counter[base_prefix] = prefix_counter.get(base_prefix, 0) + 1
    if prefix_counter[base_prefix] > 1:
        prefix = f"{base_prefix}_{prefix_counter[base_prefix]}"  # 追加 _2, _3...
    else:
        prefix = base_prefix
```

---

## 6. 关键注意事项

### 6.1 日期参数的选择

- **搜索接口** `staDate` / `endDate` → 用飞行日期（任意时间均可）
- **计划接口** `date` → 建议用 `tKO_TIME_OFF`（实际起飞时间），因为计划航路有可能在起飞前有微小调整；如果无实际起飞时间则回退到 `tKO_TIME`
- **实际接口** `beginTime` / `endTime` → 用 `tKO_TIME` / `dES_TIME`（计划时间），因为实际轨迹数据的覆盖范围通常比飞行时间更宽

### 6.2 响应格式差异

- `/getFlightListByAN` 和 `/getFlightPlanPoints` → 直接返回数组 `[{...}, ...]`
- `/getFlightHisPos` → 可能返回 `{"data": [{...}, ...]}` 或直接数组，需要兼容处理

### 6.3 字段命名风格

该系统后端疑似 Java/MyBatis 风格，字段名混合全大写和下划线：
- 代码中引用用驼峰+下划线混合（如 `fLIGHTID`）
- CSV 输出按原始字段名保存

### 6.4 数据量考量

- 单个航班实际轨迹 CSV 约 300-350 KB（~3400 行）
- 单日完整数据（4 个文件）约 650-700 KB
- 批量抓取一个月约 30-40 MB（合理范围内）

---

## 7. 复用到其他类似系统的检查清单

当在新项目中遇到类似的航班/轨迹数据系统时，按以下清单排查：

1. [ ] 打开浏览器 DevTools → Network 标签
2. [ ] 在 Web 页面中执行一次查询操作
3. [ ] 观察 Network 中的 XHR/Fetch 请求，定位以下类型的 API：
   - 搜索/列表接口（获取 ID 列表）
   - 详情/计划接口（获取规划数据）
   - 实测/回放接口（获取实际数据）
4. [ ] 记录每个 API 的: URL、请求方法(GET/POST)、Query 参数及其含义
5. [ ] 检查是否需要认证（Cookie / Authorization Header / Token）
6. [ ] 检查响应是否被额外包裹（如 `{code: 0, data: [...]}`）
7. [ ] 确定关联字段（哪个字段是后续请求的输入参数）
8. [ ] 用 Python `requests` 逐 API 重现，验证数据完整性
9. [ ] 添加 CSV 导出逻辑，用 `utf-8-sig` 编码（Excel 兼容）

---

*报告完毕。基于 flight_data_scraper.py v1.0 逆向分析结果。*
