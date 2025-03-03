from keep_alive import keep_alive
from discord_bot import bot

# 啟動 Flask 服務（在獨立線程中運行）
keep_alive()

# 運行 Discord 機器人
bot.run()