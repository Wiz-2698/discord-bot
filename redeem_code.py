"""
This script redeems a gift code for players of the mobile game
Whiteout Survival by using their API.
Modified to be "Quiet" for Discord Bots (prevents disconnects).
"""

import argparse
import hashlib
import json
import sys
import time
import base64
import ddddocr
from os.path import exists

import requests
from requests.adapters import HTTPAdapter, Retry

# Handle arguments
parser = argparse.ArgumentParser()
parser.add_argument("-c", "--code", required=True)
parser.add_argument("-f", "--player-file", dest="player_file", default="player.json")
parser.add_argument("-r", "--results-file", dest="results_file", default="results.json")
parser.add_argument("--restart", dest="restart", action="store_true")
args = parser.parse_args()

# Load players
with open(args.player_file, encoding="utf-8") as player_file:
    players = json.loads(player_file.read())

results = []
if exists(args.results_file):
    with open(args.results_file, encoding="utf-8") as results_file:
        results = json.loads(results_file.read())

found_item = next((result for result in results if result["code"] == args.code), None)

if found_item is None:
    # 這裡只印簡單的一行
    print(f"Start Processing Code: {args.code}") 
    new_item = {"code": args.code, "status": {}}
    results.append(new_item)
    result = new_item
else:
    result = found_item

URL = "https://wos-giftcode-api.centurygame.com/api"
SALT = "tB87#kPtkxqOS2"
HTTP_HEADER = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}

r = requests.Session()
retry_config = Retry(
    total=5, backoff_factor=1, status_forcelist=[429], allowed_methods=False
)
r.mount("https://", HTTPAdapter(max_retries=retry_config))

def analyze_captcha_image_and_change_2_text(base64_string):
    if "," in base64_string:
        base64_string = base64_string.split(",")[1]
    img_data = base64.b64decode(base64_string)
    ocr = ddddocr.DdddOcr(show_ad=False)
    res = ocr.classification(img_data)
    return res.upper()

# --- RETRY LOGIC ---
MAX_RETRIES = 20
retry_count = 0

# 為了防止機器人斷線，我們移除單個玩家的進度條顯示，只顯示回合進度
print(f"Target: {args.code} | Players: {len(players)} | Max Retries: {MAX_RETRIES}")
sys.stdout.flush() # 強制發送訊息給機器人

while retry_count < MAX_RETRIES:
    errors_this_round = 0
    success_this_round = 0
    
    pending_players = [p for p in players if result["status"].get(p["id"]) != "Successful" or args.restart]
    
    if not pending_players:
        break # 全部成功，直接結束

    # 只印出回合開始，不印出每一個玩家
    if retry_count > 0:
        print(f"--- Retry Round {retry_count} (Left: {len(pending_players)}) ---")
        sys.stdout.flush()

    for player in players:
        # Check status
        status = result["status"].get(player["id"])
        if status == "Successful" and not args.restart:
            continue

        # --- 核心邏輯開始 (完全靜默執行，不 print) ---
        timestamp = time.time_ns()
        request_data = {"fid": player["id"], "time": timestamp}
        request_data["sign"] = hashlib.md5(
            ("fid=" + request_data["fid"] + "&time=" + str(request_data["time"]) + SALT).encode("utf-8")
        ).hexdigest()

        # 1. Login
        try:
            login_req = r.post(URL + "/player", data=request_data, headers=HTTP_HEADER, timeout=30)
            login_resp = login_req.json()
            if login_resp["msg"] != "success":
                errors_this_round += 1
                continue
        except:
            errors_this_round += 1
            continue

        # 2. Captcha
        captcha_data = {"fid": player["id"], "time": timestamp, "init": "0"}
        captcha_data["sign"] = hashlib.md5(
            ("fid=" + captcha_data["fid"] + "&init=" + captcha_data["init"] + "&time=" + str(captcha_data["time"]) + SALT).encode("utf-8")
        ).hexdigest()

        try:
            cap_req = r.post(URL + "/captcha", data=captcha_data, headers=HTTP_HEADER, timeout=30)
            cap_resp = cap_req.json()
            if cap_resp["msg"] != "SUCCESS":
                errors_this_round += 1
                continue
            captcha_img = cap_resp["data"]["img"]
        except:
            errors_this_round += 1
            continue

        # 3. Redeem
        try:
            request_data["captcha_code"] = analyze_captcha_image_and_change_2_text(captcha_img)
        except:
            errors_this_round += 1
            continue

        request_data["cdk"] = args.code
        request_data["time"] = str(time.time_ns())
        request_data["sign"] = hashlib.md5(
            ("captcha_code=" + request_data["captcha_code"] + "&cdk=" + request_data["cdk"] + "&fid=" + request_data["fid"] + "&time=" + str(request_data["time"]) + SALT).encode("utf-8")
        ).hexdigest()

        try:
            redeem_req = r.post(URL + "/gift_code", data=request_data, headers=HTTP_HEADER, timeout=30)
            redeem_resp = redeem_req.json()
        except:
            errors_this_round += 1
            continue

        # Check Result
        err_code = redeem_resp.get("err_code")
        
        if err_code == 40014: # 不存在
            print(f"Error: Code {args.code} invalid.")
            sys.exit(1)
        elif err_code == 40007: # 過期
            print(f"Error: Code {args.code} expired.")
            sys.exit(1)
        elif err_code == 40008: # 已領過
            result["status"][player["id"]] = "Successful"
            success_this_round += 1
        elif err_code == 20000: # 成功
            result["status"][player["id"]] = "Successful"
            success_this_round += 1
        else:
            result["status"][player["id"]] = "Unsuccessful"
            errors_this_round += 1
        # --- 核心邏輯結束 ---

    # Save
    with open(args.results_file, "w", encoding="utf-8") as fp:
        json.dump(results, fp)

    # Round Summary (這裡才印出東西)
    if errors_this_round == 0:
        break
    else:
        retry_count += 1
        if retry_count < MAX_RETRIES:
            print(f"Round Result -> Success: {success_this_round}, Failed: {errors_this_round}. Retrying in 2s...")
            sys.stdout.flush() # 確保訊息送出
            time.sleep(2)
        else:
            print(f"Stopping. Remaining Failures: {errors_this_round}")

# Final Output
final_success = sum(1 for p in players if result["status"].get(p["id"]) == "Successful")
final_errors = len(players) - final_success

print(f"\n=== FINAL: Success {final_success} / Failed {final_errors} ===")