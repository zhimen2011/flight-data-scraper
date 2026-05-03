"""
AI-Powered Flight Deviation Report Generator
=============================================
Uses DeepSeek API to generate natural-language analysis reports from
structured flight deviation data.

Workflow:
  1. analyze_flight.py → analysis.json (structured data)
  2. review_report.py --data analysis.json → draft_report.md (AI-generated)
     Also saves prompt_used.txt for manual review/revision
  3. Human reviews draft, optionally edits prompt and re-generates
  4. review_report.py --draft draft_report.md --finalize → .docx report

Usage:
    # Step 1: Export analysis data
    python analyze_flight.py --flight I99806 --dates ... --json analysis.json

    # Step 2: Generate AI draft
    python review_report.py --data analysis.json

    # Step 2b: Review prompt only (no API call)
    python review_report.py --data analysis.json --prompt-only

    # Step 2c: Use custom prompt
    python review_report.py --data analysis.json --prompt my_prompt.txt

    # Step 3: Finalize approved draft to docx
    python review_report.py --data analysis.json --draft draft_report.md --finalize
"""
import os
import sys
import json
import argparse
import textwrap
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_flight import (
    analyze_flight, find_csv_files, CountryIndex, GEOJSON_PATH, CSV_DIR,
    DEFAULT_MIN_ALT_DEV_FT, DEFAULT_MIN_DURATION_NM,
    M_TO_FT,
)
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CONFIG_PATH = os.path.join(BASE_DIR, "deepseek_config.json")

# ─── DeepSeek API Client ─────────────────────────────────────────────────────

def load_api_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: Config not found: {CONFIG_PATH}")
        print("  Create the file with: api_key, base_url, model")
        sys.exit(1)
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def call_deepseek(messages, config):
    """Call DeepSeek API with chat messages. Returns response text."""
    import requests
    url = config["base_url"] + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config["model"],
        "messages": messages,
        "max_tokens": config.get("max_tokens", 4096),
        "temperature": config.get("temperature", 0.3),
    }
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ─── Prompt Builder ──────────────────────────────────────────────────────────

def build_system_prompt():
    return textwrap.dedent("""\
    你是一名资深航班运行分析专家，负责分析航班计划高度与实际飞行高度的偏差，
    并生成航线优化分析报告。

    ## 高度层标准
    - ICAO国际标准（英尺制）：FL290,FL300,FL310,FL320,FL330,FL340,FL350,FL360,FL370,FL380,FL390,FL400,FL410
      东向(000°-179°): 奇数层(FL290,310,330...)，西向(180°-359°): 偶数层(FL300,320,340...)
    - CAAC中国标准（米制）：8900m(FL291),9200m(FL301),9500m(FL311),9800m(FL321),
      10100m(FL331),10400m(FL341),10700m(FL351),11000m(FL361),11300m(FL371),
      11600m(FL381),11900m(FL391),12200m(FL401),12500m(FL411)
      中国段报告中用"XXXX米(FLxxx)"格式

    ## 报告要求
    - 只报告偏差发生率>=50%的区域，低于50%的统一写"在可接受范围内"
    - 高度层偏差用"X个高度层"描述，不要用FL编号差值
    - 国内段提前下降要定位到具体航路点
    - 结论需给出CFP优化建议（具体到高度层数字）
    - 国际段用FL格式，中国段用"米(FL)"格式
    - 语言简洁专业，参照航空公司运行控制报告风格
    """)


def build_user_prompt(analysis, config):
    """Build the user prompt with flight data."""

    flights_info = []
    for key, result in analysis.get("flights", {}).items():
        flights_info.append(f"- {key}")
        meta = result.get("metadata", {})
        flights_info.append(f"  总航程: {meta.get('total_distance_nm', '?')}nm, "
                           f"计划航路点: {meta.get('plan_waypoints_count', '?')}, "
                           f"实际轨迹点: {meta.get('actual_points_count', '?')}")

    region_stats_text = []
    for key, result in analysis.get("flights", {}).items():
        region_stats_text.append(f"\n### {key}")
        for rs in result.get("region_stats", []):
            region_stats_text.append(
                f"  {rs['region']}: 偏差范围 {rs['max_alt_dev_below']}~{rs['max_alt_dev_above']}ft, "
                f"持续 {rs['duration_nm']}nm"
            )
        # Warnings
        warnings = result.get("warnings", [])
        if warnings:
            region_stats_text.append("  显著事件:")
            for w in warnings[:8]:
                region_stats_text.append(
                    f"    {w['region']} {w['direction']} "
                    f"计划{w['plan_alt']:.0f}ft→实际{w['actual_alt']:.0f}ft "
                    f"持续{w['duration_nm']}nm"
                )

    descent_text = []
    for key, result in analysis.get("flights", {}).items():
        da = result.get("descent_analysis")
        if da and da.get("is_premature"):
            descent_text.append(
                f"\n### {key}\n"
                f"  计划下降点: {da['plan_tod_wp']}({da['plan_tod_dist']}nm, {da['plan_tod_alt']:.0f}ft)\n"
                f"  实际开始下降: {da['actual_descent_start_wp']}({da['actual_descent_start_dist']}nm)\n"
                f"  提前量: {da['descent_diff_nm']}nm\n"
                f"  途经航路点: {'→'.join(da.get('between_waypoints', []))}"
            )

    prompt = textwrap.dedent(f"""\
    根据以下飞行偏差分析数据，生成一份航线优化分析报告。

    ## 航线信息
    航线: {config.get('route_name', '未指定')}
    分析日期范围: {config.get('date_range', '')}
    分析航班共 {len(analysis.get('flights', {}))} 个

    ## 航班概况
    {chr(10).join(flights_info)}

    ## 区域偏差统计
    {chr(10).join(region_stats_text)}

    ## 下降剖面分析
    {''.join(descent_text) if descent_text else '（未检测到提前下降）'}

    ## 输出要求
    请按照以下结构输出报告（Markdown格式）：

    # 航线优化分析报告

    ## 分析依据
    （简要说明数据来源和样本量）

    ## 去程/回程航班分析（根据数据方向分组）
    （每段说明偏差统计、发生率、结论与优化建议）
    - 只列出发生率>=50%的区域
    - 中国区域使用米制高度层(如10700米(FL351))

    ## 下降剖面特征（如有提前下降）
    （定位到具体航路点，给出阶梯下降建议）

    ## 优化建议总结
    （汇总CFP修改建议，具体到高度层数字和航路点）

    请直接输出报告内容，不要输出其他说明。
    """)

    return prompt


# ─── Analysis Data Export ────────────────────────────────────────────────────

def build_analysis_json(flight_list, dates, config_params, country_index):
    """Run analysis on all flights and return structured JSON."""
    flights = {}
    for flight_num in flight_list:
        for date_str in dates:
            key = f"{flight_num}_{date_str}"
            print(f"  分析: {key}...")
            plan_file, actual_file = find_csv_files(flight_num, date_str)
            if not plan_file or not actual_file:
                print(f"    SKIP: 找不到文件")
                continue
            result = analyze_flight(plan_file, actual_file, config_params, country_index)
            if result:
                flights[key] = result
    return {"flights": flights, "analysis_time": datetime.now().isoformat()}


# ─── Draft → Docx Finalizer ──────────────────────────────────────────────────

def finalize_to_docx(draft_path, analysis, output_path):
    """Convert approved markdown draft to styled .docx with charts."""
    with open(draft_path, "r", encoding="utf-8") as f:
        markdown = f.read()

    doc = Document()
    title = doc.add_heading('航线优化分析报告', level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Simple markdown parser: split by headings and paragraphs
    current_section = None
    for line in markdown.split('\n'):
        line = line.strip()
        if not line:
            continue
        if line.startswith('# ') and not line.startswith('## '):
            doc.add_heading(line[2:], level=0)
        elif line.startswith('## '):
            doc.add_heading(line[3:], level=1)
        elif line.startswith('### '):
            doc.add_heading(line[4:], level=2)
        elif line.startswith('#### '):
            doc.add_heading(line[5:], level=3)
        elif line.startswith('- ') or line.startswith('* '):
            doc.add_paragraph(line[2:], style='List Bullet')
        elif line.startswith('1. '):
            doc.add_paragraph(line[3:], style='List Number')
        else:
            # Regular paragraph — skip markdown formatting hints
            text = line.replace('**', '').replace('*', '').replace('`', '')
            if text:
                doc.add_paragraph(text)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    print(f"  最终报告已生成: {output_path}")

    # Generate appendix
    appendix_path = output_path.replace('.docx', '_附件.docx')
    doc_app = Document()
    doc_app.add_heading('附件：飞行剖面对比图', level=0)

    from generate_docx_report import generate_profile_chart, load_plan_track, load_actual_track
    from analyze_flight import compute_deviations, mark_cruise_points

    chart_dir = os.path.join(REPORTS_DIR, "charts")
    os.makedirs(chart_dir, exist_ok=True)

    for key, result in analysis.get("flights", {}).items():
        flight, date_str = key.split("_", 1)
        doc_app.add_heading(f'{date_str}  {flight}', level=2)

        plan_file, actual_file = find_csv_files(flight, date_str)
        if plan_file and actual_file:
            plan_wp = load_plan_track(plan_file)
            actual_pts = load_actual_track(actual_file)
            dev_results = compute_deviations(actual_pts, plan_wp,
                                             {'min_alt_deviation_ft': 300, 'min_duration_nm': 30},
                                             CountryIndex(GEOJSON_PATH))
            mark_cruise_points(dev_results, plan_wp)

            chart_path = os.path.join(chart_dir, f"{key}_profile.png")
            generate_profile_chart(plan_wp, actual_pts, dev_results, flight, date_str, chart_path)
            if os.path.exists(chart_path):
                doc_app.add_picture(chart_path, width=Inches(5.5))
                doc_app.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

            # Add summary notes
            warnings = result.get("warnings", [])[:3]
            for w in warnings:
                doc_app.add_paragraph(
                    f'{w["region"]} {w["direction"]} '
                    f'计划{w["plan_alt"]:.0f}ft→实际{w["actual_alt"]:.0f}ft '
                    f'持续{w["duration_nm"]}nm'
                )

    doc_app.save(appendix_path)
    print(f"  附件已生成: {appendix_path}")
    return output_path


# ─── Main CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI航班偏差分析报告生成器")
    # Analysis inputs
    parser.add_argument("--flight", "-f", help="航班号(可逗号分隔多个)")
    parser.add_argument("--dates", help="日期列表，逗号分隔")
    parser.add_argument("--route", default=None, help="航线名称")
    parser.add_argument("--min-alt-dev", type=float, default=DEFAULT_MIN_ALT_DEV_FT)
    parser.add_argument("--min-dur", type=float, default=DEFAULT_MIN_DURATION_NM)
    # Data modes
    parser.add_argument("--data", help="已有 analysis.json 文件")
    parser.add_argument("--json", help="仅导出 analysis.json，不生成报告")
    # Prompt modes
    parser.add_argument("--prompt-only", action="store_true", help="只输出 prompt 不调用 API")
    parser.add_argument("--prompt", help="使用自定义 prompt 文件")
    # Output
    parser.add_argument("--output", "-o", default=None, help="输出文件路径")
    parser.add_argument("--finalize", action="store_true", help="将 draft markdown 转为最终 docx")
    parser.add_argument("--draft", help="已审核的 draft markdown 文件路径")

    args = parser.parse_args()

    # Load config
    api_config = load_api_config()

    # Step 1: Get analysis data
    analysis = None
    if args.data:
        with open(args.data, "r", encoding="utf-8") as f:
            analysis = json.load(f)
        print(f"加载分析数据: {args.data}")
    elif args.flight and args.dates:
        flights = [f.strip() for f in args.flight.split(",")]
        dates = [d.strip() for d in args.dates.split(",")]
        route = args.route or f"{flights[0]}航线"

        print("加载国界数据...")
        country_index = CountryIndex(GEOJSON_PATH)

        config = {
            "min_alt_deviation_ft": args.min_alt_dev,
            "min_duration_nm": args.min_dur,
            "route_name": route,
            "date_range": f"{dates[0]}至{dates[-1]}",
        }

        print(f"\n分析航班: {', '.join(flights)}, 日期: {', '.join(dates)}")
        analysis = build_analysis_json(flights, dates, config, country_index)
        print(f"\n共分析 {len(analysis.get('flights', {}))} 个航班记录")

        if args.json:
            json_path = args.json
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(analysis, f, ensure_ascii=False, indent=2)
            print(f"分析数据已导出: {json_path}")
    else:
        print("ERROR: 请指定 --data 或 --flight + --dates")
        sys.exit(1)

    # Step 2: Determine output paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    route_name = (args.route or "report").replace("/", "-").replace("、", "-")
    draft_path = args.output or os.path.join(REPORTS_DIR, f"{route_name}_draft_{timestamp}.md")
    prompt_path = draft_path.replace(".md", "_prompt.txt")

    # Step 3: Finalize mode — convert approved draft to docx
    if args.finalize:
        draft_file = args.draft or draft_path
        if not os.path.exists(draft_file):
            print(f"ERROR: draft file not found: {draft_file}")
            sys.exit(1)
        docx_path = draft_file.replace(".md", ".docx")
        finalize_to_docx(draft_file, analysis, docx_path)
        return

    # Step 4: Build prompt
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(analysis, {
        "route_name": args.route or "未指定",
        "date_range": getattr(args, 'date_range', ''),
    })

    full_prompt = f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"

    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(full_prompt)
    print(f"\nPrompt 已保存: {prompt_path}")

    # Step 5: prompt-only mode
    if args.prompt_only:
        print("\n" + "=" * 60)
        print(full_prompt)
        print("=" * 60)
        print("\n审阅后可使用以下命令重新生成:")
        print(f"  python review_report.py --data {args.data or args.json} --prompt {prompt_path}")
        return

    # Step 6: Load custom prompt if provided
    if args.prompt:
        with open(args.prompt, "r", encoding="utf-8") as f:
            custom = f.read()
        # Split system/user from custom prompt file
        if "=== USER PROMPT ===" in custom:
            parts = custom.split("=== USER PROMPT ===")
            system_prompt = parts[0].replace("=== SYSTEM PROMPT ===\n", "").strip()
            user_prompt = parts[1].strip()
        else:
            user_prompt = custom  # Use entire file as user prompt

    # Step 7: Call DeepSeek API
    print(f"\n调用 DeepSeek API (model: {api_config['model']})...")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = call_deepseek(messages, api_config)
    except Exception as e:
        print(f"API 调用失败: {e}")
        print(f"Prompt 已保存至 {prompt_path}，可手动使用。")
        sys.exit(1)

    # Step 8: Save draft
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(response)
    print(f"AI 初稿已生成: {draft_path}")
    print(f"\n审阅后使用以下命令生成最终 docx:")
    print(f"  python review_report.py --data {args.data or args.json} --draft {draft_path} --finalize")


if __name__ == "__main__":
    main()
