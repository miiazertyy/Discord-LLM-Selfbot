import discord
import asyncio
import os
import json
import time
from pathlib import Path

from discord.ext import commands
from utils.ai import generate_response
from utils.split_response import split_response
from utils.error_notifications import webhook_log
from utils.helpers import resource_path, load_config


async def _notify_telegram_error(title: str, detail: str):
    """Forward an error to Telegram if telegram_error_notifications is enabled in config."""
    try:
        cfg = load_config()
        if not cfg.get("notifications", {}).get("telegram_error_notifications", False):
            return
        _cmd_file = Path(resource_path("config/tg_commands_1.json"))
        entry = {
            "id": f"err_{time.time()}",
            "cmd": "send_error_notification",
            "payload": {"title": title, "detail": str(detail)[:1500]},
            "ts": time.time(),
        }
        existing = []
        if _cmd_file.exists():
            try:
                existing = json.loads(_cmd_file.read_text())
            except Exception:
                pass
        existing.append(entry)
        _cmd_file.write_text(json.dumps(existing))
    except Exception:
        pass


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_before_invoke(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            import random
            await __import__("asyncio").sleep(random.uniform(0.8, 2.5))

    @commands.command(name="ping")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def ping(self, ctx):
        latency = self.bot.latency * 1000
        await ctx.send(f"Pong! Latency: {latency:.2f} ms", delete_after=30)

    @commands.command(name="help", description="Get all other commands!")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def help(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return

        p = self.bot.command_prefix
        lines = [
            "```",
            "📋  Commands",
            "─────────────────────────────",
            "  🌙  AI",
            f"  {p}pause                    pause / unpause AI responses",
            f"  {p}pauseuser <user>          stop responding to a user",
            f"  {p}unpauseuser <user>        resume responding to a user",
            f"  {p}persona <user> [text]     set / clear / show a per-user persona",
            f"  {p}wipe                      clear conversation history",
            f"  {p}analyse <user>            psychological read of a user",
            "─────────────────────────────",
            "  💬  Replies",
            f"  {p}reply <user>              manually reply to a user's last message",
            f"  {p}reply check               show users with unread messages",
            f"  {p}reply all                 respond to all users with unread messages",
            "─────────────────────────────",
            "  ⚙️   Instructions & Config",
            f"  {p}prompt [text]             view / set / clear instructions (use 'clear' to wipe)",
            f"  {p}instructions              upload a new instructions.txt (attach .txt file)",
            f"  {p}getinstructions  (gi)     download current instructions.txt",
            f"  {p}config                    view full config (all sections)",
            f"  {p}config <key> <value>      edit a config value using dot notation",
            f"  {p}getconfig  (gc)           download config.yaml",
            f"  {p}setconfig                 upload a new config.yaml (attach .yaml, bot restarts)",
            f"  {p}togglecommands            enable / disable Discord command responses",
            "─────────────────────────────",
            "  📡  Channels",
            f"  {p}toggleactive              toggle current channel as active",
            f"  {p}toggleactive <id>         toggle a specific channel by ID",
            f"  {p}toggledm                  toggle DM responses",
            f"  {p}togglegc                  toggle group chat responses",
            f"  {p}toggleserver              toggle server mention/reply responses",
            f"  {p}ignore <user>             ignore / unignore a user",
            "─────────────────────────────",
            "  🎙️   Voice",
            f"  {p}join <id / link>          join a voice channel (muted & deafened)",
            f"  {p}leave                     leave the current voice channel",
            f"  {p}autojoin <id / link>      auto-join a voice channel on startup",
            f"  {p}autojoin off              disable auto-join",
            "─────────────────────────────",
            "  🖼️   Images",
            f"  {p}image ls                  list all pictures with descriptions",
            f"  {p}image upload              upload picture(s) (attach file — auto-analysed)",
            f"  {p}image download <n>        download a picture by number",
            f"  {p}image delete <n>          delete a picture by number",
            f"  {p}image delete all          delete all pictures",
            "─────────────────────────────",
            "  🎭  Profile & Status",
            f"  {p}status [emoji] [text]     set a custom status",
            f"  {p}bio [text]                set profile bio",
            f"  {p}pfp [url / attach]        change profile picture",
            f"  {p}mood [name]               view or set current mood",
            "─────────────────────────────",
            "  🛠️   System",
            f"  {p}addfriend <user_id>       send a friend request by user ID",
            f"  {p}clear [limit]             delete messages in current channel",
            f"  {p}reload                    reload all cogs + instructions",
            f"  {p}restart                   restart the bot",
            f"  {p}shutdown                  shut down the bot",
            f"  {p}update                    update to latest release",
            f"  {p}update main               update to latest commit",
            f"  {p}getdb                     download memory database",
            f"  {p}leaderboard  (lb)  [f]    show top users (e.g. {p}lb 3d / 1w)",
            f"  {p}ping                      show latency",
            "```",
        ]
        await ctx.send("\n".join(lines), delete_after=60)

    @commands.command(
        aliases=["analyze"],
        description="Analyze a user's message history and provides a psychological profile.",
    )
    @commands.cooldown(1, 300, commands.BucketType.user)
    async def analyse(self, ctx, user: discord.User):
        temp = await ctx.send(f"Analysing {user.name}'s message history...", delete_after=60)

        message_history = []
        async for message in ctx.channel.history(limit=200):
            if message.author == user:
                message_history.append(message.content)

        if len(message_history) > 200:
            message_history = message_history[-200:]

        instructions = (
            self.bot.instructions +
            f"\n\nSomeone asked you to give your honest read on {user.name} based on their messages. "
            "Stay in character. Give your real unfiltered opinion like you would to a friend. "
            "Be casual, funny, and direct. Roast them a bit but also be real about what you actually see. "
            "Reference specific things they said to back up your points. "
            "Keep it conversational — no bullet points, no formal structure, just talk like yourself. "
            "Don't be overly mean but don't sugarcoat either. Max 3-4 short paragraphs."
        )
        prompt = "Here are their messages: " + " | ".join(message_history)

        async def generate_response_in_thread(prompt):
            try:
                if not message_history:
                    await temp.edit(content=f"No messages found for {user.name} in this channel.", delete_after=15)
                    return

                response = await generate_response(prompt, instructions, history=None)
                if not response:
                    await temp.edit(content="Couldn't generate a response.", delete_after=15)
                    return
                chunks = split_response(response)
                await temp.delete()
                for chunk in chunks:
                    await ctx.reply(chunk, delete_after=120)
            except Exception as e:
                try:
                    await temp.edit(content="Something went wrong.", delete_after=15)
                except Exception:
                    pass
                await webhook_log(ctx.message, e)
                await _notify_telegram_error("Analyse Error", str(e))

        asyncio.create_task(generate_response_in_thread(prompt))


async def setup(bot):
    await bot.add_cog(General(bot))
