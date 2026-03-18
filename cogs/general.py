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

        prefix = self.bot.command_prefix
        help_text = f"""```
Bot Commands:
{prefix}pause - Pause the bot from producing AI responses
{prefix}analyse [user] - Analyze a user's message history and provides a psychological profile
{prefix}wipe - Clears history of the bot
{prefix}ping - Shows the bot's latency
{prefix}toggleactive [id / channel] - Toggle a mentioned channel or the current channel to the list of active channels
{prefix}toggledm - Toggle if the bot should be active in DM's or not
{prefix}togglegc - Toggle if the bot should be active in group chats or not
{prefix}ignore [user] - Stop a user from using the bot
{prefix}reload - Reloads all cogs and the instructions
{prefix}instructions - Attach a .txt to change the other one
{prefix}getinstructions - Get the instructions.txt in chat
{prefix}getdb - Get database with users memorys
{prefix}prompt [prompt / clear] - View, set or clear the prompt for the AI
{prefix}restart - Restarts the entire bot
{prefix}shutdown - Shuts down the entire bot
```"""

        await ctx.send(help_text, delete_after=30)

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
