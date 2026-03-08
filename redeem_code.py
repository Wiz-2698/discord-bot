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
from os.path import exists
import requests
from requests.adapters import HTTPAdapter, Retry

# 1. 效能優化：將 OCR 模組移至全域，啟動時僅載入一次，節省伺服器記憶體
import ddddocr
print("正在初始化 OCR 辨識引擎...")
sys.stdout.flush()
ocr = ddddocr.DdddOcr(show_ad=False)

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
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
    # 直接呼叫全域 ocr 變數，不再重複載入模型
    res = ocr.classification(img_data)
    return res.upper()

# --- RETRY LOGIC ---
MAX_RETRIES = 20
retry_count = 0

print(f"Target: {args.code} | Players: {len(players)} | Max Retries: {MAX_RETRIES}")
sys.stdout.flush()

while retry_count < MAX_RETRIES:
    errors_this_round = 0
    success_this_round = 0
    
    pending_players = [p for p in players if result["status"].get(p["id"]) != "Successful" or args.restart]
    
    if not pending_players:
        break # 全部成功，直接結束

    if retry_count > 0:
        print(f"--- Retry Round {retry_count} (Left: {len(pending_players)}) ---")
        sys.stdout.flush()

    for player in players:
        status = result["status"].get(player["id"])
        if status == "Successful" and not args.restart:
            continue

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
                # 2. 增加具體的錯誤輸出，方便日後除錯
                print(f"[警告] 玩家 {player['id']} 登入失敗: {login_resp}")
                errors_this_round += 1
                continue
        except Exception as e:
            error_msg = login_req.text[:200] if 'login_req' in locals() else str(e)
            print(f"[錯誤] 玩家 {player['id']} 登入連線異常。伺服器真實回傳: {error_msg}")
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
                print(f"[警告] 玩家 {player['id']} 獲取驗證碼失敗: {cap_resp}")
                errors_this_round += 1
                continue
            captcha_img = cap_resp["data"]["img"]
        except Exception as e:
            print(f"[錯誤] 玩家 {player['id']} 獲取驗證碼連線異常: {e}")
            errors_this_round += 1
            continue

        # 3. Redeem
        try:
            request_data["captcha_code"] = analyze_captcha_image_and_change_2_text(captcha_img)
        except Exception as e:
            print(f"[錯誤] 玩家 {player['id']} 驗證碼辨識失敗: {e}")
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
        except Exception as e:
            print(f"[錯誤] 玩家 {player['id']} 兌換連線異常: {e}")
            errors_this_round += 1
            continue

        # Check Result
        err_code = redeem_resp.get("err_code")
        
        if err_code == 40014:
            print(f"Error: Code {args.code} invalid.")
            sys.exit(1)
        elif err_code == 40007:
            print(f"Error: Code {args.code} expired.")
            sys.exit(1)
        elif err_code == 40008:
            result["status"][player["id"]] = "Successful"
            success_this_round += 1
        elif err_code == 20000:
            result["status"][player["id"]] = "Successful"
            success_this_round += 1
        else:
            print(f"[警告] 玩家 {player['id']} 兌換失敗代碼: {err_code}, 訊息: {redeem_resp}")
            result["status"][player["id"]] = "Unsuccessful"
            errors_this_round += 1

    # Save
    with open(args.results_file, "w", encoding="utf-8") as fp:
        json.dump(results, fp)

    # Round Summary
    if errors_this_round == 0:
        break
    else:
        retry_count += 1
        if retry_count < MAX_RETRIES:
            print(f"Round Result -> Success: {success_this_round}, Failed: {errors_this_round}. Retrying in 2s...")
            sys.stdout.flush() 
            time.sleep(2)
        else:
            print(f"Stopping. Remaining Failures: {errors_this_round}")

# Final Output
final_success = sum(1 for p in players if result["status"].get(p["id"]) == "Successful")
final_errors = len(players) - final_success

print(f"\n=== FINAL: Success {final_success} / Failed {final_errors} ===")
