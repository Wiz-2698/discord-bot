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

        # 因為名單可能很長，我們製作成文字檔發送，或是分段發送
        # 這裡選擇分段發送，每段最多顯示 10-15 人，避免洗版，或者直接發送總數
        
        count = len(players)
        msg = f"**目前名單共 {count} 人**：\n"
        
        # 為了避免超過 Discord 2000字限制，如果人太多，建議只顯示前幾名或存成檔案
        if count > 50:
            # 人數多時，生成一個臨時文件發送
            filename = "player_list.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for p in players:
                    f.write(f"{p['original_name']} ({p['id']})\n")
            
            await interaction.followup.send(f"人數眾多 ({count} 人)，請查看附件檔案：", file=discord.File(filename), ephemeral=True)
        else:
            # 人數少時直接顯示
            for p in players:
                msg += f"- {p['original_name']} ({p['id']})\n"
            await interaction.followup.send(msg, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"發生錯誤：{str(e)}", ephemeral=True)

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