import discord
import asyncio
import os

from discord.ext import commands
from utils.ai import generate_response
from utils.split_response import split_response
from utils.error_notifications import webhook_log


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
            "  🤖  AI",
            f"  {p}pause              pause/unpause AI responses",
            f"  {p}wipe               clear conversation history",
            f"  {p}respond [user]     manually reply to a user's last message",
            f"  {p}analyse [user]     psychological profile of a user",
            "─────────────────────────────",
            "  ⚙️   Config",
            f"  {p}config             view/edit config inline",
            f"  {p}getconfig          download config.yaml",
            f"  {p}setconfig          upload a new config.yaml",
            f"  {p}instructions       upload new instructions.txt",
            f"  {p}getinstructions    download instructions.txt",
            f"  {p}prompt [text]      view/set/clear instructions inline",
            "─────────────────────────────",
            "  📡  Channels",
            f"  {p}toggleactive       toggle current channel",
            f"  {p}toggledm           toggle DM responses",
            f"  {p}togglegc           toggle group chat responses",
            f"  {p}ignore [user]      ignore/unignore a user",
            "─────────────────────────────",
            "  🛠️   System",
            f"  {p}update             update to latest release",
            f"  {p}update main        update to latest commit",
            f"  {p}reload             reload all cogs + instructions",
            f"  {p}restart            restart the bot",
            f"  {p}shutdown           shut down the bot",
            f"  {p}ping               show latency",
            f"  {p}getdb              download memory database",
            f"  {p}status [emoji] [text]  set custom status",
            f"  {p}bio [text]         set profile bio",
            f"  {p}pfp [url/attach]   change profile picture",
            f"  {p}mood [name]        view or set current mood",
            "```",
        ]
        await ctx.send("\n".join(lines), delete_after=60)

    @commands.command(
        aliases=["analyze"],
        description="Analyze a user's message history and provides a psychological profile.",
    )
    @commands.cooldown(1, 300, commands.BucketType.user)
    async def analyse(self, ctx, user: discord.User):
        temp = await ctx.send(f"Analysing {user.name}'s message history...")

        message_history = []
        async for message in ctx.channel.history(limit=1500):
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
                    await temp.edit(content=f"No messages found for {user.name} in this channel.")
                    return

                response = await generate_response(prompt, instructions, history=None)
                if not response:
                    await temp.edit(content="Couldn't generate a response.")
                    return
                chunks = split_response(response)
                await temp.delete()
                for chunk in chunks:
                    await ctx.reply(chunk)
            except Exception as e:
                try:
                    await temp.edit(content="Something went wrong.")
                except Exception:
                    pass
                await webhook_log(ctx.message, e)

        asyncio.create_task(generate_response_in_thread(prompt))


async def setup(bot):
    await bot.add_cog(General(bot))
