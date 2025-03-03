import discord
from discord import app_commands
from discord.ext import commands
import hashlib
import json
import sys
import time
import requests
from requests.adapters import HTTPAdapter, Retry

def setup(bot):
    @bot.tree.command(name="redeem", description="為所有玩家兌換禮品碼")
    async def redeem_code(interaction: discord.Interaction, gift_code: str):
        # 立即 defer 回應，避免交互過期
        await interaction.response.defer(ephemeral=True)

        # 全局變數
        PLAYER_FILE = "player.json"
        RESULTS_FILE = "results.json"
        URL = "https://wos-giftcode-api.centurygame.com/api"
        SALT = "tB87#kPtkxqOS2"
        HTTP_HEADER = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        # 讀取玩家和結果文件
        try:
            with open(PLAYER_FILE, encoding="utf-8") as player_file:
                players = json.loads(player_file.read())
        except FileNotFoundError:
            await interaction.followup.send("找不到 `player.json` 文件，請檢查文件是否存在！", ephemeral=True)
            return
        except json.JSONDecodeError:
            await interaction.followup.send("`player.json` 格式錯誤，請檢查內容！", ephemeral=True)
            return

        results = []
        if exists(RESULTS_FILE):
            try:
                with open(RESULTS_FILE, encoding="utf-8") as results_file:
                    results = json.loads(results_file.read())
            except (json.JSONDecodeError, IOError) as e:
                print(f"讀取 results.json 失敗：{str(e)}")
                await interaction.followup.send("`results.json` 格式錯誤，將創建新文件！", ephemeral=True)
                results = []

        # 查找或創建結果項目
        found_item = next((result for result in results if result["code"] == gift_code), None)
        if found_item is None:
            print(f"New code: {gift_code} adding to results file and processing.")
            new_item = {"code": gift_code, "status": {}}
            results.append(new_item)
            result = new_item
        else:
            result = found_item

        # 跟踪進度
        counter_successfully_claimed = 0
        counter_already_claimed = 0
        counter_error = 0
        success_messages = []
        already_claimed_messages = []
        error_messages = []

        # 設置重試策略（處理 API 速率限制）
        r = requests.Session()
        retry_config = Retry(
            total=5, backoff_factor=1, status_forcelist=[429], allowed_methods=False
        )
        r.mount("https://", HTTPAdapter(max_retries=retry_config))

        i = 0
        for player in players:
            i += 1
            progress = f"\x1b[K{i}/{len(players)} complete. Redeeming for {player['original_name']}"
            print(progress, end="\r", flush=True)

            # 檢查是否已兌換
            if result["status"].get(player["id"]) == "Successful":
                counter_already_claimed += 1
                already_claimed_messages.append(f"玩家 {player['original_name']} 已兌換過 `{gift_code}`")
                continue

            # 準備請求數據（玩家驗證）
            try:
                request_data = {"fid": player["id"], "time": time.time_ns()}
                request_data["sign"] = hashlib.md5(
                    (f"fid={request_data['fid']}&time={request_data['time']}{SALT}").encode("utf-8")
                ).hexdigest()

                # 玩家驗證（登入）
                login_request = r.post(
                    URL + "/player", data=request_data, headers=HTTP_HEADER, timeout=30
                )
                login_response = login_request.json()
            except Exception as e:
                print(f"玩家 {player['original_name']} 驗證失敗：{str(e)}")
                counter_error += 1
                error_messages.append(f"玩家 {player['original_name']} 驗證失敗：{str(e)}")
                continue

            if login_response["msg"] != "success":
                counter_error += 1
                error_messages.append(f"玩家 {player['original_name']} 驗證失敗：{login_response.get('msg', '未知錯誤')}")
                continue

            # 兌換禮品碼（最多重試 3 次）
            max_retries = 3
            retry_count = 0
            success = False
            while retry_count < max_retries and not success:
                try:
                    # 準備兌換請求數據
                    request_data["cdk"] = gift_code
                    request_data["sign"] = hashlib.md5(
                        (f"cdk={request_data['cdk']}&fid={request_data['fid']}&time={request_data['time']}{SALT}").encode("utf-8")
                    ).hexdigest()

                    # 兌換禮品碼
                    redeem_request = r.post(
                        URL + "/gift_code", data=request_data, headers=HTTP_HEADER, timeout=30
                    )
                    redeem_response = redeem_request.json()

                    if redeem_response["err_code"] == 40014:  # 無效代碼
                        await interaction.followup.send("禮品碼不存在！終止操作。", ephemeral=True)
                        sys.exit(1)
                    elif redeem_response["err_code"] == 40007:  # 過期代碼
                        await interaction.followup.send("禮品碼已過期！終止操作。", ephemeral=True)
                        sys.exit(1)
                    elif redeem_response["err_code"] == 40008:  # 已兌換
                        counter_already_claimed += 1
                        result["status"][player["id"]] = "Successful"
                        already_claimed_messages.append(f"玩家 {player['original_name']} 已兌換過 `{gift_code}`")
                        success = True
                    elif redeem_response["err_code"] == 20000:  # 成功兌換
                        counter_successfully_claimed += 1
                        result["status"][player["id"]] = "Successful"
                        success_messages.append(f"玩家 {player['original_name']} 成功兌換 `{gift_code}`")
                        success = True
                    elif redeem_response["err_code"] == 40011:  # SAME TYPE EXCHANGE
                        counter_error += 1
                        result["status"][player["id"]] = "Unsuccessful"
                        error_messages.append(f"玩家 {player['original_name']} 兌換失敗：已兌換相同類型的禮品碼（err_code: 40011）")
                        success = True
                    elif redeem_response["err_code"] == 40004:  # 超時，重試
                        retry_count += 1
                        if retry_count == max_retries:
                            result["status"][player["id"]] = "Unsuccessful"
                            error_messages.append(f"玩家 {player['original_name']} 兌換失敗（超時，重試 {max_retries} 次後仍失敗）")
                        else:
                            print(f"玩家 {player['original_name']} 兌換超時，第 {retry_count} 次重試...")
                            time.sleep(1)  # 等待 1 秒後重試
                    else:
                        counter_error += 1
                        result["status"][player["id"]] = "Unsuccessful"
                        error_messages.append(f"玩家 {player['original_name']} 兌換失敗：{str(redeem_response)}")
                        success = True
                except Exception as e:
                    print(f"玩家 {player['original_name']} 兌換失敗：{str(e)}")
                    retry_count += 1
                    if retry_count == max_retries:
                        result["status"][player["id"]] = "Unsuccessful"
                        error_messages.append(f"玩家 {player['original_name']} 兌換失敗（錯誤，重試 {max_retries} 次後仍失敗）：{str(e)}")
                    else:
                        print(f"玩家 {player['original_name']} 兌換錯誤，第 {retry_count} 次重試...")
                        time.sleep(1)  # 等待 1 秒後重試

        # 保存結果到 results.json
        try:
            with open(RESULTS_FILE, "w", encoding="utf-8") as fp:
                json.dump(results, fp, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存 results.json 失敗：{str(e)}")
            await interaction.followup.send(f"保存結果文件失敗：{str(e)}", ephemeral=True)

        # 發送集中總結訊息
        summary = (
            f"兌換禮品碼 `{gift_code}` 結果：\n"
            f"成功兌換的玩家數量：{counter_successfully_claimed}\n"
            f"已兌換過的玩家數量：{counter_already_claimed}\n"
            f"發生錯誤的玩家數量：{counter_error}\n\n"
        )

        if success_messages:
            summary += "成功兌換的玩家：\n" + "\n".join(success_messages) + "\n"
        if already_claimed_messages:
            summary += "已兌換過的玩家：\n" + "\n".join(already_claimed_messages) + "\n"
        if error_messages:
            summary += "發生錯誤的玩家：\n" + "\n".join(error_messages)

        # 由於 Discord 訊息長度限制（2000 字元），如果總結過長，分段發送
        if len(summary) > 1900:  # 留點餘量避免超過
            chunks = [summary[i:i + 1900] for i in range(0, len(summary), 1900)]
            for chunk in chunks:
                await interaction.followup.send(chunk, ephemeral=True)
        else:
            await interaction.followup.send(summary, ephemeral=True)