import discord
import os
import sys
import subprocess
import yaml
import re
import asyncio
import requests

from discord.ext import commands
from utils.helpers import load_instructions, load_config, resource_path
from utils.db import (
    add_ignored_user,
    remove_ignored_user,
    remove_channel,
    add_channel,
)


class Management(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def save_config(self, new_config):
        config_path = resource_path("config/config.yaml")

        with open(config_path, "w", encoding="utf-8") as file:
            yaml.dump(new_config, file, default_flow_style=False, allow_unicode=True)

    @commands.command(
        name="pause", description="Pause the bot from producing AI responses."
    )
    async def pause(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.paused = not self.bot.paused
            await ctx.send(
                f"{'Paused' if self.bot.paused else 'Unpaused'} the bot from producing AI responses."
            )

    @commands.command(name="toggledm", description="Toggle DM for chatting")
    async def toggledm(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.allow_dm = not self.bot.allow_dm

            config = load_config()
            config["bot"]["allow_dm"] = self.bot.allow_dm
            self.save_config(config)

            await ctx.send(
                f"DMs are now {'allowed' if self.bot.allow_dm else 'disallowed'} for active channels."
            )

    @commands.command(name="togglegc", description="Toggle chatting in group chats.")
    async def togglegc(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.allow_gc = not self.bot.allow_gc

            config = load_config()
            config["bot"]["allow_gc"] = self.bot.allow_gc
            self.save_config(config)

            await ctx.send(
                f"Group chats are now {'allowed' if self.bot.allow_gc else 'disallowed'} for active channels."
            )

    @commands.command()
    async def ignore(self, ctx, user: discord.User):
        try:
            if ctx.author.id == self.bot.owner_id:
                if user.id in self.bot.ignore_users:
                    self.bot.ignore_users.remove(user.id)
                    remove_ignored_user(user.id)
                    await ctx.send(f"Unignored {user.name}.")
                else:
                    self.bot.ignore_users.append(user.id)
                    add_ignored_user(user.id)
                    await ctx.send(f"Ignoring {user.name}.")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @commands.command(name="toggleactive", description="Toggle active channels")
    async def toggleactive(self, ctx, channel=None):
        if ctx.author.id == self.bot.owner_id:
            if channel is None:
                channel = ctx.channel
                channel_id = channel.id
            else:
                mention_match = re.match(r"<#(\d+)>", channel)
                if mention_match:
                    channel_id = int(mention_match.group(1))
                else:
                    channel_id = int(channel)

                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except discord.errors.NotFound:
                    await ctx.send("Channel not found.")
                    return

            if channel_id in self.bot.active_channels:
                self.bot.active_channels.remove(channel_id)
                remove_channel(channel_id)
                await ctx.send(
                    f"{'This DM' if isinstance(ctx.channel, discord.DMChannel) else 'This group' if isinstance(ctx.channel, discord.GroupChannel) else channel.mention} has been removed from the list of active channels."
                )
            else:
                self.bot.active_channels.add(channel_id)
                add_channel(channel_id)
                await ctx.send(
                    f"{'This DM' if isinstance(ctx.channel, discord.DMChannel) else 'This group' if isinstance(ctx.channel, discord.GroupChannel) else channel.mention} has been added to the list of active channels."
                )

    @commands.command(
        name="wipe",
        description="Clears the bots message history, resetting it's memory.",
    )
    async def wipe(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.message_history.clear()
            await ctx.send("Wiped the bot's memory.")

    @commands.command(
        name="reload",
        description="Reloads all cogs and the bot instructions.",
    )
    async def reload(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py"):
                    try:
                        await self.bot.unload_extension(f"cogs.{filename[:-3]}")
                        await self.bot.load_extension(f"cogs.{filename[:-3]}")
                    except Exception as e:
                        print(f"Failed to reload extension {filename}. Error: {e}")
                        await ctx.send(
                            f"Failed to reload {filename}. Check logs for details."
                        )

            self.bot.instructions = load_instructions()
            await ctx.send("Reloaded all cogs.")

    @commands.command(
        name="restart",
        description="Restarts the bot.",
    )
    async def restart(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            await ctx.send("Restarting...")
            print("Restarting bot...")

            if getattr(sys, "frozen", False):
                exe_path = sys.executable
                os.startfile(exe_path)
                await asyncio.sleep(3)
                await ctx.bot.close()
                sys.exit(0)
            else:
                python = sys.executable
                subprocess.Popen([python] + sys.argv)
                await ctx.bot.close()
                sys.exit(0)

    @commands.command(
        name="shutdown",
        description="Shuts down the bot.",
    )
    async def shutdown(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            await ctx.send("Shutting down...")
            print("Shutting down...")
            await ctx.bot.close()
            sys.exit(0)

    @commands.command(
        name="update",
        description="Pulls the latest update from GitHub and relaunches the bot. Use 'main' to pull latest commit.",
    )
    async def update(self, ctx, source: str = "release"):
        if ctx.author.id != self.bot.owner_id:
            return

        if source not in ("release", "main"):
            await ctx.send(f"Invalid option. Use `,update` for latest release or `,update main` for latest commit.", delete_after=10)
            return

        if source == "main":
            msg = await ctx.send("Pulling latest commit from main... brb")
        else:
            latest = None
            try:
                response = requests.get(
                    "https://api.github.com/repos/miiazertyy/Discord-LLM-Selfbot/releases/latest",
                    timeout=10
                )
                if response.status_code == 200:
                    latest = response.json().get("tag_name", "unknown")
            except Exception:
                pass
            msg = await ctx.send(f"Updating to {latest if latest else 'latest'}... brb")

        # Save pending messages so bot can reply after restart
        self._save_pending_messages()

        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "updater.bat"], shell=True)
        else:
            subprocess.Popen(["bash", "updater.sh"])

        await msg.edit(content="Updated! Relaunching...")
        await asyncio.sleep(1)
        await ctx.bot.close()
        sys.exit(0)

    @commands.command(
        name="instructions",
        description="Attach a .txt file to update the bot instructions.",
        aliases=["setinstructions"],
    )
    async def instructions(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return

        if not ctx.message.attachments:
            await ctx.send("Please attach a `.txt` file to update the instructions.", delete_after=10)
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".txt"):
            await ctx.send("Only `.txt` files are supported.", delete_after=10)
            return

        content = await attachment.read()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            await ctx.send("Could not read file, make sure it's valid UTF-8.", delete_after=10)
            return

        self.bot.instructions = text
        with open(resource_path("config/instructions.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        await ctx.send("Instructions updated from file!", delete_after=10)

    @commands.command(
        name="getinstructions",
        description="Sends the current instructions.txt file.",
        aliases=["gi"],
    )
    async def getinstructions(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return

        instructions_path = resource_path("config/instructions.txt")

        if not os.path.exists(instructions_path):
            await ctx.send("No instructions file found.", delete_after=10)
            return

        await ctx.send(file=discord.File(instructions_path, filename="instructions.txt"))

    @commands.command(
        name="prompt",
        description="View, set or clear the prompt for the AI.",
        aliases=["setprompt", "sp"],
    )
    async def prompt(self, ctx, *, text=None):
        if ctx.author.id != self.bot.owner_id:
            return

        if text is None:
            await ctx.send(
                f"Current prompt:\n{f'```{self.bot.instructions}```' if self.bot.instructions != '' else 'No prompt is currently set.'}"
            )
        elif text.lower() == "clear":
            self.bot.instructions = ""
            with open(resource_path("config/instructions.txt"), "w", encoding="utf-8") as f:
                f.write("")
            await ctx.send("Cleared prompt.")
        else:
            self.bot.instructions = text
            with open(resource_path("config/instructions.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            await ctx.send(f"Updated prompt to:\n```{text}```")


    @commands.command(
        name="getdb",
        description="Sends the bot_data.db file to Discord.",
    )
    async def getdb(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return

        db_path = resource_path("config/bot_data.db")

        if not os.path.exists(db_path):
            await ctx.send("No database file found.", delete_after=10)
            return

        await ctx.send(file=discord.File(db_path, filename="bot_data.db"))


    @commands.command(
        name="getconfig",
        description="Sends the current config.yaml file.",
        aliases=["gc"],
    )
    async def getconfig(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return

        config_path = resource_path("config/config.yaml")

        if not os.path.exists(config_path):
            await ctx.send("No config file found.", delete_after=10)
            return

        await ctx.send(file=discord.File(config_path, filename="config.yaml"))

    @commands.command(
        name="setconfig",
        description="Attach a .yaml file to update the bot config. Bot will restart automatically.",
    )
    async def setconfig(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return

        if not ctx.message.attachments:
            await ctx.send("Please attach a `.yaml` file to update the config.", delete_after=10)
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".yaml"):
            await ctx.send("Only `.yaml` files are supported.", delete_after=10)
            return

        content = await attachment.read()
        try:
            text = content.decode("utf-8")
            # Validate it's valid yaml before saving
            yaml.safe_load(text)
        except UnicodeDecodeError:
            await ctx.send("Could not read file, make sure it's valid UTF-8.", delete_after=10)
            return
        except yaml.YAMLError as e:
            await ctx.send(f"Invalid YAML: {e}", delete_after=15)
            return

        config_path = resource_path("config/config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(text)

        await ctx.send("Config updated! Restarting...", delete_after=10)
        await asyncio.sleep(1)

        if getattr(sys, "frozen", False):
            exe_path = sys.executable
            os.startfile(exe_path)
            await asyncio.sleep(3)
            await ctx.bot.close()
            sys.exit(0)
        else:
            python = sys.executable
            subprocess.Popen([python] + sys.argv)
            await ctx.bot.close()
            sys.exit(0)


    def _save_pending_messages(self):
        """Save the last message from each active conversation to disk so we can reply after restart."""
        import json
        from utils.helpers import resource_path

        pending = {}
        for key, history in self.bot.message_history.items():
            if history and history[-1]["role"] == "user":
                user_id, channel_id = key.split("-")
                pending[key] = {
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "content": history[-1]["content"],
                    "history": history,
                }

        path = resource_path("config/pending_messages.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pending, f)
        print(f"[Update] Saved {len(pending)} pending message(s) for post-restart reply.")

    @commands.command(
        name="config",
        description="View or edit config values. Use dot notation for nested keys.",
    )
    async def config_cmd(self, ctx, key: str = None, *, value: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        config = load_config()

        # Display all settings
        if key is None:
            bot_cfg = config["bot"]
            tts = bot_cfg.get("tts") or {}
            fr = bot_cfg.get("friend_requests") or {}
            mood = bot_cfg.get("mood") or {}
            late = bot_cfg.get("late_reply") or {}
            status = bot_cfg.get("status") or {}

            lines = [
                "```",
                "⚙️  Bot Config",
                "─────────────────────────────",
                f"prefix              {bot_cfg.get('prefix')}",
                f"trigger             {bot_cfg.get('trigger')}",
                f"allow_dm            {bot_cfg.get('allow_dm')}",
                f"allow_gc            {bot_cfg.get('allow_gc')}",
                f"realistic_typing    {bot_cfg.get('realistic_typing')}",
                f"batch_messages      {bot_cfg.get('batch_messages')}",
                f"hold_conversation   {bot_cfg.get('hold_conversation')}",
                f"ignore_chance       {bot_cfg.get('ignore_chance')}",
                f"typo_chance         {bot_cfg.get('typo_chance')}",
                f"anti_age_ban        {bot_cfg.get('anti_age_ban')}",
                f"disable_mentions    {bot_cfg.get('disable_mentions')}",
                f"reply_ping          {bot_cfg.get('reply_ping')}",
                "─────────────────────────────",
                f"tts.enabled         {tts.get('enabled')}",
                f"tts.voice           {tts.get('voice')}",
                f"tts.tones           {', '.join(tts.get('tones', []))}",
                "─────────────────────────────",
                f"mood.enabled        {mood.get('enabled')}",
                "─────────────────────────────",
                f"late_reply.enabled  {late.get('enabled')}",
                f"late_reply.threshold {late.get('threshold')}",
                "─────────────────────────────",
                f"friend_requests.enabled      {fr.get('enabled')}",
                f"friend_requests.accept_delay {fr.get('accept_delay')}",
                "─────────────────────────────",
                f"Models: {', '.join(bot_cfg.get('groq_models', []))}",
                "```",
                f"Use `,config <key> <value>` to edit. Example: `,config tts.voice diana`",
            ]
            await ctx.send("\n".join(lines), delete_after=60)
            return

        # Parse dot notation into nested dict path
        keys = key.split(".")
        
        # Type coercion
        def coerce(v):
            if v.lower() == "true": return True
            if v.lower() == "false": return False
            try: return int(v)
            except ValueError: pass
            try: return float(v)
            except ValueError: pass
            return v

        # Navigate to the right place and set the value
        try:
            node = config
            for k in keys[:-1]:
                if k not in node:
                    await ctx.send(f"Key `{key}` not found.", delete_after=10)
                    return
                node = node[k]
            
            final_key = keys[-1]
            if final_key not in node:
                await ctx.send(f"Key `{key}` not found.", delete_after=10)
                return

            old_val = node[final_key]
            node[final_key] = coerce(value)
            self.save_config(config)

            await ctx.send(f"✅ `{key}` updated: `{old_val}` → `{node[final_key]}`", delete_after=15)
        except Exception as e:
            await ctx.send(f"Error: {e}", delete_after=10)


async def setup(bot):
    await bot.add_cog(Management(bot))
