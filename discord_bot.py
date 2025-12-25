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

@bot.tree.command(name="redeem", description="開始兌換禮包碼 (支援自動重試)")
@app_commands.describe(code="禮包碼")
@check_admin()
async def redeem(interaction: discord.Interaction, code: str):
    # 1. 告訴 Discord 我們收到了，請稍等（這很重要，防止 3 秒超時）
    await interaction.response.send_message(f"🚀 開始為所有玩家兌換代碼：**{code}**\n這可能需要幾分鐘，機器人將在背景運行，請勿重複執行...", ephemeral=True)
    
    # 2. 使用 asyncio.create_subprocess_exec 非阻塞地執行外部程式
    # 這是關鍵：這樣機器人本體不會卡死，可以繼續發送心跳包
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "redeem_code.py", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 準備一個變數來收集輸出
        output_buffer = ""
        
        # 3. 即時讀取輸出 (不會卡住機器人)
        while True:
            # 讀取一行
            line = await process.stdout.readline()
            if not line:
                break
            
            decoded_line = line.decode('utf-8').strip()
            if decoded_line:
                print(f"[Script]: {decoded_line}") # 在後台終端印出以便除錯
                output_buffer += decoded_line + "\n"

                # 如果是有意義的進度訊息（包含 Round 或 FINAL），我們可以嘗試編輯訊息通知用戶
                # 注意：不能太頻繁編輯訊息，不然會被 Discord 限制
                if "Round" in decoded_line or "FINAL" in decoded_line:
                    try:
                        await interaction.edit_original_response(content=f"🔄 執行中... **{code}**\n```\n{output_buffer[-1000:]}\n```") # 只顯示最後 1000 字避免過長
                    except:
                        pass # 如果編輯失敗就算了，不影響流程

        # 等待程式完全結束
        await process.wait()
        
        # 讀取錯誤輸出（如果有）
        stderr_data = await process.stderr.read()
        if stderr_data:
            output_buffer += f"\n[Errors]:\n{stderr_data.decode('utf-8')}"

        # 4. 最終結果報告
        final_message = f"✅ **兌換結束！** 代碼：{code}\n詳細結果：\n```\n{output_buffer[-1900:]}\n```" # 限制長度以免超過 Discord 上限
        
        try:
            await interaction.followup.send(final_message, ephemeral=True)
        except:
            # 如果原本的互動過期，嘗試用編輯的
            await interaction.edit_original_response(content=final_message)

    except Exception as e:
        await interaction.followup.send(f"❌ 執行時發生嚴重錯誤：{str(e)}", ephemeral=True)

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