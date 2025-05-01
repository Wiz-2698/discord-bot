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

warnings.filterwarnings("ignore", message=".*pin_memory.*", category=UserWarning)

# Configuration
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

# Argument parsing
parser = argparse.ArgumentParser(description="Redeem gift codes with automatic CAPTCHA solving")
parser.add_argument("-c", "--code", required=True, help="Gift code to redeem")
parser.add_argument("-f", "--player-file", dest="player_file", default="player.json", help="JSON file with player IDs")
parser.add_argument("-r", "--results-file", dest="results_file", default="results.json", help="JSON file to store results")
parser.add_argument("--restart", dest="restart", action="store_true", help="Restart redemption for all players")
parser.add_argument("--all-images", action="store_true", help="Save all CAPTCHA images")
parser.add_argument("--use-gpu", type=int, nargs="?", const=0, default=None, 
                    help="Enable GPU and specify device ID (0 for iGPU, 1 for dGPU, etc.)")
args = parser.parse_args()

# Setup directories and logging
script_dir = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(script_dir, "redeemed_codes.txt")
FAILED_CAPTCHA_DIR = os.path.join(script_dir, "failed_captchas")

os.makedirs(FAILED_CAPTCHA_DIR, exist_ok=True)

# Initialize OCR reader
if args.use_gpu is not None:
    try:
        import torch
        torch.cuda.set_device(args.use_gpu)
        gpu_name = torch.cuda.get_device_name(args.use_gpu)
        print(f"Using GPU device {args.use_gpu}: {gpu_name}")
        reader = easyocr.Reader(['en'], gpu=True)
    except Exception as e:
        print(f"GPU error: {e}. Falling back to CPU.")
        reader = easyocr.Reader(['en'], gpu=False)
else:
    print("Using CPU only (no GPU acceleration)")
    reader = easyocr.Reader(['en'], gpu=False)

# Counters for statistics
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

# Result messages mapping
RESULT_MESSAGES = {
    "SUCCESS": "Successfully redeemed",
    "RECEIVED": "Already redeemed",
    "SAME TYPE EXCHANGE": "Successfully redeemed (same type)",
    "TIME ERROR": "Code has expired",
    "TIMEOUT RETRY": "Server requested retry",
    "USED": "Claim limit reached, unable to claim",
    "Server requested retry": "Server requested retry",
}

def preprocess_captcha(image):
    """Apply multiple preprocessing techniques and return processed images"""
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

    # Basic threshold
    _, thresh1 = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    processed_images.append(("Basic Threshold", thresh1))

    # Adaptive threshold
    adaptive_thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    processed_images.append(("Adaptive Threshold", adaptive_thresh))

    # Otsu's thresholding
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    processed_images.append(("Otsu Threshold", otsu))

    # Noise removal
    denoised = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    processed_images.append(("Denoised", denoised))

    # Noise removal + threshold
    _, denoised_thresh = cv2.threshold(denoised, 127, 255, cv2.THRESH_BINARY)
    processed_images.append(("Denoised+Threshold", denoised_thresh))

    # Dilated
    kernel = np.ones((2,2), np.uint8)
    dilated = cv2.dilate(gray, kernel, iterations=1)
    processed_images.append(("Dilated", dilated))

    # Eroded
    eroded = cv2.erode(gray, kernel, iterations=1)
    processed_images.append(("Eroded", eroded))

    # Edge enhancement
    edges = cv2.Canny(gray, 100, 200)
    processed_images.append(("Edges", edges))

    # Morphological operations
    kernel = np.ones((1,1), np.uint8)
    opening = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
    processed_images.append(("Opening", opening))

    # Enhanced contrast
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

    # Sharpened
    sharpened = pil_img.filter(ImageFilter.SHARPEN)
    sharpened_np = np.array(sharpened)
    if len(sharpened_np.shape) > 2 and sharpened_np.shape[2] == 3:
        sharpened_np = cv2.cvtColor(sharpened_np, cv2.COLOR_RGB2BGR)
    processed_images.append(("Sharpened", sharpened_np))

    # Color filtering
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
    try:
        timestamp = int(time.time())
        image_filename = f"fid{fid}_try{attempt}_OCR_{captcha_code}_{timestamp}.png"
        full_path = os.path.join(FAILED_CAPTCHA_DIR, image_filename)
        if cv2.imwrite(full_path, img_np):
            rel_path = os.path.relpath(full_path, script_dir)
            log(f"Saved CAPTCHA image: {rel_path}")
        else:
            log(f"Failed to save CAPTCHA image: {rel_path}")
        return image_filename
    except Exception as e:
        log(f"Exception during saving CAPTCHA image: {e}")
        return None

def fetch_captcha_code(fid, retry_queue=None):
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
                    log("CAPTCHA fetch too frequent, adding to retry queue...")
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
                            log(f"Found valid CAPTCHA with method: {method_name}, confidence: {confidence:.2f}")
                            break

                    if not best_result:
                        log("OCR did not return a valid result, requesting new CAPTCHA")
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
                        log(f"Recognized CAPTCHA: {captcha_code}")
                        counters["captcha_success"] += 1
                        if first_attempt_success:
                            counters["captcha_first_try"] += 1
                        if attempts > 0:
                            counters["captcha_retry"] += attempts
                        return captcha_code, retry_queue
                    else:
                        log(f"Invalid CAPTCHA format: '{captcha_code}', refetching...")
                else:
                    log("CAPTCHA image missing in response, refetching...")
            except Exception as e:
                failed_path = os.path.join(FAILED_CAPTCHA_DIR, f"fid{fid}_exception_{int(time.time())}.png")
                with open(failed_path, "wb") as f:
                    f.write(img_bytes)
                log(f"Saved failed CAPTCHA image to {os.path.relpath(failed_path, script_dir)}")
                log(f"Error solving CAPTCHA: {e}")
        else:
            log("Failed to fetch CAPTCHA, retrying...")

        if attempts > 0:
            retry_needed = True
        attempts += 1
        time.sleep(random.uniform(1.0, 3.0))

    log(f"Failed to fetch valid CAPTCHA after {attempts} attempts, adding to retry queue")
    counters["captcha_failures"] += 1
    retry_queue[fid] = current_time + CAPTCHA_SLEEP
    return None, retry_queue

def log(message):
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
        print(f"{timestamp} - LOGGING ERROR: {e}")

def encode_data(data):
    sorted_keys = sorted(data.keys())
    encoded_data = "&".join([f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}" for key in sorted_keys])
    return {"sign": hashlib.md5(f"{encoded_data}{SALT}".encode()).hexdigest(), **data}

def make_request(url, payload, headers=None):
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                return response
            log(f"Attempt {attempt+1} failed: HTTP {response.status_code}, Response: {response.text[:200]}")
        except requests.exceptions.RequestException as e:
            log(f"Attempt {attempt+1} failed: {e}")
        time.sleep(RETRY_DELAY)
    return None

def redeem_gift_code(fid, cdk, retry_queue=None):
    if retry_queue is None:
        retry_queue = {}
    if not str(fid).strip().isdigit():
        log(f"Skipping invalid FID: '{fid}'")
        return {"msg": "Invalid FID format"}, retry_queue
    fid = str(fid).strip()

    current_time = time.time()
    if fid in retry_queue and retry_queue[fid] > current_time:
        cooldown_remaining = int(retry_queue[fid] - current_time)
        log(f"FID {fid} is in cooldown period. {cooldown_remaining} seconds remaining.")
        return {"msg": "In cooldown"}, retry_queue

    try:
        login_payload = encode_data({"fid": fid, "time": int(time.time() * 1000)})
        login_resp = make_request(LOGIN_URL, login_payload)
        if not login_resp:
            return {"msg": "Login request failed after retries"}, retry_queue

        login_data = login_resp.json()
        if login_data.get("code") != 0:
            login_msg = login_data.get('msg', 'Unknown login error')
            log(f"Login failed for {fid}: {login_data.get('code')}, {login_msg}")
            return {"msg": f"Login failed: {login_msg}"}, retry_queue

        for attempt in range(CAPTCHA_RETRIES):
            try:
                captcha_code, retry_queue = fetch_captcha_code(fid, retry_queue)
                if captcha_code is None:
                    return {"msg": "Added to retry queue due to CAPTCHA rate limit"}, retry_queue

                redeem_payload = encode_data({
                    "fid": fid,
                    "cdk": cdk,
                    "captcha_code": captcha_code,
                    "time": int(time.time() * 1000)
                })
                redeem_resp = make_request(REDEEM_URL, redeem_payload)
                if not redeem_resp:
                    return {"msg": "Redemption request failed after retries"}, retry_queue

                redeem_data = redeem_resp.json()
                msg = redeem_data.get('msg', 'Unknown error').strip('.')

                if msg in ["CAPTCHA CHECK ERROR", "Sign Error", "Server requested retry"]:
                    log(f"CAPTCHA attempt failed, retrying... (Attempt {attempt+1}/{CAPTCHA_RETRIES})")
                    time.sleep(random.uniform(2.5, 4))
                    continue
                elif msg == "CAPTCHA CHECK TOO FREQUENT":
                    log(f"CAPTCHA check too frequent, adding to retry queue for {CAPTCHA_SLEEP} seconds...")
                    retry_queue[fid] = current_time + CAPTCHA_SLEEP
                    return {"msg": "Added to retry queue"}, retry_queue
                elif msg == "NOT LOGIN":
                    log(f"Session expired or invalid after CAPTCHA. Skipping {fid}.")
                    return {"msg": "Session expired after CAPTCHA"}, retry_queue
                else:
                    return redeem_data, retry_queue
            except Exception as e:
                log(f"Error during CAPTCHA attempt {attempt+1}: {e}")
                if attempt == CAPTCHA_RETRIES - 1:
                    log(f"Reached max CAPTCHA retries for FID {fid}, adding to retry queue")
                    retry_queue[fid] = current_time + CAPTCHA_SLEEP
                    return {"msg": "Max retries reached, added to retry queue"}, retry_queue

        retry_queue[fid] = current_time + CAPTCHA_SLEEP
        return {"msg": "Added to retry queue"}, retry_queue
    except Exception as e:
        log(f"Unexpected error during redemption for {fid}: {e}")
        return {"msg": f"Unexpected Error: {e}"}, retry_queue

def print_summary():
    script_end_time = time.time()
    total_seconds = script_end_time - script_start_time
    execution_time = str(timedelta(seconds=int(total_seconds)))

    log("\n=== Redemption Complete ===")
    log(f"Successfully redeemed: {counters['success']}")
    log(f"Already redeemed: {counters['already_redeemed']}")
    log(f"Errors/Failures: {counters['errors']}")

    if error_details:
        log("\n=== Error Details ===")
        log("The following IDs encountered errors:")
        for fid, error_msg in error_details.items():
            log(f"FID {fid}: {error_msg}")

    log("\n=== CAPTCHA Statistics ===")
    log(f"Total attempts: {counters['captcha_attempts']}")
    log(f"Successful decodes: {counters['captcha_success']}")
    log(f"First attempt success: {counters['captcha_first_try']}")
    log(f"Retries: {counters['captcha_retry']}")
    log(f"Complete failures: {counters['captcha_failures']}")

    success_rate = (counters['captcha_success'] / counters['captcha_attempts'] * 100) if counters['captcha_attempts'] > 0 else 0
    first_try_rate = (counters['captcha_first_try'] / counters['captcha_success'] * 100) if counters['captcha_success'] > 0 else 0
    avg_attempts = (counters['captcha_attempts'] / counters['captcha_success']) if counters['captcha_success'] > 0 else 0

    log(f"Success rate: {success_rate:.2f}%")
    log(f"First try success rate: {first_try_rate:.2f}%")
    log(f"Average attempts per successful CAPTCHA: {avg_attempts:.2f}")
    log(f"Total execution time: {execution_time}")

if __name__ == "__main__":
    log(f"\n=== Starting redemption for gift code: {args.code} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # Load player data
    try:
        with open(args.player_file, encoding="utf-8") as player_file:
            players = json.loads(player_file.read())
    except FileNotFoundError:
        log(f"Error: Player file '{args.player_file}' not found")
        sys.exit(1)
    except Exception as e:
        log(f"Error reading player file: {e}")
        sys.exit(1)

    # Load or initialize results
    results = []
    if exists(args.results_file):
        try:
            with open(args.results_file, encoding="utf-8") as results_file:
                results = json.loads(results_file.read())
        except Exception as e:
            log(f"Error reading results file: {e}")

    found_item = next((result for result in results if result["code"] == args.code), None)
    if found_item is None:
        log(f"New code: {args.code} adding to results file and processing.")
        new_item = {"code": args.code, "status": {}}
        results.append(new_item)
        result = new_item
    else:
        result = found_item

    # Setup HTTP session with retries
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
            log(f"Processing {player['original_name']} ({fid}) [{i}/{len(players)}]")

            if fid not in retry_attempts:
                retry_attempts[fid] = 0

            result_data, retry_queue = redeem_gift_code(fid, args.code, retry_queue)
            raw_msg = result_data.get('msg', 'Unknown error').strip('.')
            friendly_msg = RESULT_MESSAGES.get(raw_msg, raw_msg)

            if raw_msg in ["Added to retry queue", "Max retries reached, added to retry queue", 
                           "In cooldown", "Added to retry queue due to CAPTCHA rate limit"]:
                retry_attempts[fid] += 1
                if retry_attempts[fid] >= MAX_RETRY_ATTEMPTS:
                    log(f"FID {fid} failed after {MAX_RETRY_ATTEMPTS} retry attempts, marking as error")
                    processed_fids.add(fid)
                    counters["errors"] += 1
                    error_details[fid] = f"Failed after {MAX_RETRY_ATTEMPTS} retry attempts"
                    if fid in retry_queue:
                        del retry_queue[fid]
                continue

            processed_fids.add(fid)
            if raw_msg == "TIME ERROR":
                log("Code has expired! Script will now exit.")
                print_summary()
                sys.exit(1)
            elif raw_msg == "USED":
                log("Claim limit reached! Script will now exit.")
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

            log(f"Result: {friendly_msg}")
            time.sleep(DELAY)

        waiting_fids = [f for f, t in retry_queue.items() if t > current_time and f not in processed_fids]
        if waiting_fids:
            next_retry_time = min([t for f, t in retry_queue.items() if f in waiting_fids])
            wait_time = max(1, min(5, next_retry_time - current_time))
            log(f"{len(waiting_fids)} FIDs in cooldown. Next retry in ~{int(wait_time)} seconds.")
            time.sleep(wait_time)
        elif len(processed_fids) < len(players):
            time.sleep(5)

    # Save results
    try:
        with open(args.results_file, "w", encoding="utf-8") as fp:
            json.dump(results, fp)
    except Exception as e:
        log(f"Error writing results file: {e}")

    print_summary()
