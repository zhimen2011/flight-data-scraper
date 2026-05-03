"""
Flight Analysis Web GUI Server
===============================
Start with: python flight_server.py
Opens browser to http://localhost:8765
Zero extra dependencies — uses Python built-in http.server
"""
import os, sys, json, csv, glob, threading, webbrowser, io, urllib.parse
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_flight import (
    CountryIndex, GEOJSON_PATH, CSV_DIR, analyze_flight, find_csv_files, load_config,
)
from flight_data_scraper import (
    get_flight_plan_points, get_flight_his_pos, save_csv, search_flights as scraper_search,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
SCRAPE_URL = "http://192.168.8.18:8082"
os.makedirs(REPORTS_DIR, exist_ok=True)

# ─── HTML GUI ─────────────────────────────────────────────────────────────────

GUI_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>航班轨迹偏差分析工具</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:"Microsoft YaHei","SimHei",sans-serif;background:#1a1a2e;color:#e0e0e0;height:100vh;display:flex;flex-direction:column;}
.header{background:#16213e;padding:10px 20px;border-bottom:2px solid #0f3460;}
.header h1{font-size:18px;color:#e94560;}
.tabs{display:flex;background:#0f3460;border-bottom:1px solid #1a1a2e;}
.tab{padding:10px 24px;cursor:pointer;font-size:13px;border-bottom:2px solid transparent;transition:.2s;}
.tab:hover{background:#1a1a3e;}
.tab.active{border-bottom-color:#e94560;color:#e94560;}
.tab-content{flex:1;overflow-y:auto;padding:12px 16px;display:none;}
.tab-content.active{display:block;}
.search-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;}
.search-bar input{background:#16213e;border:1px solid #333;color:#eee;padding:6px 10px;border-radius:4px;font-size:12px;}
.search-bar button{background:#e94560;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;}
.search-bar button:hover{background:#ff6b7a;}
table{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0;}
th{background:#0f3460;color:#e94560;padding:6px 8px;text-align:left;position:sticky;top:0;}
td{padding:5px 8px;border-bottom:1px solid #1a1a2e;}
tr:hover td{background:#1a1a3e;}
input[type=checkbox]{accent-color:#e94560;transform:scale(1.2);}
.btn-row{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap;align-items:center;}
.btn-row button{padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;border:none;}
.btn-primary{background:#e94560;color:#fff;}
.btn-secondary{background:#16213e;color:#ccc;border:1px solid #333;}
.btn-primary:hover{background:#ff6b7a;}
.btn-secondary:hover{background:#1a1a4e;}
.progress{width:100%;height:6px;background:#0f3460;border-radius:3px;margin:8px 0;overflow:hidden;}
.progress-bar{height:100%;background:#e94560;width:0%;transition:width .3s;}
.status{font-size:12px;color:#aaa;margin:4px 0;}
.config-row{display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin:8px 0;}
.config-row label{font-size:12px;color:#aaa;}
.config-row input[type=range]{width:140px;accent-color:#e94560;}
.config-row .val{color:#e94560;font-weight:bold;}
</style>
</head>
<body>
<div class="header"><h1>✈ 航班轨迹偏差分析工具</h1></div>
<div class="tabs">
  <div class="tab active" onclick="switchTab(0)">1. 数据抓取</div>
  <div class="tab" onclick="switchTab(1)">2. 偏差分析</div>
  <div class="tab" onclick="switchTab(2)">3. 报告查看</div>
</div>

<!-- TAB 0: Scrape -->
<div class="tab-content active" id="tab0">
  <div class="search-bar">
    <span>航班号:</span><input id="sFlight" placeholder="I98831,I98833" style="width:130px;">
    <span>飞机号:</span><input id="sAircraft" placeholder="B-2079" style="width:80px;">
    <span>日期:</span><input id="sDateFrom" value="2026-04-01" style="width:95px;">
    <span>~</span><input id="sDateTo" value="2026-04-30" style="width:95px;">
    <button onclick="searchFlights()">搜索并添加到结果池</button>
  </div>
  <div class="btn-row">
    <button class="btn-secondary" onclick="selPool('outbound')">全选去程</button>
    <button class="btn-secondary" onclick="selPool('return')">全选回程</button>
    <button class="btn-secondary" onclick="selPool('all')">全选</button>
    <button class="btn-secondary" onclick="selPool('none')">清空</button>
    <button class="btn-secondary" onclick="removeUnchecked()">移除未勾选</button>
  </div>
  <div class="status" id="poolStatus">结果池（共 0 条）</div>
  <div id="poolTable"></div>
  <div class="btn-row">
    <button class="btn-primary" onclick="startScrape()">开始抓取勾选的航班</button>
    <div class="progress" style="flex:1;min-width:200px;"><div class="progress-bar" id="scrapeProgress"></div></div>
    <span class="status" id="scrapeStatus">就绪</span>
  </div>
</div>

<!-- TAB 1: Analyze -->
<div class="tab-content" id="tab1">
  <div class="config-row">
    <label>高度偏差阈值: <input type="range" id="aAlt" min="200" max="3000" value="1000" step="100" oninput="updateCfg()">
      <span class="val" id="aAltVal">1000</span> ft</label>
    <label>持续时间阈值: <input type="range" id="aDur" min="20" max="500" value="200" step="10" oninput="updateCfg()">
      <span class="val" id="aDurVal">200</span> nm</label>
    <label><input type="checkbox" id="aCruise" checked> 仅巡航段</label>
    <button class="btn-primary" onclick="saveConfig()">保存为默认</button>
  </div>
  <div class="btn-row">
    <button class="btn-secondary" onclick="selAnalyze(true)">全选</button>
    <button class="btn-secondary" onclick="selAnalyze(false)">清空</button>
    <button class="btn-secondary" onclick="refreshAnalyze()">刷新列表</button>
  </div>
  <div class="status" id="analyzeStatus">已有数据</div>
  <div id="analyzeTable"></div>
  <div class="btn-row">
    <button class="btn-primary" onclick="runAnalysis()">▶ 运行偏差分析</button>
    <button class="btn-primary" onclick="genWordReport()">生成 Word 报告</button>
    <button class="btn-primary" onclick="genHtmlReport()">生成 HTML 集成报告</button>
    <button class="btn-secondary" onclick="runAll()">全部（分析+Word+HTML）</button>
    <div class="progress" style="flex:1;min-width:150px;"><div class="progress-bar" id="analyzeProgress"></div></div>
    <span class="status" id="analyzeMsg">就绪</span>
  </div>
</div>

<!-- TAB 2: Reports -->
<div class="tab-content" id="tab2">
  <div class="btn-row">
    <button class="btn-secondary" onclick="refreshReports()">刷新</button>
    <button class="btn-secondary" onclick="openReportsFolder()">打开文件夹</button>
  </div>
  <div id="reportsTable"></div>
</div>

<script>
function switchTab(n){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',i===n));
  document.querySelectorAll('.tab-content').forEach((t,i)=>t.classList.toggle('active',i===n));
  if(n===0) refreshPool();
  if(n===1) refreshAnalyze();
  if(n===2) refreshReports();
}
async function api(path,opt={}){
  let url='/api'+path;
  if(opt.method==='POST'){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(opt.body||{})});return r.json();}
  else{let r=await fetch(url);return r.json();}
}
// TAB 0
let pool=[],poolChecks={};
async function searchFlights(){
  let qs=`?flight=${encodeURIComponent($('sFlight').value)}&aircraft=${encodeURIComponent($('sAircraft').value)}&from=${$('sDateFrom').value}&to=${$('sDateTo').value}`;
  let r=await api('/search'+qs);
  if(r.error){alert(r.error);return;}
  let added=0;
  r.results.forEach(fl=>{let k=fl.key;if(!pool.some(p=>p.key===k)){pool.push(fl);poolChecks[k]=true;added++;}});
  renderPool();setStatus('poolStatus','结果池（共 '+pool.length+' 条）');alert('新增 '+added+' 条');
}
function renderPool(){
  let h='<table><tr><th>✓</th><th>方向</th><th>日期</th><th>航班号</th><th>机号</th><th>起降</th></tr>';
  pool.forEach(p=>{
    let ck=poolChecks[p.key]!==false?'☑':'☐';
    h+=`<tr><td onclick="togglePool('${p.key}')" style="cursor:pointer">${ck}</td><td>${p.dir||''}</td><td>${p.date}</td><td>${p.flight}</td><td>${p.ac}</td><td>${p.route}</td></tr>`;
  });
  h+='</table>';document.getElementById('poolTable').innerHTML=h;
}
function togglePool(k){poolChecks[k]=!poolChecks[k];renderPool();}
function selPool(mode){
  pool.forEach(p=>{
    if(mode==='all') poolChecks[p.key]=true;
    else if(mode==='none') poolChecks[p.key]=false;
    else if(mode==='outbound') poolChecks[p.key]=p.dir==='去程';
    else if(mode==='return') poolChecks[p.key]=p.dir==='回程';
  });
  renderPool();
}
function removeUnchecked(){pool=pool.filter(p=>poolChecks[p.key]!==false);renderPool();setStatus('poolStatus','结果池（共 '+pool.length+' 条）');}
async function refreshPool(){renderPool();}
async function startScrape(){
  let keys=pool.filter(p=>poolChecks[p.key]!==false).map(p=>p.key);
  if(!keys.length){alert('请勾选航班');return;}
  setStatus('scrapeStatus','抓取中...');setProgress('scrapeProgress',0);
  let r=await api('/scrape',{method:'POST',body:{keys}});
  setProgress('scrapeProgress',100);setStatus('scrapeStatus',r.status||'完成');
  refreshAnalyze();
}

// TAB 1
let analyzeList=[],analyzeChecks={};
async function refreshAnalyze(){
  let r=await api('/list_csv');
  analyzeList=r.files||[];
  analyzeList.forEach(f=>{if(!(f.key in analyzeChecks)) analyzeChecks[f.key]=true;});
  renderAnalyze();setStatus('analyzeStatus','共 '+analyzeList.length+' 个航班数据');
}
function renderAnalyze(){
  let h='<table><tr><th>✓</th><th>日期</th><th>航班号</th><th>状态</th></tr>';
  analyzeList.forEach(f=>{
    let ck=analyzeChecks[f.key]!==false?'☑':'☐';
    h+=`<tr><td onclick="toggleAnalyze('${f.key}')" style="cursor:pointer">${ck}</td><td>${f.date}</td><td>${f.flight}</td><td>${f.status||'CSV ✓'}</td></tr>`;
  });
  h+='</table>';document.getElementById('analyzeTable').innerHTML=h;
}
function toggleAnalyze(k){analyzeChecks[k]=!analyzeChecks[k];renderAnalyze();}
function selAnalyze(state){analyzeList.forEach(f=>analyzeChecks[f.key]=state);renderAnalyze();}
function updateCfg(){$('aAltVal').textContent=$('aAlt').value;$('aDurVal').textContent=$('aDur').value;}
async function saveConfig(){
  let cfg={min_alt_deviation_ft:parseInt($('aAlt').value),min_duration_nm:parseInt($('aDur').value)};
  await api('/save_config',{method:'POST',body:cfg});
  alert('配置已保存');
}
async function runAnalysis(){
  let keys=analyzeList.filter(f=>analyzeChecks[f.key]!==false).map(f=>f.key);
  if(!keys.length){alert('请勾选航班');return;}
  let cfg={min_alt_deviation_ft:parseInt($('aAlt').value),min_duration_nm:parseInt($('aDur').value)};
  setStatus('analyzeMsg','分析中...');setProgress('analyzeProgress',0);
  let r=await api('/analyze',{method:'POST',body:{keys,config:cfg}});
  setProgress('analyzeProgress',100);
  setStatus('analyzeMsg',r.status||('完成 '+r.count+' 个航班'));
}
async function genWordReport(){
  setStatus('analyzeMsg','生成 Word 报告...');setProgress('analyzeProgress',0);
  let r=await api('/gen_word',{method:'POST',body:{}});
  setProgress('analyzeProgress',100);setStatus('analyzeMsg',r.status||'Word 报告已生成');
  refreshReports();
}
async function genHtmlReport(){
  setStatus('analyzeMsg','生成 HTML 报告...');setProgress('analyzeProgress',0);
  let r=await api('/gen_html',{method:'POST',body:{}});
  setProgress('analyzeProgress',100);setStatus('analyzeMsg',r.status||'HTML 报告已生成');
  refreshReports();
}
async function runAll(){await runAnalysis();setTimeout(genWordReport,2000);setTimeout(genHtmlReport,4000);}

// TAB 2
async function refreshReports(){
  let r=await api('/list_reports');
  let files=r.files||[];
  let h='<table><tr><th>文件名</th><th>大小</th><th>时间</th><th>操作</th></tr>';
  files.forEach(f=>{h+=`<tr><td>${f.name}</td><td>${f.size}</td><td>${f.time}</td><td><a href="/reports/${encodeURIComponent(f.name)}" target="_blank" style="color:#e94560">打开</a></td></tr>`;});
  h+='</table>';document.getElementById('reportsTable').innerHTML=h;
}
function openReportsFolder(){window.open('/reports/');}

// Utils
function setStatus(id,txt){document.getElementById(id).textContent=txt;}
function setProgress(id,pct){document.getElementById(id).style.width=pct+'%';}
function $(id){return document.getElementById(id);}

// Init
refreshPool();refreshAnalyze();refreshReports();
</script>
</body>
</html>
"""

# ─── HTTP Server ──────────────────────────────────────────────────────────────

class FlightAPIHandler(BaseHTTPRequestHandler):
    """Handles API requests and serves the GUI."""

    search_pool = []  # class-level shared state
    analysis_results = {}
    country_index = None

    def log_message(self, format, *args):
        pass  # suppress logs

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._serve_html(GUI_HTML)
        elif path == "/api/search":
            self._api_search()
        elif path == "/api/list_csv":
            self._api_list_csv()
        elif path == "/api/list_reports":
            self._api_list_reports()
        elif path == "/api/save_config":
            self._send_json({"status": "ok"})
        elif path.startswith("/reports/"):
            self._serve_report_file(path)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]

        if path == "/api/scrape":
            self._api_scrape(body)
        elif path == "/api/analyze":
            self._api_analyze(body)
        elif path == "/api/gen_word":
            self._api_gen_word()
        elif path == "/api/gen_html":
            self._api_gen_html()
        elif path == "/api/save_config":
            cfg = json.loads(body) if body else {}
            self._save_config(cfg)
            self._send_json({"status": "已保存"})
        else:
            self._send_json({"error": "not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode("utf-8") if length else ""

    def _send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _serve_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_report_file(self, path):
        fname = os.path.basename(urllib.parse.unquote(path))
        fpath = os.path.join(REPORTS_DIR, fname)
        if os.path.isfile(fpath):
            self.send_response(200)
            ct = "text/html" if fname.endswith(".html") else "application/octet-stream"
            self.send_header("Content-Type", ct)
            self.end_headers()
            with open(fpath, "rb") as f:
                self.wfile.write(f.read())
        else:
            self._send_json({"error": "not found"}, 404)

    # ── API Handlers ──

    def _api_search(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        flight_nums = [f.strip().upper() for f in params.get("flight", [""])[0].split(",") if f.strip()]
        aircraft = params.get("aircraft", [""])[0].strip().upper()
        date_from = params.get("from", [""])[0]
        date_to = params.get("to", [""])[0]

        if not flight_nums and not aircraft:
            self._send_json({"error": "请输入航班号或飞机号"})
            return

        import requests as req
        results = []
        search_terms = flight_nums if flight_nums else [aircraft]
        for fn in search_terms:
            try:
                r = req.get(f"{SCRAPE_URL}/getFlightListByAN",
                           params={"fi": fn, "staDate": date_from, "endDate": date_to}, timeout=30)
                data = r.json()
                if isinstance(data, dict):
                    data = data.get("data", [])
                for fl in data:
                    fid = fl.get("fLIGHTID", "")
                    tko = fl.get("tKO_TIME", "")[:10]
                    ac = fl.get("aIRCRAFT", "")
                    if aircraft and aircraft not in ac.upper():
                        continue
                    dep = fl.get("tKO_FIELD", "")
                    arr = fl.get("dES_FIELD", "")
                    direction = "回程" if ("布鲁塞尔" in arr or "福州" in arr) else "去程"
                    key = f"{tko}_{fid}"
                    results.append({"key": key, "dir": direction, "date": tko,
                                    "flight": fn, "ac": ac, "route": f"{dep}→{arr}"})
            except Exception as e:
                print(f"  search error: {e}")
        self._send_json({"results": results})

    def _api_scrape(self, body):
        keys = json.loads(body).get("keys", [])
        if not keys:
            self._send_json({"status": "无航班"})
            return
        import requests as req
        csv_dir = os.path.join(BASE_DIR, "flight_csv")
        os.makedirs(csv_dir, exist_ok=True)
        done = 0
        for key in keys:
            parts = key.split("_", 1)
            date_str = parts[0]
            # Search for full details
            try:
                r = req.get(f"{SCRAPE_URL}/getFlightListByAN",
                           params={"fi": key, "staDate": date_str, "endDate": date_str}, timeout=30)
                flights = r.json()
                if isinstance(flights, dict):
                    flights = flights.get("data", [])
                for fl in flights:
                    fid = fl.get("fLIGHTID", "")
                    if not fid:
                        continue
                    fn = fl.get("fLIGHTID", key)
                    tko = fl.get("tKO_TIME", "")
                    des = fl.get("dES_TIME", "")
                    ac = fl.get("aIRCRAFT", "")
                    dep = fl.get("tKO_FIELD", "")
                    arr = fl.get("dES_FIELD", "")
                    tko_off = fl.get("tKO_TIME_OFF", "")
                    fdate = tko[:10] if tko else date_str
                    prefix = os.path.join(csv_dir, f"{fn}_{fdate}")

                    plan = get_flight_plan_points(fid, ac, dep, arr, tko_off or tko)
                    if plan:
                        save_csv(plan, f"{prefix}_plan_track.csv")
                        save_csv(plan, f"{prefix}_plan_profile.csv",
                                ["name", "dist", "alt", "ful", "time", "lat", "lon"])
                    actual = get_flight_his_pos(fid, tko, des)
                    if actual:
                        if isinstance(actual, dict):
                            actual = actual.get("data", actual)
                        save_csv(actual, f"{prefix}_actual_track.csv")
                        save_csv(actual, f"{prefix}_actual_profile.csv",
                                ["gateway_time", "alt", "fob", "dis", "lat", "lon", "posPointName", "sAlt", "type"])
            except Exception as e:
                print(f"  scrape error {key}: {e}")
            done += 1
        self._send_json({"status": f"已完成 {done} 个航班"})

    def _api_list_csv(self):
        files = []
        csv_dir = os.path.join(BASE_DIR, "flight_csv")
        for root, dirs, fnames in os.walk(csv_dir):
            for f in fnames:
                if f.endswith("_plan_track.csv"):
                    base = f.replace("_plan_track.csv", "")
                    parts = base.split("_")
                    if len(parts) >= 2:
                        files.append({"key": f"{parts[0]}_{parts[1]}", "flight": parts[0],
                                      "date": parts[1], "status": "CSV ✓"})
        files.sort(key=lambda x: (x["date"], x["flight"]), reverse=True)
        self._send_json({"files": files})

    def _api_list_reports(self):
        files = []
        if os.path.exists(REPORTS_DIR):
            for f in sorted(os.listdir(REPORTS_DIR), reverse=True):
                path = os.path.join(REPORTS_DIR, f)
                if os.path.isfile(path) and not f.startswith("~$"):
                    files.append({
                        "name": f,
                        "size": f"{os.path.getsize(path)/1024:.0f} KB",
                        "time": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
                    })
        self._send_json({"files": files})

    def _api_analyze(self, body):
        data = json.loads(body) if isinstance(body, str) else body
        keys = data.get("keys", [])
        config = data.get("config", {"min_alt_deviation_ft": 1000, "min_duration_nm": 200})
        if self.country_index is None:
            self.country_index = CountryIndex(GEOJSON_PATH)
        results = {}
        for key in keys:
            parts = key.split("_", 1)
            fn, date_str = parts[0], parts[1] if len(parts) > 1 else ""
            pf, af = find_csv_files(fn, date_str)
            if pf and af:
                r = analyze_flight(pf, af, config, self.country_index)
                if r:
                    results[key] = r
        self.analysis_results = results
        self._send_json({"status": f"完成 {len(results)} 个航班", "count": len(results)})

    def _api_gen_word(self):
        if not self.analysis_results:
            self._send_json({"status": "请先运行分析"})
            return
        from review_report import finalize_to_docx, build_system_prompt, build_user_prompt, call_deepseek, load_api_config
        analysis = {"flights": self.analysis_results, "analysis_time": datetime.now().isoformat()}
        api_cfg = load_api_config()
        route_name = "航线分析"
        for k in self.analysis_results:
            route_name = k.split("_")[0]; break
        date_list = sorted(set(k.rsplit("_",1)[1] for k in self.analysis_results))
        sys_p = build_system_prompt()
        usr_p = build_user_prompt(analysis, {"route_name": route_name, "date_range": f"{date_list[0]}至{date_list[-1]}"})
        messages = [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}]
        api_resp = call_deepseek(messages, api_cfg)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        draft_path = os.path.join(REPORTS_DIR, f"{route_name}_draft_{ts}.md")
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(api_resp)
        finalize_to_docx(draft_path, analysis, draft_path.replace(".md", ".docx"))
        self._send_json({"status": "Word 报告已生成"})

    def _api_gen_html(self):
        if not self.analysis_results:
            self._send_json({"status": "请先运行分析"})
            return
        from review_report import generate_integrated_html, build_system_prompt, build_user_prompt, call_deepseek, load_api_config
        analysis = {"flights": self.analysis_results, "analysis_time": datetime.now().isoformat()}
        api_cfg = load_api_config()
        route_name = "航班分析"
        for k in self.analysis_results:
            route_name = k.split("_")[0]; break
        date_list = sorted(set(k.rsplit("_",1)[1] for k in self.analysis_results))
        config = {"min_alt_deviation_ft": 1000, "min_duration_nm": 200}
        sys_p = build_system_prompt()
        usr_p = build_user_prompt(analysis, {"route_name": route_name, "date_range": f"{date_list[0]}至{date_list[-1]}"})
        messages = [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}]
        api_resp = call_deepseek(messages, api_cfg)
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        html_path = os.path.join(REPORTS_DIR, f"{route_name}_integrated_{ts}.html")
        generate_integrated_html(analysis, api_resp, config, html_path)
        self._send_json({"status": "HTML 报告已生成"})

    def _save_config(self, cfg):
        cfg_path = os.path.join(BASE_DIR, "analysis_config.json")
        try:
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    port = 8765
    server = HTTPServer(("localhost", port), FlightAPIHandler)
    url = f"http://localhost:{port}"
    print(f"\n  航班分析服务器已启动")
    print(f"  浏览器打开: {url}")
    print(f"  按 Ctrl+C 停止\n")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务器已停止")
        server.shutdown()

if __name__ == "__main__":
    main()
