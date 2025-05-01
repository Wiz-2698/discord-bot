"""
This script redeems a gift code for players of the mobile game 
Whiteout Survival by using their API, with automatic CAPTCHA solving.
"""

import argparse
import hashlib
import json
import sys
import time
import os
import warnings
import requests
from requests.adapters import HTTPAdapter, Retry
import base64
import easyocr
import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from datetime import datetime, timedelta
from os.path import exists
import random

warnings.filterwarnings("ignore", message=".*pin_memory.*", category=UserWarning)

# 配置常量
LOGIN_URL = "https://wos-giftcode-api.centurygame.com/api/player"
CAPTCHA_URL = "https://wos-giftcode-api.centurygame.com/api/captcha"
REDEEM_URL = "https://wos-giftcode-api.centurygame.com/api/gift_code"
SALT = "tB87#kPtkxqOS2"
DELAY = 1
RETRY_DELAY = 2
MAX_RETRIES = 3
CAPTCHA_RETRIES = 4
CAPTCHA_SLEEP = 60
MAX_CAPTCHA_ATTEMPTS = 4
MIN_CONFIDENCE = 0.4

# 解析命令列參數
parser = argparse.ArgumentParser(description="兌換 Whiteout Survival 禮品碼，支援自動 CAPTCHA 識別")
parser.add_argument("-c", "--code", required=True, help="要兌換的禮品碼")
parser.add_argument("-f", "--player-file", dest="player_file", default="player.json", help="包含玩家 ID 的 JSON 檔案")
parser.add_argument("-r", "--results-file", dest="results_file", default="results.json", help="儲存兌換結果的 JSON 檔案")
parser.add_argument("--restart", action="store_true", help="為所有玩家重新兌換")
parser.add_argument("--all-images", action="store_true", help="儲存所有 CAPTCHA 圖片")
parser.add_argument("--use-gpu", type=int, nargs="?", const=0, default=None, 
                    help="啟用 GPU 並指定設備 ID（0 為 iGPU，1 為 dGPU 等）")
args = parser.parse_args()

# 設置日誌和 CAPTCHA 圖片儲存
script_dir = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(script_dir, "redeemed_codes.txt")
FAILED_CAPTCHA_DIR = os.path.join(script_dir, "failed_captchas")

os.makedirs(FAILED_CAPTCHA_DIR, exist_ok=True)

# 初始化 EasyOCR
if args.use_gpu is not None:
    try:
        import torch
        torch.cuda.set_device(args.use_gpu)
        gpu_name = torch.cuda.get_device_name(args.use_gpu)
        print(f"使用 GPU 設備 {args.use_gpu}：{gpu_name}")
        reader = easyocr.Reader(['en'], gpu=True)
    except Exception as e:
        print(f"GPU 錯誤：{e}，回退到 CPU")
        reader = easyocr.Reader(['en'], gpu=False)
else:
    print("僅使用 CPU（無 GPU 加速）")
    reader = easyocr.Reader(['en'], gpu=False)

# 計數器
counters = {
    "success": 0,
    "already_redeemed": 0,
    "errors": 0,
    "captcha_success": 0,
    "captcha_first_try": 0,
    "captcha_retry": 0,
    "captcha_attempts": 0,
    "captcha_failures": 0,
}
error_details = {}
script_start_time = time.time()

# 結果訊息映射
RESULT_MESSAGES = {
    "SUCCESS": "成功兌換",
    "RECEIVED": "已兌換",
    "SAME TYPE EXCHANGE": "成功兌換（相同類型）",
    "TIME ERROR": "禮品碼已過期",
    "TIMEOUT RETRY": "伺服器要求重試",
    "USED": "兌換次數已達上限",
    "Server requested retry": "伺服器要求重試",
}

def preprocess_captcha(image):
    """對 CAPTCHA 圖片進行多種預處理並返回處理後的圖片"""
    if isinstance(image, Image.Image):
        img_np = np.array(image)
        if len(img_np.shape) > 2 and img_np.shape[2] == 3:
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    else:
        img_np = image

    processed_images = [("Original", img_np)]

    if len(img_np.shape) > 2:
        gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
    else:
        gray = img_np

    _, thresh1 = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    processed_images.append(("Basic Threshold", thresh1))

    adaptive_thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    processed_images.append(("Adaptive Threshold", adaptive_thresh))

    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    processed_images.append(("Otsu Threshold", otsu))

    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    processed_images.append(("Denoised", denoised))

    _, denoised_thresh = cv2.threshold(denoised, 127, 255, cv2.THRESH_BINARY)
    processed_images.append(("Denoised+Threshold", denoised_thresh))

    kernel = np.ones((2,2), np.uint8)
    dilated = cv2.dilate(gray, kernel, iterations=1)
    processed_images.append(("Dilated", dilated))

    eroded = cv2.erode(gray, kernel, iterations=1)
    processed_images.append(("Eroded", eroded))

    edges = cv2.Canny(gray, 100, 200)
    processed_images.append(("Edges", edges))

    kernel = np.ones((1,1), np.uint8)
    opening = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
    processed_images.append(("Opening", opening))

    if isinstance(image, Image.Image):
        pil_img = image
    else:
        if len(img_np.shape) > 2 and img_np.shape[2] == 3:
            pil_img = Image.fromarray(cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB))
        else:
            pil_img = Image.fromarray(img_np)
    enhanced = ImageEnhance.Contrast(pil_img).enhance(2.0)
    enhanced_np = np.array(enhanced)
    if len(enhanced_np.shape) > 2 and enhanced_np.shape[2] == 3:
        enhanced_np = cv2.cvtColor(enhanced_np, cv2.COLOR_RGB2BGR)
    processed_images.append(("Enhanced Contrast", enhanced_np))

    sharpened = pil_img.filter(ImageFilter.SHARPEN)
    sharpened_np = np.array(sharpened)
    if len(sharpened_np.shape) > 2 and sharpened_np.shape[2] == 3:
        sharpened_np = cv2.cvtColor(sharpened_np, cv2.COLOR_RGB2BGR)
    processed_images.append(("Sharpened", sharpened_np))

    if len(img_np.shape) > 2 and img_np.shape[2] == 3:
        blue_channel = img_np[:, :, 0]
        _, blue_thresh = cv2.threshold(blue_channel, 127, 255, cv2.THRESH_BINARY)
        processed_images.append(("Blue Channel", blue_thresh))

        hsv = cv2.cvtColor(img_np, cv2.COLOR_BGR2HSV)
        lower_purple = np.array([100, 50, 50])
        upper_purple = np.array([170, 255, 255])
        purple_mask = cv2.inRange(hsv, lower_purple, upper_purple)
        processed_images.append(("Purple Filter", purple_mask))

        lower_green = np.array([40, 50, 50])
        upper_green = np.array([90, 255, 255])
        green_mask = cv2.inRange(hsv, lower_green, upper_green)
        processed_images.append(("Green Filter", green_mask))

    return processed_images

def save_captcha_image(img_np, fid, attempt, captcha_code):
    """儲存 CAPTCHA 圖片"""
    try:
        timestamp = int(time.time())
        image_filename = f"fid{fid}_try{attempt}_OCR_{captcha_code}_{timestamp}.png"
        full_path = os.path.join(FAILED_CAPTCHA_DIR, image_filename)
        if cv2.imwrite(full_path, img_np):
            log(f"儲存 CAPTCHA 圖片：{os.path.relpath(full_path, script_dir)}")
        else:
            log(f"無法儲存 CAPTCHA 圖片：{os.path.relpath(full_path, script_dir)}")
        return image_filename
    except Exception as e:
        log(f"儲存 CAPTCHA 圖片時發生異常：{e}")
        return None

def fetch_captcha_code(fid, retry_queue=None):
    """獲取並識別 CAPTCHA 代碼"""
    if retry_queue is None:
        retry_queue = {}
    attempts = 0
    current_time = time.time()
    first_attempt_success = False
    retry_needed = False

    while attempts < MAX_CAPTCHA_ATTEMPTS:
        counters["captcha_attempts"] += 1
        payload = encode_data({"fid": fid, "time": int(time.time() * 1000), "init": "0"})
        response = make_request(CAPTCHA_URL, payload)
        if response and response.status_code == 200:
            try:
                captcha_data = response.json()
                if captcha_data.get("code") == 1 and captcha_data.get("msg") == "CAPTCHA GET TOO FREQUENT.":
                    log("CAPTCHA 請求過於頻繁，加入重試隊列...")
                    retry_queue[fid] = current_time + CAPTCHA_SLEEP
                    return None, retry_queue
                if "data" in captcha_data and "img" in captcha_data["data"]:
                    img_field = captcha_data["data"]["img"]
                    img_base64 = img_field.split(",", 1)[1] if img_field.startswith("data:image") else img_field
                    img_bytes = base64.b64decode(img_base64)
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    img_np = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    processed_images = preprocess_captcha(img_np)

                    candidates = []
                    for method_name, processed_img in processed_images:
                        processed_bytes = cv2.imencode('.png', processed_img)[1].tobytes()
                        results = reader.readtext(processed_bytes, detail=1)
                        for result in results:
                            if len(result) >= 2:
                                text = result[1].strip().replace(' ', '')
                                confidence = result[2]
                                if text and confidence > MIN_CONFIDENCE:
                                    candidates.append((text, confidence, method_name))

                    candidates.sort(key=lambda x: x[1], reverse=True)
                    best_result = ""
                    for text, confidence, method_name in candidates:
                        if text.isalnum() and len(text) == 4:
                            best_result = text
                            log(f"找到有效 CAPTCHA，方法：{method_name}，信心值：{confidence:.2f}")
                            break

                    if not best_result:
                        log("OCR 未返回有效結果，請求新 CAPTCHA")
                        if attempts > 0:
                            retry_needed = True
                        attempts += 1
                        time.sleep(random.uniform(1.0, 3.0))
                        continue

                    if attempts == 0:
                        first_attempt_success = True
                    captcha_code = best_result

                    if args.all_images or not (captcha_code.isalnum() and len(captcha_code) == 4):
                        save_captcha_image(img_np, fid, attempts, captcha_code)

                    if captcha_code.isalnum() and len(captcha_code) == 4:
                        log(f"辨識到 CAPTCHA：{captcha_code}")
                        counters["captcha_success"] += 1
                        if first_attempt_success:
                            counters["captcha_first_try"] += 1
                        if attempts > 0:
                            counters["captcha_retry"] += attempts
                        return captcha_code, retry_queue
                    else:
                        log(f"無效 CAPTCHA 格式：'{captcha_code}'，重新獲取...")
                else:
                    log("回應中缺少 CAPTCHA 圖片，重新獲取...")
            except Exception as e:
                failed_path = os.path.join(FAILED_CAPTCHA_DIR, f"fid{fid}_exception_{int(time.time())}.png")
                with open(failed_path, "wb") as f:
                    f.write(img_bytes)
                log(f"儲存失敗的 CAPTCHA 圖片到 {os.path.relpath(failed_path, script_dir)}")
                log(f"解決 CAPTCHA 時發生錯誤：{e}")
        else:
            log("無法獲取 CAPTCHA，重試中...")

        if attempts > 0:
            retry_needed = True
        attempts += 1
        time.sleep(random.uniform(1.0, 3.0))

    log(f"經過 {attempts} 次嘗試仍無法獲取有效 CAPTCHA，加入重試隊列")
    counters["captcha_failures"] += 1
    retry_queue[fid] = current_time + CAPTCHA_SLEEP
    return None, retry_queue

def log(message):
    """記錄日誌到檔案和終端"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - {message}"
    try:
        print(log_entry)
    except UnicodeEncodeError:
        cleaned = log_entry.encode('utf-8', errors='replace').decode('ascii', errors='replace')
        print(cleaned)
    try:
        with open(LOG_FILE, "a", encoding="utf-8-sig") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"{timestamp} - 日誌記錄錯誤：{e}")

def encode_data(data):
    """編碼數據並生成簽名"""
    sorted_keys = sorted(data.keys())
    encoded_data = "&".join([f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}" for key in sorted_keys])
    return {"sign": hashlib.md5(f"{encoded_data}{SALT}".encode()).hexdigest(), **data}

def make_request(url, payload, headers=None):
    """執行 HTTP 請求，支援重試"""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response
            log(f"嘗試 {attempt+1} 失敗：HTTP {response.status_code}，回應：{response.text[:200]}")
        except requests.exceptions.RequestException as e:
            log(f"嘗試 {attempt+1} 失敗：{e}")
        time.sleep(RETRY_DELAY)
    return None

def redeem_gift_code(fid, cdk, retry_queue=None):
    """兌換禮品碼，支援 CAPTCHA"""
    if retry_queue is None:
        retry_queue = {}
    if not str(fid).strip().isdigit():
        log(f"跳過無效 FID：'{fid}'")
        return {"msg": "無效 FID 格式"}, retry_queue
    fid = str(fid).strip()

    current_time = time.time()
    if fid in retry_queue and retry_queue[fid] > current_time:
        cooldown_remaining = int(retry_queue[fid] - current_time)
        log(f"FID {fid} 處於冷卻期，剩餘 {cooldown_remaining} 秒")
        return {"msg": "冷卻中"}, retry_queue

    try:
        login_payload = encode_data({"fid": fid, "time": int(time.time() * 1000)})
        login_resp = make_request(LOGIN_URL, login_payload)
        if not login_resp:
            return {"msg": "登入請求失敗"}, retry_queue

        login_data = login_resp.json()
        if login_data.get("code") != 0:
            login_msg = login_data.get('msg', '未知登入錯誤')
            log(f"FID {fid} 登入失敗：{login_data.get('code')}，{login_msg}")
            return {"msg": f"登入失敗：{login_msg}"}, retry_queue

        for attempt in range(CAPTCHA_RETRIES):
            try:
                captcha_code, retry_queue = fetch_captcha_code(fid, retry_queue)
                if captcha_code is None:
                    return {"msg": "因 CAPTCHA 頻率限制加入重試隊列"}, retry_queue

                redeem_payload = encode_data({
                    "fid": fid,
                    "cdk": cdk,
                    "captcha_code": captcha_code,
                    "time": int(time.time() * 1000)
                })
                redeem_resp = make_request(REDEEM_URL, redeem_payload)
                if not redeem_resp:
                    return {"msg": "兌換請求失敗"}, retry_queue

                redeem_data = redeem_resp.json()
                msg = redeem_data.get('msg', '未知錯誤').strip('.')

                if msg in ["CAPTCHA CHECK ERROR", "Sign Error", "Server requested retry"]:
                    log(f"CAPTCHA 嘗試失敗，重試中...（第 {attempt+1}/{CAPTCHA_RETRIES} 次）")
                    time.sleep(random.uniform(2.5, 4))
                    continue
                elif msg == "CAPTCHA CHECK TOO FREQUENT":
                    log(f"CAPTCHA 檢查過於頻繁，加入重試隊列，等待 {CAPTCHA_SLEEP} 秒...")
                    retry_queue[fid] = current_time + CAPTCHA_SLEEP
                    return {"msg": "加入重試隊列"}, retry_queue
                elif msg == "NOT LOGIN":
                    log(f"FID {fid} 在 CAPTCHA 後會話過期或無效，跳過")
                    return {"msg": "CAPTCHA 後會話過期"}, retry_queue
                else:
                    return redeem_data, retry_queue
            except Exception as e:
                log(f"CAPTCHA 嘗試 {attempt+1} 時發生錯誤：{e}")
                if attempt == CAPTCHA_RETRIES - 1:
                    log(f"FID {fid} 達到最大 CAPTCHA 重試次數，加入重試隊列")
                    retry_queue[fid] = current_time + CAPTCHA_SLEEP
                    return {"msg": "達到最大重試次數，加入重試隊列"}, retry_queue

        retry_queue[fid] = current_time + CAPTCHA_SLEEP
        return {"msg": "加入重試隊列"}, retry_queue
    except Exception as e:
        log(f"FID {fid} 兌換時發生意外錯誤：{e}")
        return {"msg": f"意外錯誤：{e}"}, retry_queue

def print_summary():
    """列印兌換總結"""
    script_end_time = time.time()
    total_seconds = script_end_time - script_start_time
    execution_time = str(timedelta(seconds=int(total_seconds)))

    summary = (
        "\n=== 兌換完成 ===\n"
        f"成功兌換：{counters['success']}\n"
        f"已兌換：{counters['already_redeemed']}\n"
        f"錯誤/失敗：{counters['errors']}\n"
    )

    if error_details:
        summary += "\n=== 錯誤詳情 ===\n以下 ID 發生錯誤：\n"
        for fid, error_msg in error_details.items():
            summary += f"FID {fid}：{error_msg}\n"

    summary += (
        "\n=== CAPTCHA 統計 ===\n"
        f"總嘗試次數：{counters['captcha_attempts']}\n"
        f"成功解碼：{counters['captcha_success']}\n"
        f"首次成功：{counters['captcha_first_try']}\n"
        f"重試次數：{counters['captcha_retry']}\n"
        f"完全失敗：{counters['captcha_failures']}\n"
    )

    success_rate = (counters['captcha_success'] / counters['captcha_attempts'] * 100) if counters['captcha_attempts'] > 0 else 0
    first_try_rate = (counters['captcha_first_try'] / counters['captcha_success'] * 100) if counters['captcha_success'] > 0 else 0
    avg_attempts = (counters['captcha_attempts'] / counters['captcha_success']) if counters['captcha_success'] > 0 else 0

    summary += (
        f"成功率：{success_rate:.2f}%\n"
        f"首次成功率：{first_try_rate:.2f}%\n"
        f"平均成功所需嘗試次數：{avg_attempts:.2f}\n"
        f"總執行時間：{execution_time}\n"
    )

    log(summary)
    return summary

if __name__ == "__main__":
    log(f"\n=== 開始兌換禮品碼：{args.code}，時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # 讀取玩家數據
    try:
        with open(args.player_file, encoding="utf-8") as player_file:
            players = json.loads(player_file.read())
    except FileNotFoundError:
        log(f"錯誤：找不到玩家檔案 '{args.player_file}'")
        sys.exit(1)
    except Exception as e:
        log(f"讀取玩家檔案時發生錯誤：{e}")
        sys.exit(1)

    # 讀取或初始化結果
    results = []
    if exists(args.results_file):
        try:
            with open(args.results_file, encoding="utf-8") as results_file:
                results = json.loads(results_file.read())
        except Exception as e:
            log(f"讀取結果檔案時發生錯誤：{e}")

    found_item = next((result for result in results if result["code"] == args.code), None)
    if found_item is None:
        log(f"新的禮品碼：{args.code}，加入結果檔案並處理")
        new_item = {"code": args.code, "status": {}}
        results.append(new_item)
        result = new_item
    else:
        result = found_item

    # 設置 HTTP 會話
    session = requests.Session()
    retry_config = Retry(total=5, backoff_factor=1, status_forcelist=[429], allowed_methods=False)
    session.mount("https://", HTTPAdapter(max_retries=retry_config))

    retry_queue = {}
    processed_fids = set()
    retry_attempts = {}
    MAX_RETRY_ATTEMPTS = 5
    i = 0

    while len(processed_fids) < len(players):
        current_time = time.time()
        initial_processed_count = len(processed_fids)

        for player in players:
            fid = player["id"]
            if fid in processed_fids:
                continue
            if result["status"].get(fid) == "Successful" and not args.restart:
                counters["already_redeemed"] += 1
                processed_fids.add(fid)
                continue
            if fid in retry_queue and retry_queue[fid] > current_time:
                continue

            i += 1
            log(f"處理玩家 {player['original_name']} ({fid}) [{i}/{len(players)}]")

            if fid not in retry_attempts:
                retry_attempts[fid] = 0

            result_data, retry_queue = redeem_gift_code(fid, args.code, retry_queue)
            raw_msg = result_data.get('msg', '未知錯誤').strip('.')
            friendly_msg = RESULT_MESSAGES.get(raw_msg, raw_msg)

            if raw_msg in ["加入重試隊列", "達到最大重試次數，加入重試隊列", 
                           "冷卻中", "因 CAPTCHA 頻率限制加入重試隊列"]:
                retry_attempts[fid] += 1
                if retry_attempts[fid] >= MAX_RETRY_ATTEMPTS:
                    log(f"FID {fid} 在 {MAX_RETRY_ATTEMPTS} 次重試後失敗，標記為錯誤")
                    processed_fids.add(fid)
                    counters["errors"] += 1
                    error_details[fid] = f"在 {MAX_RETRY_ATTEMPTS} 次重試後失敗"
                    if fid in retry_queue:
                        del retry_queue[fid]
                continue

            processed_fids.add(fid)
            if raw_msg == "TIME ERROR":
                log("禮品碼已過期！腳本即將退出")
                print_summary()
                sys.exit(1)
            elif raw_msg == "USED":
                log("兌換次數已達上限！腳本即將退出")
                print_summary()
                sys.exit(1)
            elif raw_msg in ["SUCCESS", "SAME TYPE EXCHANGE"]:
                counters["success"] += 1
                result["status"][fid] = "Successful"
            elif raw_msg == "RECEIVED":
                counters["already_redeemed"] += 1
                result["status"][fid] = "Successful"
            elif raw_msg in ["TIMEOUT RETRY", "Server requested retry"]:
                result["status"][fid] = "Unsuccessful"
            else:
                counters["errors"] += 1
                error_details[fid] = friendly_msg
                result["status"][fid] = "Unsuccessful"

            log(f"結果：{friendly_msg}")
            time.sleep(DELAY)

        waiting_fids = [f for f, t in retry_queue.items() if t > current_time and f not in processed_fids]
        if waiting_fids:
            next_retry_time = min([t for f, t in retry_queue.items() if f in waiting_fids])
            wait_time = max(1, min(5, next_retry_time - current_time))
            log(f"{len(waiting_fids)} 個 FID 處於冷卻期，下一重試將在約 {int(wait_time)} 秒後進行")
            time.sleep(wait_time)
        elif len(processed_fids) < len(players):
            time.sleep(5)

    # 儲存結果
    try:
        with open(args.results_file, "w", encoding="utf-8") as fp:
            json.dump(results, fp)
    except Exception as e:
        log(f"寫入結果檔案時發生錯誤：{e}")

    print_summary()
