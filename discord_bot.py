import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import sys
import asyncio
from os.path import exists
from keep_alive import keep_alive

# 設置 Discord 機器人
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 全局變數
PLAYER_FILE = "player.json"

def ensure_files_exist():
    if not exists(PLAYER_FILE):
        with open(PLAYER_FILE, "w", encoding="utf-8") as fp:
            json.dump([], fp)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f'機器人已啟動：{bot.user}，同步了 {len(synced)} 個指令')
    except Exception as e:
        print(f"同步指令失敗：{e}")

# 權限檢查
def check_admin():
    async def predicate(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("您需要管理員權限才能使用此指令！", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# --- 指令區 ---

@bot.tree.command(name="add_player", description="添加單個玩家")
@app_commands.describe(player_id="玩家ID", player_name="玩家名稱")
@check_admin()
async def add_player(interaction: discord.Interaction, player_id: str, player_name: str):
    await interaction.response.defer(ephemeral=True)
    try:
        ensure_files_exist()
        with open(PLAYER_FILE, "r", encoding="utf-8") as f:
            players = json.load(f)
        
        # 檢查是否重複
        if any(p['id'] == player_id for p in players):
            await interaction.followup.send(f"玩家 ID {player_id} 已經存在！", ephemeral=True)
            return

        players.append({"id": player_id, "original_name": player_name})
        
        with open(PLAYER_FILE, "w", encoding="utf-8") as f:
            json.dump(players, f, ensure_ascii=False, indent=4)
            
        await interaction.followup.send(f"已添加玩家：{player_name} ({player_id})", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

@bot.tree.command(name="remove_player", description="移除單個玩家")
@app_commands.describe(player_id="玩家ID")
@check_admin()
async def remove_player(interaction: discord.Interaction, player_id: str):
    await interaction.response.defer(ephemeral=True)
    try:
        ensure_files_exist()
        with open(PLAYER_FILE, "r", encoding="utf-8") as f:
            players = json.load(f)
        
        initial_count = len(players)
        # 過濾掉該 ID
        players = [p for p in players if p['id'] != player_id]
        
        if len(players) == initial_count:
            await interaction.followup.send(f"找不到 ID 為 {player_id} 的玩家。", ephemeral=True)
            return

        with open(PLAYER_FILE, "w", encoding="utf-8") as f:
            json.dump(players, f, ensure_ascii=False, indent=4)
            
        await interaction.followup.send(f"已移除玩家 ID：{player_id}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

@bot.tree.command(name="list_players", description="列出所有玩家")
@check_admin()
async def list_players(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        ensure_files_exist()
        with open(PLAYER_FILE, "r", encoding="utf-8") as f:
            players = json.load(f)
            
        if not players:
            await interaction.followup.send("目前沒有玩家名單。", ephemeral=True)
            return
        
        count = len(players)
        msg = f"**目前名單共 {count} 人**：\n"
        
        if count > 50:
            filename = "player_list_temp.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for p in players:
                    f.write(f"{p['original_name']} ({p['id']})\n")
            
            await interaction.followup.send(f"人數眾多 ({count} 人)，請查看附件檔案：", file=discord.File(filename), ephemeral=True)
        else:
            for p in players:
                msg += f"- {p['original_name']} ({p['id']})\n"
            await interaction.followup.send(msg, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

# ===== 新增功能區 =====

@bot.tree.command(name="export_players", description="匯出 player.json 檔案 (方便備份至 GitHub)")
@check_admin()
async def export_players(interaction: discord.Interaction):
    """匯出目前的 player.json 讓使用者可以去更新 GitHub"""
    await interaction.response.defer(ephemeral=True)
    try:
        ensure_files_exist()
        # 直接把機器人伺服器裡的 player.json 當成檔案傳到 Discord
        file = discord.File(PLAYER_FILE, filename="player_backup.json")
        await interaction.followup.send("📦 這是目前的玩家名單備份！\n請下載此檔案並覆蓋到您的 GitHub Repository 中，以免重置後遺失：", file=file, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"匯出失敗：{str(e)}", ephemeral=True)

@bot.tree.command(name="import_players_json", description="上傳 player.json 檔案以批次匯入玩家")
@app_commands.describe(json_file="請上傳 .json 格式的名單檔案")
@check_admin()
async def import_players_json(interaction: discord.Interaction, json_file: discord.Attachment):
    """讀取上傳的 json 檔案並合併到現有名單中"""
    await interaction.response.defer(ephemeral=True)
    if not json_file.filename.endswith('.json'):
        await interaction.followup.send("❌ 錯誤：請確保上傳的是 `.json` 檔案！", ephemeral=True)
        return

    try:
        # 讀取上傳的檔案內容
        file_bytes = await json_file.read()
        new_players = json.loads(file_bytes.decode('utf-8'))

        ensure_files_exist()
        with open(PLAYER_FILE, "r", encoding="utf-8") as f:
            current_players = json.load(f)

        existing_ids = {p['id'] for p in current_players}
        added_count = 0

        # 合併名單（防重複）
        for p in new_players:
            if 'id' in p and 'original_name' in p:
                if p['id'] not in existing_ids:
                    current_players.append({"id": p['id'], "original_name": p['original_name']})
                    existing_ids.add(p['id'])
                    added_count += 1

        with open(PLAYER_FILE, "w", encoding="utf-8") as f:
            json.dump(current_players, f, ensure_ascii=False, indent=4)

        await interaction.followup.send(f"✅ 匯入成功！從檔案中成功新增了 {added_count} 名新玩家（已略過重複的 ID）。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 檔案解析錯誤：請確認檔案是標準的 JSON 格式。\n錯誤訊息: {str(e)}", ephemeral=True)

@bot.tree.command(name="add_multiple_players", description="批次新增多名玩家 (支援單行無腦貼上)")
@app_commands.describe(players_data="格式：ID,名字；ID 名字 (可用分號、逗號或空白隔開)")
@check_admin()
async def add_multiple_players(interaction: discord.Interaction, players_data: str):
    """文字輸入批次新增，支援多種分隔符號無腦貼上"""
    await interaction.response.defer(ephemeral=True)
    try:
        ensure_files_exist()
        with open(PLAYER_FILE, "r", encoding="utf-8") as f:
            players = json.load(f)

        existing_ids = {p['id'] for p in players}
        added_count = 0
        
        # 將全形分號與換行符號，全部統一替換成半形分號，方便一次切割
        normalized_data = players_data.replace('\n', ';').replace('；', ';')
        
        # 用分號切開每個玩家的資料
        entries = normalized_data.split(';')

        for entry in entries:
            entry = entry.strip()
            if not entry: continue
            
            # 將全形逗號、全形空白替換成半形，以利後續分割 ID 與名稱
            entry = entry.replace('，', ',').replace('　', ' ')
            
            if ',' in entry:
                parts = entry.split(',', 1)
            else:
                # split(maxsplit=1) 會自動把中間多餘的空白或 Tab 當成一個分隔符號
                # 這對直接從試算表複製貼上的格式非常友善
                parts = entry.split(maxsplit=1)
                
            if len(parts) >= 2:
                pid = parts[0].strip()
                pname = parts[1].strip()
                
                if pid.isdigit() and pid not in existing_ids:
                    players.append({"id": pid, "original_name": pname})
                    existing_ids.add(pid)
                    added_count += 1

        with open(PLAYER_FILE, "w", encoding="utf-8") as f:
            json.dump(players, f, ensure_ascii=False, indent=4)

        await interaction.followup.send(f"✅ 批次新增完成！共成功讀取並添加了 {added_count} 名新玩家。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 發生錯誤：{str(e)}", ephemeral=True)

# ===== 兌換功能 (保持原樣) =====

@bot.tree.command(name="redeem", description="開始兌換禮包碼 (背景執行)")
@app_commands.describe(code="禮包碼")
@check_admin()
async def redeem(interaction: discord.Interaction, code: str):
    # 1. 回應 Discord 防止超時
    await interaction.response.send_message(f"🚀 開始為所有玩家兌換代碼：**{code}**\n機器人將在背景運行，請耐心等待...", ephemeral=True)
    
    # 2. 非阻塞執行 (防止斷線的核心)
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "redeem_code.py", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        output_buffer = ""
        
        # 3. 即時讀取輸出
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            decoded_line = line.decode('utf-8').strip()
            if decoded_line:
                print(f"[Script]: {decoded_line}") 
                output_buffer += decoded_line + "\n"

                # 只有在回合結束或程式結束時才更新訊息
                if "Round" in decoded_line or "FINAL" in decoded_line:
                    try:
                        # 擷取最後 1000 字元避免過長
                        display_text = output_buffer[-1000:]
                        await interaction.edit_original_response(content=f"🔄 執行中... **{code}**\n```\n{display_text}\n```")
                    except:
                        pass 

        await process.wait()
        
        # 讀取錯誤
        stderr_data = await process.stderr.read()
        if stderr_data:
            output_buffer += f"\n[Errors]:\n{stderr_data.decode('utf-8')}"

        # 4. 最終報告
        final_message = f"✅ **兌換結束！** 代碼：{code}\n詳細結果：\n```\n{output_buffer[-1900:]}\n```"
        
        try:
            await interaction.followup.send(final_message, ephemeral=True)
        except:
            await interaction.edit_original_response(content=final_message)

    except Exception as e:
        await interaction.followup.send(f"❌ 執行錯誤：{str(e)}", ephemeral=True)

# 啟動 Flask 保持活躍
keep_alive()
ensure_files_exist()

# 啟動機器人
async def main():
    token = os.environ.get('DISCORD_TOKEN')
    if not token:
        print("錯誤：找不到 DISCORD_TOKEN 環境變數")
        return
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass