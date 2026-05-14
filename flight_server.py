"""
Flight Analysis Web GUI Server
Start with: python flight_server.py → http://localhost:8765
"""
import os, sys, json, csv, glob, threading, webbrowser, io, urllib.parse, re, shutil
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_flight import (
    BASE_DIR as APP_BASE_DIR,
    CountryIndex, GEOJSON_PATH, CSV_DIR, analyze_flight, find_csv_files, load_config,
)
from flight_data_scraper import (
    get_flight_plan_points, get_flight_his_pos, save_csv,
)
from flight_keys import build_flight_key, display_datetime_from_key, parse_flight_key

BASE_DIR = APP_BASE_DIR
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CSV_ARCHIVE_DIR = os.path.join(BASE_DIR, "flight_csv_archive")
SCRAPE_URL = "http://192.168.8.18:8082"
os.makedirs(REPORTS_DIR, exist_ok=True)

GUI_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>航班轨迹偏差分析工具</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:"Microsoft YaHei","SimHei",sans-serif;background:#1a1a2e;color:#e0e0e0;height:100vh;display:flex;flex-direction:column;}
.header{background:#16213e;padding:10px 20px;border-bottom:2px solid #0f3460;}
.header h1{font-size:18px;color:#e94560;}
.tabs{display:flex;background:#0f3460;border-bottom:1px solid #1a1a2e;}
.tab{padding:10px 20px;cursor:pointer;font-size:13px;border-bottom:2px solid transparent;transition:.2s;}
.tab:hover{background:#1a1a3e;}.tab.active{border-bottom-color:#e94560;color:#e94560;}
.tab-content{flex:1;overflow-y:auto;padding:12px 16px;display:none;}
.tab-content.active{display:block;}
.search-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;}
.search-bar input,select{background:#16213e;border:1px solid #333;color:#eee;padding:6px 10px;border-radius:4px;font-size:12px;}
.search-bar button{background:#e94560;color:#fff;border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;}
button:hover{opacity:0.85;}
table{width:100%;border-collapse:collapse;font-size:12px;margin:8px 0;}
th{background:#0f3460;color:#e94560;padding:6px 8px;text-align:left;position:sticky;top:0;}
td{padding:5px 8px;border-bottom:1px solid #1a1a2e;}
tr:hover td{background:#1a1a3e;}
input[type=checkbox]{accent-color:#e94560;transform:scale(1.2);}
.btn-row{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap;align-items:center;}
.btn-row button,.btn-primary,.btn-secondary{padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px;border:none;}
.btn-primary{background:#e94560;color:#fff;}.btn-secondary{background:#16213e;color:#ccc;border:1px solid #333;}
.progress{width:100%;height:6px;background:#0f3460;border-radius:3px;margin:8px 0;overflow:hidden;}
.progress-bar{height:100%;background:#e94560;width:0%;transition:width .3s;}
.status{font-size:12px;color:#aaa;margin:4px 0;}
.config-row{display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin:8px 0;}
.config-row label{font-size:12px;color:#aaa;}
.config-row input[type=range]{width:140px;accent-color:#e94560;}
.val{color:#e94560;font-weight:bold;}
.section-box{background:#16213e;border:1px solid #0f3460;border-radius:6px;padding:10px 14px;margin:8px 0;}
.section-box h3{font-size:13px;color:#e94560;margin-bottom:6px;cursor:pointer;}
.section-box h3 span{font-size:11px;color:#888;}
.section-box textarea{width:100%;height:200px;background:#0f3460;color:#ccc;border:1px solid #333;border-radius:4px;font-size:11px;font-family:Consolas,monospace;padding:8px;resize:vertical;}
.section-box textarea:focus{outline:none;border-color:#e94560;}
.upload-row{display:flex;gap:8px;align-items:center;margin:6px 0;}
.upload-row input[type=file]{display:none;}
.upload-row .file-label{background:#0f3460;color:#ccc;border:1px solid #333;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;}
.upload-row .file-label:hover{border-color:#e94560;}
.upload-row .file-name{font-size:11px;color:#888;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
select{background:#16213e;border:1px solid #333;color:#eee;padding:3px 6px;border-radius:3px;font-size:11px;}
select:focus{outline:none;border-color:#e94560;}
.dot-ok{color:#4caf50;}.dot-fail{color:#f44336;}.dot-unknown{color:#ffaa00;}
</style></head><body>
<div class="header"><h1>✈ 航班轨迹偏差分析工具</h1></div>
<div class="tabs">
  <div class="tab active" onclick="switchTab(0)">1. 数据抓取</div>
  <div class="tab" onclick="switchTab(1)">2. 偏差分析</div>
  <div class="tab" onclick="switchTab(2)">3. 报告查看</div>
</div>

<!-- TAB 0: Scrape -->
<div class="tab-content active" id="tab0">
  <div class="search-bar">
    <span>去程航班号:</span><input id="sOutboundFlights" placeholder="I99857,I99859" style="width:150px;">
    <span>回程航班号:</span><input id="sReturnFlights" placeholder="I99858,I99860" style="width:150px;">
    <span>飞机号:</span><input id="sAircraft" placeholder="B-2079" style="width:80px;">
    <span>日期:</span><input id="sDateFrom" value="2026-04-01" style="width:95px;">
    <span>~</span><input id="sDateTo" value="2026-04-30" style="width:95px;">
    <button onclick="searchFlights()">搜索并添加到结果池</button>
  </div>
  <div class="status" id="baseInfo" style="display:none;"></div>
  <div class="btn-row">
    <button class="btn-secondary" onclick="selPool('outbound')">全选去程</button>
    <button class="btn-secondary" onclick="selPool('return')">全选回程</button>
    <button class="btn-secondary" onclick="selPool('unknown')">全选未知</button>
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
  <!-- Thresholds -->
  <div class="config-row">
    <label>高度偏差阈值: <input type="range" id="aAlt" min="200" max="3000" value="1000" step="100" oninput="$('aAltVal').textContent=$('aAlt').value">
      <span class="val" id="aAltVal">1000</span> ft</label>
    <label>持续时间阈值: <input type="range" id="aDur" min="10" max="500" value="50" step="10" oninput="$('aDurVal').textContent=$('aDur').value">
      <span class="val" id="aDurVal">50</span> nm</label>
    <label><input type="checkbox" id="aCruise" checked> 仅巡航段</label>
    <button class="btn-secondary" onclick="saveConfig()">保存为默认</button>
  </div>

  <!-- CSV list -->
  <div class="btn-row">
    <button class="btn-secondary" onclick="selAnalyze(true)">全选</button>
    <button class="btn-secondary" onclick="selAnalyze(false)">清空</button>
    <button class="btn-secondary" onclick="refreshAnalyze()">刷新列表</button>
    <button class="btn-secondary" onclick="archiveCurrentCsv()">归档当前数据</button>
  </div>
  <div class="status" id="analyzeStatus">已有数据</div>
  <div id="analyzeTable"></div>

  <!-- Action buttons -->
  <div class="btn-row">
    <button class="btn-primary" onclick="runAllReports()">一键生成报告</button>
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
  if(n===1) refreshAnalyze();
  if(n===2) refreshReports();
}
function toggleSection(id){var e=$(id);e.style.display=e.style.display==='none'?'block':'none';}
async function api(path,opt={}){
  try{
    let url='/api'+path,r;
    if(opt.method==='POST'){r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(opt.body||{})});}
    else{r=await fetch(url);}
    if(!r.ok){let e=await r.text();throw new Error(e||r.statusText);}
    return await r.json();
  }catch(e){alert('请求错误: '+e.message);return null;}
}
function $(id){return document.getElementById(id);}
function setStatus(id,txt){$(id).textContent=txt;}
function setProgress(id,pct){$(id).style.width=pct+'%';}
async function readFileAsText(file){return new Promise((resolve)=>{let r=new FileReader();r.onload=()=>resolve(r.result);r.readAsText(file);});}

// ── TAB 0: Pool ──
let pool=[],poolChecks={},autoBase='';
async function searchFlights(){
  let outbound=$('sOutboundFlights').value;
  let returnFlights=$('sReturnFlights').value;
  let qs=`?outbound=${encodeURIComponent(outbound)}&returnFlights=${encodeURIComponent(returnFlights)}&aircraft=${encodeURIComponent($('sAircraft').value)}&from=${$('sDateFrom').value}&to=${$('sDateTo').value}`;
  let r=await api('/search'+qs);
  if(!r){return;} if(r.error){alert(r.error);return;}
  let added=0;
  r.results.forEach(fl=>{
    let k=fl.key;
    let existing=pool.find(p=>p.key===k);
    if(existing){existing.dir=fl.dir||existing.dir;}
    else{pool.push(fl);poolChecks[k]=true;added++;}
  });
  $('baseInfo').style.display='block';
  $('baseInfo').textContent='方向已按去程/回程输入框写入，表格中仍可手动修正。';
  renderPool();setStatus('poolStatus','结果池（共 '+pool.length+' 条）');alert('新增 '+added+' 条');
}
function renderPool(){
  let h='<table><tr><th>✓</th><th>方向</th><th>日期</th><th>航班号</th><th>机号</th><th>起降</th></tr>';
  pool.forEach(p=>{
    let ck=poolChecks[p.key]!==false?'☑':'☐';
    let selId='dir_'+p.key.replace(/[^a-zA-Z0-9]/g,'_');
    h+=`<tr><td onclick="togglePool('${p.key}')" style="cursor:pointer">${ck}</td>
      <td><select id="${selId}" onchange="changeDir('${p.key}',this.value)" style="width:65px;">
        <option value="去程"${p.dir==='去程'?' selected':''}>去程</option>
        <option value="回程"${p.dir==='回程'?' selected':''}>回程</option>
        <option value="未知"${p.dir==='未知'?' selected':''}>未知</option>
      </select></td>
      <td>${p.date}</td><td>${p.flight}</td><td>${p.ac}</td><td>${p.route}</td></tr>`;
  });
  h+='</table>';$('poolTable').innerHTML=h;
}
function changeDir(key,val){let p=pool.find(x=>x.key===key);if(p)p.dir=val;}
function togglePool(k){poolChecks[k]=!poolChecks[k];renderPool();}
function selPool(mode){
  pool.forEach(p=>{
    if(mode==='all') poolChecks[p.key]=true;
    else if(mode==='none') poolChecks[p.key]=false;
    else if(mode==='outbound') poolChecks[p.key]=p.dir==='去程';
    else if(mode==='return') poolChecks[p.key]=p.dir==='回程';
    else if(mode==='unknown') poolChecks[p.key]=p.dir==='未知';
  });
  renderPool();
}
function removeUnchecked(){pool=pool.filter(p=>poolChecks[p.key]!==false);renderPool();setStatus('poolStatus','结果池（共 '+pool.length+' 条）');}
async function startScrape(){
  let flights=pool.filter(p=>poolChecks[p.key]!==false);
  if(!flights.length){alert('请勾选航班');return;}
  setStatus('scrapeStatus','抓取中...');setProgress('scrapeProgress',0);
  let r=await api('/scrape',{method:'POST',body:{flights}});
  setProgress('scrapeProgress',100);if(!r){setStatus('scrapeStatus','失败');return;}
  setStatus('scrapeStatus',r.status);
  if(r.results){alert(r.status+'\n'+r.results.map(x=>x.flight+' '+x.date+': '+(x.ok?'OK '+x.detail:'FAIL '+x.detail)).join('\n'));}
  setTimeout(refreshAnalyze,500);
}

// ── TAB 1: Analyze ──
let analyzeList=[],analyzeChecks={};
async function refreshAnalyze(){
  let r=await api('/list_csv');if(!r)return;
  analyzeList=r.files||[];
  analyzeList.forEach(f=>{if(!(f.key in analyzeChecks))analyzeChecks[f.key]=false;});
  renderAnalyze();setStatus('analyzeStatus','共 '+analyzeList.length+' 个航班数据（请勾选要分析的）');
}
function renderAnalyze(){
  let h='<table><tr><th>✓</th><th>日期</th><th>航班号</th><th>状态</th></tr>';
  analyzeList.forEach(f=>{
    let ck=analyzeChecks[f.key]!==false?'☑':'☐';
    h+=`<tr><td onclick="toggleAnalyze('${f.key}')" style="cursor:pointer">${ck}</td><td>${f.date}</td><td>${f.flight}</td><td>${f.status||'CSV ✓'}</td></tr>`;
  });
  h+='</table>';$('analyzeTable').innerHTML=h;
}
function toggleAnalyze(k){analyzeChecks[k]=!analyzeChecks[k];renderAnalyze();}
function selAnalyze(state){analyzeList.forEach(f=>analyzeChecks[f.key]=state);renderAnalyze();}
function updateCfg(){$('aAltVal').textContent=$('aAlt').value;$('aDurVal').textContent=$('aDur').value;}
async function saveConfig(){
  let cfg={min_alt_deviation_ft:parseInt($('aAlt').value),min_duration_nm:parseInt($('aDur').value)};
  await api('/save_config',{method:'POST',body:cfg});alert('配置已保存');
}
async function archiveCurrentCsv(){
  if(!confirm('归档后当前偏差分析列表会清空，旧数据会移动到 flight_csv_archive。继续吗？')) return;
  let r=await api('/archive_csv',{method:'POST',body:{}});
  if(!r) return;
  analyzeChecks={};
  await refreshAnalyze();
  alert(r.status||'已归档');
}
async function runAllReports(){
  let keys=analyzeList.filter(f=>analyzeChecks[f.key]!==false).map(f=>f.key);
  if(!keys.length){alert('请勾选航班');return;}
  let cfg={min_alt_deviation_ft:parseInt($('aAlt').value),min_duration_nm:parseInt($('aDur').value)};
  setStatus('analyzeMsg','分析并生成统计表、附件和 HTML 报告...');setProgress('analyzeProgress',0);
  let r=await api('/run_all_reports',{method:'POST',body:{keys,config:cfg}});
  setProgress('analyzeProgress',100);if(!r){setStatus('analyzeMsg','生成失败');return;}
  setStatus('analyzeMsg',r.status||('完成 '+r.count+' 个航班'));
  refreshReports();
}

// ── TAB 2: Reports ──
async function refreshReports(){
  let r=await api('/list_reports');if(!r)return;
  let h='<table><tr><th>文件名</th><th>大小</th><th>时间</th><th>操作</th></tr>';
  (r.files||[]).forEach(f=>{h+=`<tr><td>${f.name}</td><td>${f.size}</td><td>${f.time}</td><td><a href="/reports/${encodeURIComponent(f.name)}" target="_blank" style="color:#e94560">打开</a></td></tr>`;});
  h+='</table>';$('reportsTable').innerHTML=h;
}
function openReportsFolder(){window.open('/reports/');}

// Init
refreshAnalyze();refreshReports();
</script></body></html>
""";

# ─── HTTP Server ──────────────────────────────────────────────────────────────

def archive_active_csv_data(csv_dir=None, archive_root=None):
    """Move current working CSV data out of flight_csv before a new scrape batch."""
    csv_dir = csv_dir or CSV_DIR
    archive_root = archive_root or CSV_ARCHIVE_DIR
    if not os.path.isdir(csv_dir):
        return {"archived": 0, "archive_dir": ""}

    candidates = []
    for name in os.listdir(csv_dir):
        path = os.path.join(csv_dir, name)
        if os.path.isfile(path) and (name.lower().endswith(".csv") or name == "metadata.json"):
            candidates.append(path)

    if not candidates:
        return {"archived": 0, "archive_dir": ""}

    os.makedirs(archive_root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = os.path.join(archive_root, stamp)
    suffix = 2
    while os.path.exists(archive_dir):
        archive_dir = os.path.join(archive_root, f"{stamp}_{suffix}")
        suffix += 1
    os.makedirs(archive_dir, exist_ok=True)

    for path in candidates:
        shutil.move(path, os.path.join(archive_dir, os.path.basename(path)))

    return {"archived": len(candidates), "archive_dir": archive_dir}


def list_active_csv_files(csv_dir=None):
    """List only the current working CSV files; archived data lives elsewhere."""
    csv_dir = csv_dir or CSV_DIR
    files = []
    if not os.path.isdir(csv_dir):
        return files
    for f in os.listdir(csv_dir):
        path = os.path.join(csv_dir, f)
        if os.path.isfile(path) and f.endswith("_plan_track.csv"):
            base = f.replace("_plan_track.csv", "")
            actual_path = os.path.join(csv_dir, f"{base}_actual_track.csv")
            if not os.path.isfile(actual_path):
                continue
            parsed = parse_flight_key(base)
            if parsed.flight and parsed.date:
                files.append({
                    "key": base,
                    "flight": parsed.flight,
                    "date": display_datetime_from_key(base),
                    "status": "CSV",
                })
    files.sort(key=lambda x: (x["date"], x["flight"]), reverse=True)
    return files


class FlightAPIHandler(BaseHTTPRequestHandler):
    search_pool = []
    analysis_results = {}
    last_config = {"min_alt_deviation_ft": 1000, "min_duration_nm": 50}
    country_index = None

    def log_message(self, format, *args): pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"): self._serve_html(GUI_HTML)
        elif path == "/api/search": self._api_search()
        elif path == "/api/list_csv": self._api_list_csv()
        elif path == "/api/list_reports": self._api_list_reports()
        elif path.startswith("/reports/"): self._serve_report_file(path)
        else: self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]

        data = json.loads(body) if body else {}
        if path == "/api/scrape": self._api_scrape(data)
        elif path == "/api/archive_csv": self._api_archive_csv()
        elif path == "/api/analyze": self._api_analyze(data)
        elif path == "/api/run_all_reports": self._api_run_all_reports(data)
        elif path == "/api/gen_word": self._api_gen_word()
        elif path == "/api/gen_html": self._api_gen_html()
        elif path == "/api/save_config": self._save_config(data); self._send_json({"status":"ok"})
        else: self._send_json({"error": "not found"}, 404)

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
        for h in ("Cache-Control","Pragma","Expires"):
            self.send_header(h, "no-cache")
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
            with open(fpath, "rb") as f: self.wfile.write(f.read())
        else: self._send_json({"error":"not found"}, 404)

    # ── API: Search ──
    def _api_search(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        def parse_flights(name):
            return [f.strip().upper() for f in params.get(name,[""])[0].split(",") if f.strip()]
        outbound_nums = parse_flights("outbound")
        return_nums = parse_flights("returnFlights")
        legacy_nums = parse_flights("flight")
        aircraft = params.get("aircraft",[""])[0].strip().upper()
        date_from = params.get("from",[""])[0]; date_to = params.get("to",[""])[0]
        if not outbound_nums and not return_nums and not legacy_nums and not aircraft:
            self._send_json({"error":"请输入去程或回程航班号，或飞机号"}); return

        import requests as req
        results_by_key = {}; dep_counts = {}
        search_terms = []
        search_terms.extend((fn, "去程") for fn in outbound_nums)
        search_terms.extend((fn, "回程") for fn in return_nums)
        search_terms.extend((fn, "") for fn in legacy_nums)
        if not search_terms and aircraft:
            search_terms.append((aircraft, ""))
        for fn, forced_dir in search_terms:
            try:
                r = req.get(f"{SCRAPE_URL}/getFlightListByAN",
                    params={"fi":fn,"staDate":date_from,"endDate":date_to}, timeout=30)
                data = r.json()
                if isinstance(data, dict): data = data.get("data",[])
                for fl in data:
                    fid = fl.get("fLIGHTID",""); tko = fl.get("tKO_TIME","")[:10]
                    ac = fl.get("aIRCRAFT","")
                    if aircraft and aircraft not in ac.upper(): continue
                    dep = fl.get("tKO_FIELD",""); arr = fl.get("dES_FIELD","")
                    dep_counts[dep] = dep_counts.get(dep,0) + 1
                    key = build_flight_key(fid or fn, tko, fl.get("tKO_TIME", ""))
                    if key in results_by_key:
                        if forced_dir and results_by_key[key].get("dir") in ("", "?", "未知"):
                            results_by_key[key]["dir"] = forced_dir
                        continue
                    results_by_key[key] = {"key":key,"dir":forced_dir or "未知","date":tko,"flight":fid or fn,"ac":ac,
                        "route":f"{dep}→{arr}","flightId":fid,"aircraft":ac,
                        "tkoTime":fl.get("tKO_TIME",""),"desTime":fl.get("dES_TIME",""),
                        "tkoTimeOff":fl.get("tKO_TIME_OFF",""),"depAirport":dep,"arrAirport":arr}
            except Exception as e: print(f"  search error: {e}")

        # Fallback direction detection is only used for aircraft-only or legacy searches.
        auto_base = ""
        if dep_counts:
            auto_base = max(dep_counts, key=dep_counts.get)
            for r2 in results_by_key.values():
                if r2["dir"] in ("", "?", "未知"):
                    if r2["depAirport"] == auto_base: r2["dir"] = "去程"
                    elif r2["arrAirport"] == auto_base: r2["dir"] = "回程"
                    else: r2["dir"] = "未知"
        results = sorted(
            results_by_key.values(),
            key=lambda item: (item.get("date",""), item.get("tkoTime",""), item.get("flight","")),
            reverse=True,
        )
        self._send_json({"results":results,"autoBase":auto_base})

    # ── API: Scrape ──
    def _api_scrape(self, data):
        flights = data.get("flights",[])
        if not flights: self._send_json({"status":"无航班"}); return
        import requests as req
        csv_dir = CSV_DIR; os.makedirs(csv_dir,exist_ok=True)
        archive_result = archive_active_csv_data(csv_dir)
        results = []
        for fl_info in flights:
            s = {"flight":fl_info.get("flight","?"),"date":fl_info.get("date","?"),"ok":False,"detail":""}
            try:
                fid=fl_info.get("flightId","");fn=fl_info.get("flight","");ac=fl_info.get("aircraft","")
                dep=fl_info.get("depAirport","");arr=fl_info.get("arrAirport","")
                tko=fl_info.get("tkoTime","");des=fl_info.get("desTime","")
                tko_off=fl_info.get("tkoTimeOff","");fdate=tko[:10] if tko else fl_info.get("date","")
                file_key = build_flight_key(fid or fn, fdate, tko)
                prefix=os.path.join(csv_dir,file_key);details=[]
                plan_saved = False
                actual_saved = False
                plan=get_flight_plan_points(fid,ac,dep,arr,tko_off or tko)
                if plan and len(plan)>0:
                    plan_saved = save_csv(plan,f"{prefix}_plan_track.csv")
                    save_csv(plan,f"{prefix}_plan_profile.csv",["name","dist","alt","ful","time","lat","lon"])
                    details.append(f"plan={len(plan)}pts")
                else: details.append("plan=empty")
                actual=get_flight_his_pos(fid,tko,des)
                ad=actual
                if isinstance(actual,dict): ad=actual.get("data",[])
                if ad and len(ad)>0:
                    actual_saved = save_csv(ad,f"{prefix}_actual_track.csv")
                    save_csv(ad,f"{prefix}_actual_profile.csv",["gateway_time","alt","fob","dis","lat","lon","posPointName","sAlt","type"])
                    details.append(f"actual={len(ad)}pts")
                else: details.append("actual=empty")
                if plan_saved and actual_saved:
                    try:
                        from local_report import save_metadata_entry
                        save_metadata_entry(file_key, {
                            "flight_id": fid,
                            "flight": fn,
                            "aircraft": ac,
                            "dep_airport": dep,
                            "arr_airport": arr,
                            "tko_time": tko,
                            "des_time": des,
                            "direction": fl_info.get("dir", ""),
                        })
                    except Exception as meta_error:
                        details.append(f"metadata={str(meta_error)[:40]}")
                else:
                    details.append("missing required csv")
                s["ok"]=plan_saved and actual_saved;s["detail"]=", ".join(details)
            except Exception as e: s["detail"]=str(e)[:100]
            results.append(s)
        ok_count=sum(1 for r in results if r["ok"])
        prefix = f"已归档旧数据 {archive_result['archived']} 个文件；" if archive_result["archived"] else ""
        self._send_json({"status":f"{prefix}完成 {ok_count}/{len(results)} 个航班","results":results,
            "archive": {"count": archive_result["archived"], "dir": os.path.basename(archive_result["archive_dir"])}})

    def _api_archive_csv(self):
        result = archive_active_csv_data()
        if result["archived"]:
            self._send_json({"status":f"已归档当前数据 {result['archived']} 个文件到 {os.path.basename(result['archive_dir'])}",
                "count": result["archived"], "dir": os.path.basename(result["archive_dir"])})
        else:
            self._send_json({"status":"当前没有可归档的 CSV 数据","count":0,"dir":""})

    # ── API: List ──
    def _api_list_csv(self):
        self._send_json({"files":list_active_csv_files()})

    def _api_list_reports(self):
        files=[]
        if os.path.exists(REPORTS_DIR):
            for f in sorted(os.listdir(REPORTS_DIR),reverse=True):
                p=os.path.join(REPORTS_DIR,f)
                if os.path.isfile(p) and not f.startswith("~$"):
                    files.append({"name":f,"size":f"{os.path.getsize(p)/1024:.0f} KB","time":datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M")})
        self._send_json({"files":files})

    # ── API: Analyze ──
    def _run_analysis(self, keys, config):
        if FlightAPIHandler.country_index is None:
            FlightAPIHandler.country_index = CountryIndex(GEOJSON_PATH)
        results = {}
        for key in keys:
            parsed = parse_flight_key(key)
            fn, ds = parsed.flight, parsed.date_key
            pf, af = find_csv_files(fn, ds)
            if pf and af:
                r = analyze_flight(pf, af, config, FlightAPIHandler.country_index)
                if r:
                    results[key] = r
        FlightAPIHandler.analysis_results = results
        FlightAPIHandler.last_config = config
        return results

    def _analysis_payload(self):
        return {
            "flights": FlightAPIHandler.analysis_results,
            "analysis_time": datetime.now().isoformat(),
        }

    def _api_analyze(self, data):
        keys=data.get("keys",[]);config=data.get("config",{"min_alt_deviation_ft":1000,"min_duration_nm":50})
        results = self._run_analysis(keys, config)
        self._send_json({"status":f"完成 {len(results)} 个航班","count":len(results)})

    # ── API: Generate Reports ──
    def _api_run_all_reports(self, data):
        keys = data.get("keys", [])
        config = data.get("config", {"min_alt_deviation_ft": 1000, "min_duration_nm": 50})
        if not keys:
            self._send_json({"status":"请先勾选航班","count":0}); return
        try:
            results = self._run_analysis(keys, config)
            if not results:
                self._send_json({"status":"未分析出可生成报告的航班","count":0}); return
            from local_report import generate_stats_report, generate_appendix_report, generate_table_html_report
            analysis = self._analysis_payload()
            stats_path = generate_stats_report(analysis, REPORTS_DIR)
            appendix_path = generate_appendix_report(analysis, REPORTS_DIR)
            html_path = generate_table_html_report(analysis, config, REPORTS_DIR)
        except Exception as e:
            self._send_json({"status":f"生成失败: {e}"}); return
        files = [os.path.basename(stats_path), os.path.basename(appendix_path), os.path.basename(html_path)]
        self._send_json({
            "status": f"完成 {len(results)} 个航班，统计表 Word、附件 Word、HTML 表格报告已生成",
            "count": len(results),
            "files": files,
        })

    def _api_gen_word(self):
        if not FlightAPIHandler.analysis_results: self._send_json({"status":"请先运行分析"}); return
        from local_report import generate_stats_report, generate_appendix_report
        analysis = self._analysis_payload()
        try:
            path = generate_stats_report(analysis, REPORTS_DIR)
            appendix_path = generate_appendix_report(analysis, REPORTS_DIR)
        except Exception as e:
            self._send_json({"status":f"生成失败: {e}"}); return
        self._send_json({
            "status":"统计表 Word 和附件 Word 已生成",
            "files":[os.path.basename(path), os.path.basename(appendix_path)],
        })

    def _api_gen_html(self):
        if not FlightAPIHandler.analysis_results: self._send_json({"status":"请先运行分析"}); return
        from local_report import generate_table_html_report
        analysis = self._analysis_payload()
        config = FlightAPIHandler.last_config
        try:
            path = generate_table_html_report(analysis, config, REPORTS_DIR)
        except Exception as e:
            self._send_json({"status":f"生成失败: {e}"}); return
        self._send_json({"status":"HTML 表格报告已生成","file":os.path.basename(path)})

    def _save_config(self, cfg):
        with open(os.path.join(BASE_DIR,"analysis_config.json"),"w") as f:
            json.dump(cfg,f,indent=2)

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(REPORTS_DIR,exist_ok=True)
    port=8765
    server=HTTPServer(("localhost",port),FlightAPIHandler)
    url=f"http://localhost:{port}"
    print(f"\n  航班分析服务器已启动\n  浏览器打开: {url}\n  按 Ctrl+C 停止\n")
    if os.environ.get("FLIGHT_SERVER_NO_BROWSER") != "1":
        webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n  服务器已停止"); server.shutdown()

if __name__=="__main__": main()
