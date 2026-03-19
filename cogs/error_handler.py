import discord

from discord.ext import commands
from utils.error_notifications import print_error, webhook_log


class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._processed_commands = set()

    @commands.Cog.listener()
    async def on_command(self, ctx):
        """Dedup — silently drop if this message ID was already processed."""
        if ctx.message.id in self._processed_commands:
            ctx.command = None  # prevent command from running
            return
        self._processed_commands.add(ctx.message.id)
        if len(self._processed_commands) > 500:
            self._processed_commands = set(list(self._processed_commands)[-250:])

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return

        elif isinstance(error, commands.BotMissingPermissions):
            print_error("Bot Missing Permissions", error)
            await webhook_log(ctx, error)

        elif isinstance(error, commands.MessageNotFound):
            print_error("Message Not Found", error)
            await webhook_log(ctx, error)

        elif isinstance(error, commands.ChannelNotFound):
            print_error("Channel Not Found", error)
            await webhook_log(ctx, error)

        elif isinstance(error, commands.MemberNotFound):
            print_error("Member Not Found", error)
            await webhook_log(ctx, error)


async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))
