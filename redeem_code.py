"""
This script redeems a gift code for players of the mobile game
Whiteout Survival by using their API.
Modified to auto-retry failed attempts.
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

# Handle arguments the script is called with
parser = argparse.ArgumentParser()
parser.add_argument("-c", "--code", required=True)
parser.add_argument("-f", "--player-file", dest="player_file", default="player.json")
parser.add_argument("-r", "--results-file", dest="results_file", default="results.json")
parser.add_argument("--restart", dest="restart", action="store_true")
args = parser.parse_args()

# Open and read the user files
with open(args.player_file, encoding="utf-8") as player_file:
    players = json.loads(player_file.read())

# Initalize results to not error if no results file exists yet
results = []

# If a results file exists, load it
if exists(args.results_file):
    with open(args.results_file, encoding="utf-8") as results_file:
        results = json.loads(results_file.read())

# Retrieve the result set if it exists or create an empty one
found_item = next((result for result in results if result["code"] == args.code), None)

if found_item is None:
    print("New code: " + args.code + " adding to results file and processing.")
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

# OCR Initialization
ocr = ddddocr.DdddOcr(show_ad=False)

# Requests Session Setup
r = requests.Session()
retry_config = Retry(
    total=5, backoff_factor=1, status_forcelist=[429], allowed_methods=False
)
r.mount("https://", HTTPAdapter(max_retries=retry_config))


def analyze_captcha_image_and_change_2_text(base64_string):
    if "," in base64_string:
        base64_string = base64_string.split(",")[1]
    img_data = base64.b64decode(base64_string)
    res = ocr.classification(img_data)
    return res.upper()

# --- NEW: Retry Logic Variables ---
MAX_RETRIES = 15  # 最大重試輪數
retry_count = 0
global_counter_successfully_claimed = 0
global_counter_already_claimed = 0

print(f"Target Code: {args.code} | Total Players: {len(players)}")

while retry_count < MAX_RETRIES:
    errors_this_round = 0
    i = 0
    
    # 計算還剩多少人未成功
    pending_players = [p for p in players if result["status"].get(p["id"]) != "Successful" or args.restart]
    if not pending_players:
        print("\nAll players have successfully claimed the code!")
        break

    if retry_count > 0:
        print(f"\n--- Retry Round {retry_count} --- (Remaining: {len(pending_players)})")

    for player in players:
        i += 1
        
        # Check status
        status = result["status"].get(player["id"])
        if status == "Successful" and not args.restart:
            # 已經成功的直接計數跳過，不印 log 以免洗版
            continue

        print(
            f"\x1b[K[{i}/{len(players)}] Redeeming for {player['original_name']}...",
            end="\r",
            flush=True,
        )

        # Time logic
        timestamp = time.time_ns()
        request_data = {"fid": player["id"], "time": timestamp}
        request_data["sign"] = hashlib.md5(
            ("fid=" + request_data["fid"] + "&time=" + str(request_data["time"]) + SALT).encode("utf-8")
        ).hexdigest()

        # 1. Login
        try:
            login_request = r.post(URL + "/player", data=request_data, headers=HTTP_HEADER, timeout=10)
            login_response = login_request.json()
        except Exception as e:
            print(f"\nLogin Connection Error for {player['original_name']}: {e}")
            errors_this_round += 1
            continue

        if login_response["msg"] != "success":
            print(f"\nLogin failed for {player['original_name']} (ID: {player['id']}). Skipping.")
            # 登入失敗通常是 ID 錯，計入錯誤但不一定能透過重試解決，這裡仍計入 error
            errors_this_round += 1
            continue

        # 2. Captcha
        captcha_data = {"fid": player["id"], "time": timestamp, "init": "0"}
        captcha_data["sign"] = hashlib.md5(
            ("fid=" + captcha_data["fid"] + "&init=" + captcha_data["init"] + "&time=" + str(captcha_data["time"]) + SALT).encode("utf-8")
        ).hexdigest()

        try:
            captcha_request = r.post(URL + "/captcha", data=captcha_data, headers=HTTP_HEADER, timeout=10)
            captcha_response = captcha_request.json()
        except Exception as e:
            print(f"\nCaptcha Connection Error for {player['original_name']}")
            errors_this_round += 1
            continue

        if captcha_response["msg"] != "SUCCESS":
            print(f"\nCaptcha fetch failed for {player['original_name']}")
            errors_this_round += 1
            continue

        captcha_img = captcha_response["data"]["img"]

        # 3. Redeem
        request_data["captcha_code"] = analyze_captcha_image_and_change_2_text(captcha_img)
        request_data["cdk"] = args.code
        request_data["time"] = str(time.time_ns())
        request_data["sign"] = hashlib.md5(
            ("captcha_code=" + request_data["captcha_code"] + "&cdk=" + request_data["cdk"] + "&fid=" + request_data["fid"] + "&time=" + str(request_data["time"]) + SALT).encode("utf-8")
        ).hexdigest()

        try:
            redeem_request = r.post(URL + "/gift_code", data=request_data, headers=HTTP_HEADER, timeout=10)
            redeem_response = redeem_request.json()
        except Exception as e:
            print(f"\nRedeem Connection Error for {player['original_name']}")
            errors_this_round += 1
            continue

        # Handle Responses
        err_code = redeem_response.get("err_code")
        
        if err_code == 40014: # Code doesn't exist
            print(f"\nThe gift code {args.code} doesn't exist!")
            sys.exit(1)
        elif err_code == 40007: # Expired
            print(f"\nThe gift code {args.code} is expired!")
            sys.exit(1)
        elif err_code == 40008: # Already claimed
            result["status"][player["id"]] = "Successful"
        elif err_code == 20000: # Success
            result["status"][player["id"]] = "Successful"
            print(f"\nSUCCESS: {player['original_name']}")
        else:
            # 40004 (Timeout), 40101 (Freq), 40103 (Captcha Error) -> Retryable
            result["status"][player["id"]] = "Unsuccessful"
            # 不要印出每一行錯誤，保持畫面整潔，除非是最後一輪
            if retry_count == MAX_RETRIES - 1:
                print(f"\nFailed for {player['original_name']}: {redeem_response}")
            errors_this_round += 1

    # Save progress after each round
    with open(args.results_file, "w", encoding="utf-8") as fp:
        json.dump(results, fp)

    # Check if we need to loop again
    if errors_this_round == 0:
        break
    else:
        retry_count += 1
        if retry_count < MAX_RETRIES:
            wait_time = 3 + (retry_count * 1) # 漸進式等待：3秒, 4秒, 5秒...
            print(f"\nRound finished with {errors_this_round} errors. Retrying in {wait_time}s...")
            time.sleep(wait_time)
        else:
            print(f"\nMax retries reached. Stopping with {errors_this_round} errors remaining.")

# Final Stats Calculation
final_success = sum(1 for p in players if result["status"].get(p["id"]) == "Successful")
final_errors = len(players) - final_success

print(
    f"\n\n=== FINAL RESULTS ===\n"
    f"Total Players: {len(players)}\n"
    f"Successful: {final_success}\n"
    f"Failed: {final_errors}"
)