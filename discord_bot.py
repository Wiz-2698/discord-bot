import discord
from discord import app_commands
from discord.ext import commands
import json
import io
from os.path import exists
import os
import subprocess
import asyncio

# 設置 Discord 機器人
intents = discord.Intents.default()
intents.message_content = True  # 允許讀取訊息內容
bot = commands.Bot(command_prefix='!', intents=intents)

# 全局變數，儲存玩家和結果文件路徑
PLAYER_FILE = "player.json"

# 確保 player.json 存在
def ensure_files_exist():
    if not exists(PLAYER_FILE):
        with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
            json.dump([], fp)

# 啟動時同步 Slash Commands
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f'機器人已啟動，名稱：{bot.user}，同步了 {len(synced)} 個 Slash Commands')
    except Exception as e:
        print(f"同步 Slash Commands 失敗：{e}")

# 檢查是否具有管理員權限
def check_admin():
    async def predicate(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("您需要管理員權限才能使用此指令！", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# Slash Command: 添加單個玩家
@bot.tree.command(name="addplayer", description="添加單個玩家到 player.json")
async def add_player(interaction: discord.Interaction, player_id: str, player_name: str):
    try:
        try:
            player_id = str(int(player_id))  # 確保是數字並轉為字符串
        except ValueError:
            await interaction.response.send_message("玩家 ID 必須是數字！", ephemeral=True)
            return

        if not player_name.strip():
            await interaction.response.send_message("玩家名稱不能為空！", ephemeral=True)
            return

        players = []
        if exists(PLAYER_FILE):
            try:
                with open(PLAYER_FILE, encoding="utf-8") as player_file:
                    players = json.loads(player_file.read())
            except (json.JSONDecodeError, IOError) as e:
                print(f"讀取 player.json 失敗：{str(e)}")
                await interaction.response.send_message("`player.json` 格式錯誤，將創建新文件！", ephemeral=True)
                players = []

        existing_player = next((player for player in players if player["id"] == player_id), None)
        if existing_player:
            existing_player["original_name"] = player_name
            await interaction.response.send_message(f"已更新玩家 ID `{player_id}` 的名稱為 `{player_name}`")
        else:
            new_player = {"id": player_id, "original_name": player_name}
            players.append(new_player)
            await interaction.response.send_message(f"成功添加玩家：ID `{player_id}`，名稱 `{player_name}`")

        try:
            with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
                json.dump(players, fp, ensure_ascii=False, indent=4)
        except Exception as e:
            await interaction.response.send_message(f"保存玩家資料失敗：{str(e)}")

    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)

# Slash Command: 添加多個玩家
@bot.tree.command(name="addplayers", description="添加多個玩家到 player.json（格式：id1,name1;id2,name2）")
async def add_players(interaction: discord.Interaction, player_list: str):
    try:
        if not player_list.strip():
            await interaction.response.send_message("格式錯誤！請使用 `id1,name1;id2,name2` 格式，例如 `12312312,Lemoj;12345679,OOOO`", ephemeral=True)
            return

        players_to_add = [p.strip() for p in player_list.split(';')]
        successful_additions = []
        errors = []

        current_players = []
        if exists(PLAYER_FILE):
            try:
                with open(PLAYER_FILE, encoding="utf-8") as player_file:
                    current_players = json.loads(player_file.read())
            except (json.JSONDecodeError, IOError) as e:
                print(f"讀取 player.json 失敗：{str(e)}")
                await interaction.response.send_message("`player.json` 格式錯誤，將創建新文件！", ephemeral=True)
                current_players = []

        for player_info in players_to_add:
            if ',' not in player_info:
                errors.append(f"格式錯誤：{player_info}（缺少逗號分隔）")
                continue

            player_id, player_name = player_info.split(',', 1)
            
            try:
                player_id = str(int(player_id.strip()))
            except ValueError:
                errors.append(f"玩家 ID 錯誤：{player_info}（ID 必須是數字）")
                continue

            player_name = player_name.strip()
            if not player_name:
                errors.append(f"玩家名稱錯誤：{player_info}（名稱不能為空）")
                continue

            existing_player = next((player for player in current_players if player["id"] == player_id), None)
            if existing_player:
                existing_player["original_name"] = player_name
                successful_additions.append(f"已更新玩家 ID `{player_id}` 的名稱為 `{player_name}`")
            else:
                new_player = {"id": player_id, "original_name": player_name}
                current_players.append(new_player)
                successful_additions.append(f"成功添加玩家：ID `{player_id}`，名稱 `{player_name}`")

        try:
            with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
                json.dump(current_players, fp, ensure_ascii=False, indent=4)
        except Exception as e:
            await interaction.response.send_message(f"保存玩家資料失敗：{str(e)}", ephemeral=True)
            return

        response = ""
        if successful_additions:
            response += "\n".join(successful_additions) + "\n"
        if errors:
            response += "以下項目發生錯誤：\n" + "\n".join(errors)
        await interaction.response.send_message(response if response else "無操作或所有操作成功！")

    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)

# Slash Command: 列出所有玩家
@bot.tree.command(name="listplayers", description="列出 player.json 中的所有玩家名稱")
async def list_players(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        if not exists(PLAYER_FILE):
            await interaction.followup.send("找不到 `player.json` 文件！", ephemeral=True)
            return

        with open(PLAYER_FILE, encoding="utf-8") as player_file:
            players = json.loads(player_file.read())

        if not players:
            await interaction.followup.send("目前沒有玩家資料！", ephemeral=True)
            return

        player_list = [f"{i+1}. {player['original_name']}" for i, player in enumerate(players)]
        messages = []
        current_message = "玩家列表：\n"
        for line in player_list:
            if len(current_message) + len(line) + 1 > 1900:
                messages.append(current_message)
                current_message = "玩家列表（續）：\n"
            current_message += line + "\n"
        if current_message.strip():
            messages.append(current_message)

        for message in messages:
            await interaction.channel.send(message)

    except json.JSONDecodeError:
        await interaction.followup.send("`player.json` 格式錯誤！請檢查文件內容。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

# Slash Command: 刪除玩家
@bot.tree.command(name="delplayer", description="根據列表編號從 player.json 中刪除玩家")
@check_admin()
async def del_player(interaction: discord.Interaction, number: int):
    try:
        if not exists(PLAYER_FILE):
            await interaction.response.send_message("找不到 `player.json` 文件！", ephemeral=True)
            return

        with open(PLAYER_FILE, encoding="utf-8") as player_file:
            players = json.loads(player_file.read())

        if not players:
            await interaction.response.send_message("目前沒有玩家資料！", ephemeral=True)
            return

        if number < 1 or number > len(players):
            await interaction.response.send_message(f"編號錯誤！請輸入 1 到 {len(players)} 之間的數字。", ephemeral=True)
            return

        deleted_player = players.pop(number - 1)
        await interaction.response.send_message(f"已刪除玩家：ID `{deleted_player['id']}`，名稱 `{deleted_player['original_name']}`")

        try:
            with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
                json.dump(players, fp, ensure_ascii=False, indent=4)
        except Exception as e:
            await interaction.response.send_message(f"保存玩家資料失敗：{str(e)}", ephemeral=True)

    except json.JSONDecodeError:
        await interaction.response.send_message("`player.json` 格式錯誤！請檢查文件內容。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)

# Slash Command: 匯出玩家資料
@bot.tree.command(name="exportplayers", description="將 player.json 匯出為檔案")
async def export_players(interaction: discord.Interaction):
    try:
        if not exists(PLAYER_FILE):
            await interaction.response.send_message("找不到 `player.json` 文件！", ephemeral=True)
            return

        with open(PLAYER_FILE, encoding="utf-8") as player_file:
            players = json.loads(player_file.read())

        if not players:
            await interaction.response.send_message("目前沒有玩家資料！", ephemeral=True)
            return

        json_data = json.dumps(players, ensure_ascii=False, indent=4)
        file = discord.File(fp=io.StringIO(json_data), filename="player.json")
        await interaction.response.send_message("已匯出 `player.json` 檔案：", file=file)

    except json.JSONDecodeError:
        await interaction.response.send_message("`player.json` 格式錯誤！請檢查文件內容。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"發生錯誤：{str(e)}", ephemeral=True)

# Slash Command: 匯入玩家資料
@bot.tree.command(name="importplayers", description="從上傳的檔案匯入玩家資料")
@check_admin()
async def import_players(interaction: discord.Interaction):
    await interaction.response.send_message("請上傳一個 `player.json` 檔案（60 秒內）。", ephemeral=True)
    
    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel and m.attachments

    try:
        message = await bot.wait_for('message', check=check, timeout=60.0)
        attachment = message.attachments[0]
        
        if not attachment.filename.endswith('.json'):
            await interaction.followup.send("檔案格式錯誤！請上傳 `.json` 檔案。", ephemeral=True)
            return

        content = await attachment.read()
        try:
            players = json.loads(content.decode('utf-8'))
            if not isinstance(players, list) or not all(isinstance(p, dict) and "id" in p and "original_name" in p for p in players):
                await interaction.followup.send("格式錯誤！請確保 JSON 格式正確，且每個項目包含 `id` 和 `original_name`。", ephemeral=True)
                return

            for player in players:
                try:
                    player["id"] = str(int(player["id"]))
                except ValueError:
                    await interaction.followup.send(f"玩家 ID 錯誤：{player['id']} 必須是數字！", ephemeral=True)
                    return

            current_players = []
            if exists(PLAYER_FILE):
                try:
                    with open(PLAYER_FILE, encoding="utf-8") as player_file:
                        current_players = json.loads(player_file.read())
                except (json.JSONDecodeError, IOError) as e:
                    print(f"讀取 player.json 失敗：{str(e)}")
                    await interaction.followup.send("`player.json` 格式錯誤，將創建新文件！", ephemeral=True)
                    current_players = []

            for new_player in players:
                existing_player = next((p for p in current_players if p["id"] == new_player["id"]), None)
                if existing_player:
                    existing_player["original_name"] = new_player["original_name"]
                else:
                    current_players.append(new_player)

            try:
                with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
                    json.dump(current_players, fp, ensure_ascii=False, indent=4)
                await interaction.followup.send("成功匯入玩家資料！")
            except Exception as e:
                await interaction.followup.send(f"保存玩家資料失敗：{str(e)}")

        except json.JSONDecodeError:
            await interaction.followup.send("JSON 格式錯誤！請檢查您的檔案內容。", ephemeral=True)

    except TimeoutError:
        await interaction.followup.send("時間已過！請重新使用 `/importplayers` 指令。", ephemeral=True)

# Slash Command: 兌換禮品碼並自動更新名稱
@bot.tree.command(name="redeem", description="為所有玩家兌換禮品碼並自動更新名稱")
async def redeem(interaction: discord.Interaction, gift_code: str, restart: bool = False):
    await interaction.response.defer(ephemeral=True)

    try:
        # 讀取當前玩家資料
        if not exists(PLAYER_FILE):
            await interaction.followup.send("找不到 `player.json` 文件！", ephemeral=True)
            return

        with open(PLAYER_FILE, encoding="utf-8") as player_file:
            players = json.loads(player_file.read())

        # 調用 redeem_code.py 腳本
        cmd = ["python", "redeem_code.py", "--code", gift_code]
        if restart:
            cmd.append("--restart")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            output = result.stdout
            lines = output.split('\n')
            messages = []
            current_message = "兌換結果：\n```\n"
            for line in lines:
                if len(current_message) + len(line) + 1 > 1900:
                    current_message += "\n```"
                    messages.append(current_message)
                    current_message = "兌換結果（續）：\n```\n"
                current_message += line + "\n"
            if current_message.strip():
                current_message += "\n```"
                messages.append(current_message)

            # 自動更新名稱（假設輸出包含 Player ID 和 New Name）
            updated_players = []
            for line in lines:
                if "Player ID:" in line and "New Name:" in line:
                    parts = line.split(", ")
                    if len(parts) >= 2:
                        player_id_part = parts[0].replace("Player ID: ", "")
                        new_name_part = parts[1].replace("New Name: ", "")
                        try:
                            player_id = str(int(player_id_part.strip()))
                            new_name = new_name_part.strip()
                            for player in players:
                                if player["id"] == player_id:
                                    player["original_name"] = new_name
                                    updated_players.append(player)
                                    break
                        except ValueError:
                            continue

            # 如果有玩家名稱更新，保存到 player.json
            if updated_players:
                for player in players:
                    if player not in updated_players and any(p["id"] == player["id"] for p in updated_players):
                        for updated_player in updated_players:
                            if updated_player["id"] == player["id"]:
                                player["original_name"] = updated_player["original_name"]
                try:
                    with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
                        json.dump(players, fp, ensure_ascii=False, indent=4)
                    messages.append("已自動更新玩家名稱至 `player.json`！")
                except Exception as e:
                    messages.append(f"保存更新後的玩家資料失敗：{str(e)}")

            for message in messages:
                await interaction.followup.send(message, ephemeral=True)
        else:
            error = result.stderr
            lines = error.split('\n')
            messages = []
            current_message = "兌換失敗，錯誤：\n```\n"
            for line in lines:
                if len(current_message) + len(line) + 1 > 1900:
                    current_message += "\n```"
                    messages.append(current_message)
                    current_message = "兌換失敗，錯誤（續）：\n```\n"
                current_message += line + "\n"
            if current_message.strip():
                current_message += "\n```"
                messages.append(current_message)
            for message in messages:
                await interaction.followup.send(message, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"執行兌換時發生錯誤：{str(e)}", ephemeral=True)

# 確保檔案存在
ensure_files_exist()

# 主程式：添加異常處理和自動重試
async def run_bot_with_retry():
    while True:
        try:
            print("正在啟動 Discord 機器人...")
            DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
            if not DISCORD_TOKEN:
                raise ValueError("環境變數 DISCORD_TOKEN 未設置！請在環境變數中設置您的 Discord 權杖。")
            await bot.start(DISCORD_TOKEN)
        except Exception as e:
            print(f"機器人崩潰，錯誤：{str(e)}")
            print("等待 10 秒後重新啟動...")
            await asyncio.sleep(10)
        except discord.errors.ConnectionClosed as e:
            print(f"Discord 連線斷開，錯誤：{str(e)}")
            print("等待 10 秒後重新連線...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_bot_with_retry())
