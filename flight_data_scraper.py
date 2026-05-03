"""
航班轨迹数据抓取工具
从 http://192.168.8.18:8082 抓取智能分析-历史回放中的计划/实际轨迹和剖面数据
输出为 CSV 文件，按日期和航班号命名

用法: python flight_data_scraper.py --flight I99806 --date 2026-04-29
"""

import requests
import csv
import os
import argparse
import sys

BASE_URL = "http://192.168.8.18:8082"


def search_flights(flight_number, date_start, date_end=None):
    """搜索航班列表"""
    if date_end is None:
        date_end = date_start
    url = f"{BASE_URL}/getFlightListByAN"
    params = {"fi": flight_number, "staDate": date_start, "endDate": date_end}
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def get_flight_his_pos(flight_id, begin_time, end_time):
    """获取历史飞行位置数据 (实际轨迹+剖面)"""
    url = f"{BASE_URL}/getFlightHisPos"
    params = {"fi": flight_id, "beginTime": begin_time, "endTime": end_time}
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


def get_flight_plan_points(flight_id, aircraft, dep_airport, arr_airport, date_str):
    """获取飞行计划航路点 (计划轨迹+剖面)"""
    url = f"{BASE_URL}/getFlightPlanPoints"
    params = {
        "fi": flight_id,
        "an": aircraft,
        "depAirport": dep_airport,
        "arrAirport": arr_airport,
        "date": date_str,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def save_csv(data, filename, fieldnames=None):
    """保存数据为 CSV 文件"""
    if not data:
        print(f"  跳过 {filename}: 无数据")
        return False
    if fieldnames is None:
        fieldnames = list(data[0].keys())
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    print(f"  已保存: {filename} ({len(data)} 条记录)")
    return True


def process_flight(flight_number, date_str, end_date=None):
    """处理航班: 抓取所有数据并保存 CSV"""
    if end_date is None:
        end_date = date_str

    print(f"\n{'='*60}")
    print(f"处理航班: {flight_number}, 日期: {date_str} ~ {end_date}")
    print(f"{'='*60}")

    # Step 1: 搜索航班
    print("\n[1/4] 搜索航班...")
    flights = search_flights(flight_number, date_str, end_date)
    if not flights:
        print(f"  未找到航班 {flight_number} 在 {date_str} 的数据")
        return
    print(f"  找到 {len(flights)} 条航班记录")

    prefix_counter = {}  # 用于同日多班次去重

    for idx, flight in enumerate(flights):
        flight_id = flight.get("fLIGHTID", "")
        aircraft = flight.get("aIRCRAFT", "")
        tko_time = flight.get("tKO_TIME", "")
        des_time = flight.get("dES_TIME", "")
        tko_time_off = flight.get("tKO_TIME_OFF", "")
        dep_airport = flight.get("tKO_FIELD", "")
        arr_airport = flight.get("dES_FIELD", "")
        arr_name = flight.get("aRRI", "")

        print(f"\n  航班 [{idx+1}/{len(flights)}]:")
        print(f"    Flight ID: {flight_id}")
        print(f"    机尾号: {aircraft}")
        print(f"    起飞机场: {dep_airport}")
        print(f"    到达机场: {arr_airport} ({arr_name})")
        print(f"    起飞时间: {tko_time}")
        print(f"    降落时间: {des_time}")

        if not flight_id:
            print("  跳过: 缺少 Flight ID")
            continue

        # 从起飞时间提取实际飞行日期用于文件命名
        flight_date = tko_time[:10] if tko_time and len(tko_time) >= 10 else date_str
        base_prefix = f"{flight_id}_{flight_date}"
        # 同日多班次加序号，避免文件覆盖
        prefix_counter[base_prefix] = prefix_counter.get(base_prefix, 0) + 1
        if prefix_counter[base_prefix] > 1:
            prefix = f"{base_prefix}_{prefix_counter[base_prefix]}"
        else:
            prefix = base_prefix

        # Step 2: 获取计划轨迹
        print(f"\n  [2/4] 获取计划轨迹...")
        plan_date = tko_time_off or tko_time or ""
        plan_points = get_flight_plan_points(
            flight_id, aircraft, dep_airport, arr_airport, plan_date
        )
        if plan_points:
            save_csv(plan_points, f"{prefix}_plan_track.csv")
            profile_fields = ["name", "dist", "alt", "ful", "time", "lat", "lon"]
            save_csv(plan_points, f"{prefix}_plan_profile.csv", profile_fields)
        else:
            print("  未找到计划轨迹数据")

        # Step 3: 获取实际轨迹
        print(f"\n  [3/4] 获取实际轨迹...")
        actual_positions = get_flight_his_pos(flight_id, tko_time, des_time)
        if actual_positions:
            save_csv(actual_positions, f"{prefix}_actual_track.csv")
            profile_fields = [
                "gateway_time", "alt", "fob", "dis", "lat", "lon",
                "posPointName", "sAlt", "type"
            ]
            save_csv(actual_positions, f"{prefix}_actual_profile.csv", profile_fields)
        else:
            print("  未找到实际轨迹数据")

        # Step 4: 汇总
        print(f"\n  [4/4] 完成!")
        print(f"  输出文件:")
        found_any = False
        for f in sorted(os.listdir(".")):
            if f.startswith(prefix) and f.endswith(".csv"):
                size = os.path.getsize(f)
                print(f"    {f} ({size:,} bytes)")
                found_any = True
        if not found_any:
            print("    (无)")


def main():
    parser = argparse.ArgumentParser(
        description="航班轨迹数据抓取工具 - 从历史回放中提取计划/实际轨迹和剖面"
    )
    parser.add_argument(
        "--flight", "-f", required=True, help="航班号 (如 I99806)"
    )
    parser.add_argument(
        "--date", "-d", required=True, help="飞行日期 (如 2026-04-29)"
    )
    parser.add_argument(
        "--end-date", "-e", default=None, help="结束日期 (可选, 默认与开始日期相同)"
    )
    parser.add_argument(
        "--output", "-o", default=".", help="输出目录 (默认当前目录)"
    )

    args = parser.parse_args()

    if args.output != ".":
        os.makedirs(args.output, exist_ok=True)
        os.chdir(args.output)

    flight_number = args.flight.upper()
    date_str = args.date
    end_date = args.end_date

    try:
        process_flight(flight_number, date_str, end_date)
    except requests.RequestException as e:
        print(f"网络错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
