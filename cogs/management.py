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
from utils.logger import log_system, log_error
from utils.db import (
    add_ignored_user,
    remove_ignored_user,
    remove_channel,
    add_channel,
)


class Management(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_before_invoke(self, ctx):
        import random
        await __import__("asyncio").sleep(random.uniform(0.8, 2.5))

    def save_config(self, new_config):
        config_path = resource_path("config/config.yaml")
        with open(config_path, "w", encoding="utf-8") as file:
            yaml.dump(new_config, file, default_flow_style=False, allow_unicode=True)

    @commands.command(name="pause", description="Pause the bot from producing AI responses.")
    async def pause(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.paused = not self.bot.paused
            await ctx.send(f"{'Paused' if self.bot.paused else 'Unpaused'} the bot from producing AI responses.")

    @commands.command(name="toggledm", description="Toggle DM for chatting")
    async def toggledm(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.allow_dm = not self.bot.allow_dm
            config = load_config()
            config["bot"]["allow_dm"] = self.bot.allow_dm
            self.save_config(config)
            await ctx.send(f"DMs are now {'allowed' if self.bot.allow_dm else 'disallowed'} for active channels.")

    @commands.command(name="togglegc", description="Toggle chatting in group chats.")
    async def togglegc(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.allow_gc = not self.bot.allow_gc
            config = load_config()
            config["bot"]["allow_gc"] = self.bot.allow_gc
            self.save_config(config)
            await ctx.send(f"Group chats are now {'allowed' if self.bot.allow_gc else 'disallowed'} for active channels.")

    @commands.command(name="toggleserver", description="Toggle responding to mentions/replies in servers.")
    async def toggleserver(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.allow_server = not getattr(self.bot, 'allow_server', True)
            config = load_config()
            config["bot"]["allow_server"] = self.bot.allow_server
            self.save_config(config)
            await ctx.send(f"Server responses are now {'enabled' if self.bot.allow_server else 'disabled'}.")

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
                await ctx.send(f"{'This DM' if isinstance(ctx.channel, discord.DMChannel) else 'This group' if isinstance(ctx.channel, discord.GroupChannel) else channel.mention} has been removed from the list of active channels.")
            else:
                self.bot.active_channels.add(channel_id)
                add_channel(channel_id)
                await ctx.send(f"{'This DM' if isinstance(ctx.channel, discord.DMChannel) else 'This group' if isinstance(ctx.channel, discord.GroupChannel) else channel.mention} has been added to the list of active channels.")

    @commands.command(name="wipe", description="Clears the bots message history, resetting it's memory.")
    async def wipe(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            self.bot.message_history.clear()
            await ctx.send("Wiped the bot's memory.")

    @commands.command(name="reload", description="Reloads all cogs and the bot instructions.")
    async def reload(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py"):
                    try:
                        await self.bot.unload_extension(f"cogs.{filename[:-3]}")
                        await self.bot.load_extension(f"cogs.{filename[:-3]}")
                    except Exception as e:
                        print(f"Failed to reload extension {filename}. Error: {e}")
                        await ctx.send(f"Failed to reload {filename}. Check logs for details.")
            self.bot.instructions = load_instructions()
            await ctx.send("Reloaded all cogs.")

    @commands.command(name="restart", description="Restarts the bot.")
    async def restart(self, ctx):
        if ctx.author.id == self.bot.owner_id:
            msg = await ctx.send("Restarting...")
            print("Restarting bot...")
            if getattr(sys, "frozen", False):
                exe_path = sys.executable
                os.startfile(exe_path)
                await asyncio.sleep(3)
                await msg.delete()
                await ctx.bot.close()
                sys.exit(0)
            else:
                python = sys.executable
                subprocess.Popen([python] + sys.argv)
                await msg.delete()
                await ctx.bot.close()
                sys.exit(0)

    @commands.command(name="shutdown", description="Shuts down the bot.")
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
            version_str = "main"
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
            version_str = latest if latest else "latest"
            msg = await ctx.send(f"Updating to {version_str}... brb")

        self._save_pending_messages()

        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "updater.bat"], shell=True)
        else:
            subprocess.Popen(["bash", "updater.sh"])

        await msg.edit(content=f"Updated to {version_str}! Relaunching...")
        await msg.delete()
        await asyncio.sleep(1)
        await ctx.bot.close()
        sys.exit(0)

    @commands.command(name="instructions", description="Attach a .txt file to update the bot instructions.", aliases=["setinstructions"])
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

    @commands.command(name="getinstructions", description="Sends the current instructions.txt file.", aliases=["gi"])
    async def getinstructions(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return
        instructions_path = resource_path("config/instructions.txt")
        if not os.path.exists(instructions_path):
            await ctx.send("No instructions file found.", delete_after=10)
            return
        await ctx.send(file=discord.File(instructions_path, filename="instructions.txt"))

    @commands.command(name="prompt", description="View, set or clear the prompt for the AI.", aliases=["setprompt", "sp"])
    async def prompt(self, ctx, *, text=None):
        if ctx.author.id != self.bot.owner_id:
            return
        if text is None:
            await ctx.send(f"Current prompt:\n{f'```{self.bot.instructions}```' if self.bot.instructions != '' else 'No prompt is currently set.'}")
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

    @commands.command(name="getdb", description="Sends the bot_data.db file to Discord.")
    async def getdb(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return
        db_path = resource_path("config/bot_data.db")
        if not os.path.exists(db_path):
            await ctx.send("No database file found.", delete_after=10)
            return
        await ctx.send(file=discord.File(db_path, filename="bot_data.db"))

    @commands.command(name="getconfig", description="Sends the current config.yaml file.", aliases=["gc"])
    async def getconfig(self, ctx):
        if ctx.author.id != self.bot.owner_id:
            return
        config_path = resource_path("config/config.yaml")
        if not os.path.exists(config_path):
            await ctx.send("No config file found.", delete_after=10)
            return
        await ctx.send(file=discord.File(config_path, filename="config.yaml"))

    @commands.command(name="setconfig", description="Attach a .yaml file to update the bot config. Bot will restart automatically.")
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
        """Save pending conversations to disk so we can reply after restart."""
        import json

        prefix = self.bot.command_prefix
        pending = {}

        def _is_server_channel(channel_id):
            """Returns True if the channel is a server TextChannel."""
            ch = self.bot.get_channel(int(channel_id))
            import discord as _discord
            return isinstance(ch, _discord.TextChannel)

        # 1. Unanswered messages from history
        for key, history in self.bot.message_history.items():
            if not history:
                continue
            unanswered = []
            for entry in reversed(history):
                if entry["role"] == "user":
                    unanswered.insert(0, entry["content"])
                else:
                    break
            if not unanswered:
                continue
            real_msgs = [m for m in unanswered if not m.startswith(prefix)]
            if not real_msgs:
                continue
            user_id, channel_id = key.split("-")
            if _is_server_channel(channel_id):
                continue
            pending[key] = {
                "user_id": user_id,
                "channel_id": channel_id,
                "content": "\n".join(real_msgs),
                "history": history,
            }

        # 2. Messages sitting in the queue (not yet responded to)
        for channel_id, queue in self.bot.message_queues.items():
            for msg in queue:
                if not msg.content or msg.content.startswith(prefix):
                    continue
                if _is_server_channel(channel_id):
                    continue
                key = f"{msg.author.id}-{channel_id}"
                if key in pending:
                    continue
                history = self.bot.message_history.get(key, [])
                pending[key] = {
                    "user_id": str(msg.author.id),
                    "channel_id": str(channel_id),
                    "content": msg.content,
                    "history": history,
                }

        # 3. Messages in batch buffers (collected but not yet sent to AI)
        for batch_key, batch_data in self.bot.user_message_batches.items():
            msgs = batch_data.get("messages", [])
            if not msgs:
                continue
            combined = "\n".join(m.content for m in msgs if m.content and not m.content.startswith(prefix))
            if not combined:
                continue
            first_msg = msgs[0]
            channel_id = first_msg.channel.id
            if _is_server_channel(channel_id):
                continue
            key = f"{first_msg.author.id}-{channel_id}"
            if key in pending:
                continue
            history = self.bot.message_history.get(key, [])
            pending[key] = {
                "user_id": str(first_msg.author.id),
                "channel_id": str(channel_id),
                "content": combined,
                "history": history,
            }

        path = resource_path("config/pending_messages.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pending, f)
        print(f"[Update] Saved {len(pending)} pending message(s) for post-restart reply.")
        for k, v in pending.items():
            print(f"[Update] → {v['user_id']}: {v['content'][:60]!r}")

    async def _respond_to_user(self, ctx, user):
        """Core logic to find and respond to a single user. Returns (success, reason)."""
        target_channel = None
        recent_msgs = []

        try:
            dm = user.dm_channel or await user.create_dm()
            async for msg in dm.history(limit=20):
                if msg.author.id == user.id:
                    recent_msgs.append(msg)
                elif recent_msgs:
                    break
            if recent_msgs:
                target_channel = dm
        except Exception as e:
            print(f"[Respond] DM error for {user.name}: {e}")

        if not target_channel:
            for channel_id in self.bot.active_channels:
                try:
                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                    msgs = []
                    async for msg in channel.history(limit=50):
                        if msg.author.id == user.id:
                            msgs.append(msg)
                        elif msgs:
                            break
                    if msgs:
                        recent_msgs = msgs
                        target_channel = channel
                        break
                except Exception as e:
                    print(f"[Respond] Channel error: {e}")
                    continue

        if not target_channel or not recent_msgs:
            return False, "no recent messages found"

        if not hasattr(self.bot, "generate_response_and_reply"):
            return False, "bot not ready"

        recent_msgs = list(reversed(recent_msgs))
        combined_content = "\n".join(msg.content for msg in recent_msgs if msg.content)
        last_msg = recent_msgs[-1]

        key = f"{user.id}-{target_channel.id}"
        history = self.bot.message_history.get(key, [])
        if not history or history[-1].get("content") != combined_content:
            history.append({"role": "user", "content": combined_content})
            self.bot.message_history[key] = history

        response = await self.bot.generate_response_and_reply(last_msg, combined_content, history)
        if response:
            self.bot.message_history[key].append({"role": "assistant", "content": response})
            return True, "ok"
        return False, "failed to generate response"

    async def _run_respond(self, ctx, args):
        """Shared logic for ,respond and ,reply."""
        if not args:
            await ctx.send("Usage: `,respond <id>` or `,respond <id1, id2, ...>` or `,respond check`", delete_after=15)
            return

        if args.strip().lower() == "check":
            unreplied = []
            for key, history in self.bot.message_history.items():
                if not history:
                    continue
                if history[-1].get("role") == "user":
                    try:
                        user_id = int(key.split("-")[0])
                        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                        unreplied.append(f"• {user.name} (`{user_id}`)")
                    except Exception:
                        unreplied.append(f"• Unknown (`{key.split('-')[0]}`)")
            if not unreplied:
                await ctx.send("No unreplied conversations.", delete_after=20)
            else:
                await ctx.send("**Unreplied conversations:**\n" + "\n".join(unreplied), delete_after=60)
            return

        raw_ids = [x.strip().strip("<@!>") for x in re.split(r"[,\s]+", args) if x.strip()]
        users = []
        invalid = []
        for raw in raw_ids:
            if not raw.isdigit():
                invalid.append(raw)
                continue
            try:
                user = self.bot.get_user(int(raw)) or await self.bot.fetch_user(int(raw))
                users.append(user)
            except Exception:
                invalid.append(raw)

        if invalid:
            await ctx.send(f"Could not resolve: {', '.join(f'`{i}`' for i in invalid)}", delete_after=10)
        if not users:
            return

        try:
            await ctx.message.delete()
        except Exception:
            pass

        if len(users) == 1:
            status = await ctx.send(f"Generating response for {users[0].name}...", delete_after=30)
            success, reason = await self._respond_to_user(ctx, users[0])
            try:
                await status.delete()
            except Exception:
                pass
            if success:
                await ctx.send(f"Responded to {users[0].name}.", delete_after=5)
            else:
                await ctx.send(f"Failed to respond to {users[0].name}: {reason}.", delete_after=10)
        else:
            status = await ctx.send(f"Responding to {len(users)} users...", delete_after=60)
            results = []
            for user in users:
                success, reason = await self._respond_to_user(ctx, user)
                icon = "✅" if success else "❌"
                results.append(f"{icon} {user.name} (`{user.id}`){'' if success else f' — {reason}'}")
            try:
                await status.delete()
            except Exception:
                pass
            await ctx.send("\n".join(results), delete_after=30)

    @commands.command(name="respond", description="Respond to one or more users by ID. Use 'check' to see unreplied DMs.")
    async def respond(self, ctx, *, args: str = None):
        if ctx.author.id != self.bot.owner_id:
            return
        await self._run_respond(ctx, args)

    @commands.command(name="reply", description="Alias for ,respond — respond to one or more users by ID.")
    async def reply_cmd(self, ctx, *, args: str = None):
        if ctx.author.id != self.bot.owner_id:
            return
        await self._run_respond(ctx, args)

    @commands.command(name="config", description="View or edit config values. Use dot notation for nested keys.")
    async def config_cmd(self, ctx, key: str = None, *, value: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        config = load_config()

        if key is None:
            bot_cfg = config["bot"]
            tts = bot_cfg.get("tts") or {}
            fr = bot_cfg.get("friend_requests") or {}
            mood = bot_cfg.get("mood") or {}
            late = bot_cfg.get("late_reply") or {}

            status = bot_cfg.get("status") or {}
            notif = config.get("notifications") or {}

            wait_times = bot_cfg.get("batch_wait_times") or []
            wt_str = "  ".join(f"{w['time']}s({w['weight']})" for w in wait_times)

            mood_list = ", ".join(mood.get("moods", {}).keys())

            lines = [
                "```",
                "⚙️  Bot Config",
                "─────────────────────────────",
                "  🔧  General",
                f"  prefix                {bot_cfg.get('prefix')}",
                f"  trigger               {bot_cfg.get('trigger')}",
                f"  owner_id              {bot_cfg.get('owner_id')}",
                f"  priority_prefix       {bot_cfg.get('priority_prefix')}",
                "─────────────────────────────",
                "  💬  Responses",
                f"  allow_dm              {bot_cfg.get('allow_dm')}",
                f"  allow_gc              {bot_cfg.get('allow_gc')}",
                f"  allow_server          {bot_cfg.get('allow_server', True)}",
                f"  hold_conversation     {bot_cfg.get('hold_conversation')}",
                f"  realistic_typing      {bot_cfg.get('realistic_typing')}",
                f"  reply_ping            {bot_cfg.get('reply_ping')}",
                f"  disable_mentions      {bot_cfg.get('disable_mentions')}",
                f"  batch_messages        {bot_cfg.get('batch_messages')}",
                f"  batch_wait_times      {wt_str}",
                "─────────────────────────────",
                "  🎭  Behaviour",
                f"  ignore_chance         {bot_cfg.get('ignore_chance')}",
                f"  typo_chance           {bot_cfg.get('typo_chance')}",
                f"  anti_age_ban          {bot_cfg.get('anti_age_ban')}",
                "─────────────────────────────",
                "  🤖  Models",
                (lambda v: f"  groq_models           {', '.join(v) if isinstance(v, list) else str(v)}")(bot_cfg.get('groq_models', [])),
                f"  groq_image_model      {bot_cfg.get('groq_image_model')}",
                f"  groq_whisper_model    {bot_cfg.get('groq_whisper_model')}",
                "─────────────────────────────",
                "  🔊  TTS",
                f"  tts.enabled           {tts.get('enabled')}",
                f"  tts.voice             {tts.get('voice')}",
                (lambda v: f"  tts.tones             {', '.join(v) if isinstance(v, list) else str(v)}")(tts.get('tones', [])),
                "─────────────────────────────",
                "  😶  Mood",
                f"  mood.enabled          {mood.get('enabled')}",
                f"  mood.shift_interval_min  {mood.get('shift_interval_min')}",
                f"  mood.shift_interval_max  {mood.get('shift_interval_max')}",
                f"  mood.moods            {mood_list}",
                "─────────────────────────────",
                "  🕐  Status",
                f"  status.enabled        {status.get('enabled')}",
                f"  status.change_interval_min  {status.get('change_interval_min')}",
                f"  status.change_interval_max  {status.get('change_interval_max')}",
                (lambda v: f"  status.statuses       {', '.join(v) if isinstance(v, list) else str(v)}")(status.get('statuses', [])),
                "─────────────────────────────",
                "  💬  Late Reply",
                f"  late_reply.enabled    {late.get('enabled')}",
                f"  late_reply.threshold  {late.get('threshold')}",
                "─────────────────────────────",
                "  👥  Friend Requests",
                f"  friend_requests.enabled      {fr.get('enabled')}",
                f"  friend_requests.accept_delay {fr.get('accept_delay')}",
                "─────────────────────────────",
                "  🔔  Notifications",
                f"  error_webhook         {'set' if notif.get('error_webhook') else 'not set'}",
                f"  ratelimit_notifications  {notif.get('ratelimit_notifications')}",
                "```",
                f"Use `,config <key> <value>` to edit. Example: `,config tts.voice diana`",
            ]
            await ctx.send("\n".join(lines), delete_after=60)
            return

        keys = key.split(".")

        LIST_KEYS = {"groq_models", "tones", "statuses"}

        def coerce(v, existing=None):
            if v.lower() == "true": return True
            if v.lower() == "false": return False
            try: return int(v)
            except ValueError: pass
            try: return float(v)
            except ValueError: pass
            # If the existing value is a list, parse comma-separated input back into a list
            if isinstance(existing, list) or (keys[-1] in LIST_KEYS):
                return [item.strip() for item in v.split(",") if item.strip()]
            return v

        try:
            node = config["bot"]
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
            node[final_key] = coerce(value, old_val)
            self.save_config(config)
            await ctx.send(f"`{key}` updated: `{old_val}` → `{node[final_key]}`", delete_after=15)
        except Exception as e:
            await ctx.send(f"Error: {e}", delete_after=10)


    @commands.command(name="mood", description="View or set the bot's current mood.")
    async def mood_cmd(self, ctx, *, mood_name: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        from utils.mood import get_mood, shift_mood, get_mood_prompt
        config = load_config()
        available_moods = list(config["bot"]["mood"]["moods"].keys())

        if mood_name is None:
            current = get_mood()
            mood_list = ", ".join(f"`{m}`" for m in available_moods)
            await ctx.send(
                f"Current mood: `{current}`\nAvailable moods: {mood_list}",
                delete_after=20
            )
            return

        mood_name = mood_name.lower().strip()
        if mood_name not in available_moods:
            mood_list = ", ".join(f"`{m}`" for m in available_moods)
            await ctx.send(
                f"Unknown mood `{mood_name}`. Available: {mood_list}",
                delete_after=10
            )
            return

        from utils.mood import current_mood
        import utils.mood as mood_module
        mood_module.current_mood = mood_name
        await ctx.send(f"Mood set to `{mood_name}`.", delete_after=10)

    @commands.command(name="pfp", description="Change the bot's profile picture. Attach an image or provide a URL.")
    async def pfp(self, ctx, url: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        image_data = None

        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if not attachment.content_type or not attachment.content_type.startswith("image/"):
                await ctx.send("Please attach a valid image file.", delete_after=10)
                return
            image_data = await attachment.read()
        elif url:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            await ctx.send(f"Failed to fetch image (status {resp.status}).", delete_after=10)
                            return
                        image_data = await resp.read()
            except Exception as e:
                await ctx.send(f"Error fetching image: {e}", delete_after=10)
                return
        else:
            await ctx.send("Please attach an image or provide a URL.", delete_after=10)
            return

        try:
            await self.bot.user.edit(avatar=image_data)
            await ctx.send("Profile picture updated!", delete_after=10)
        except discord.errors.HTTPException as e:
            if "Too many users" in str(e) or "rate" in str(e).lower():
                await ctx.send("You're being rate limited on avatar changes. Try again later.", delete_after=15)
            else:
                await ctx.send(f"Failed to update avatar: {e}", delete_after=10)
        except Exception as e:
            await ctx.send(f"Error: {e}", delete_after=10)

    @commands.command(name="bio", description="Change the bot's profile bio.")
    async def bio(self, ctx, *, text: str = None):
        if ctx.author.id != self.bot.owner_id:
            return
        try:
            await self.bot.user.edit(bio=text or "")
            if text:
                await ctx.send(f"Bio updated to: `{text}`", delete_after=10)
            else:
                await ctx.send("Bio cleared.", delete_after=10)
        except Exception as e:
            await ctx.send(f"Error: {e}", delete_after=10)


    @commands.command(name="status", description="Change the bot's custom status.")
    async def status(self, ctx, emoji: str = None, *, text: str = None):
        if ctx.author.id != self.bot.owner_id:
            return
        try:
            await self.bot.change_presence(
                activity=discord.CustomActivity(name=text or "", emoji=emoji or None)
            )
            if text or emoji:
                await ctx.send(f"Status updated.", delete_after=10)
            else:
                await ctx.send("Status cleared.", delete_after=10)
        except Exception as e:
            await ctx.send(f"Error: {e}", delete_after=10)


    async def _connect_and_keep_alive(self, target: discord.VoiceChannel):
        """Connect to a voice channel muted/deafened and keep alive."""
        existing = target.guild.voice_client
        if existing:
            await existing.disconnect(force=True)

        vc = await target.connect(self_mute=True, self_deaf=True)

        # discord.py-self drops idle voice connections — keep alive by hooking the websocket
        # The ws keep_alive loop handles heartbeats but we need to block disconnection
        # by holding a reference and periodically checking/re-opening the connection
        async def _guard(channel, voice_client):
            # Flag to prevent the reconnect loop from firing on intentional ,leave
            voice_client._keep_alive_guard = True
            while getattr(voice_client, '_keep_alive_guard', False):
                await asyncio.sleep(20)
                vc_now = channel.guild.voice_client
                if vc_now is None and getattr(voice_client, '_keep_alive_guard', False):
                    try:
                        new_vc = await channel.connect(self_mute=True, self_deaf=True)
                        new_vc._keep_alive_guard = True
                        voice_client = new_vc
                        log_system(f"Rejoined voice channel: {channel.name}")
                    except Exception as e:
                        log_error("Voice Keep-Alive", str(e))

        asyncio.create_task(_guard(target, vc))
        return vc

    @commands.command(name="join", description="Join a voice channel. Usage: ,join <channel_id>, ,join <guild_id> <channel_id>, or ,join <discord_link>")
    async def join(self, ctx, *, args: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        if not args:
            await ctx.send("Usage: `,join <channel_id>` or `,join <guild_id> <channel_id>` or `,join https://discord.com/channels/guild_id/channel_id`", delete_after=15)
            return

        guild_id_parsed = None
        channel_id_parsed = None

        link_match = re.match(r"https?://discord\.com/channels/(\d+)/(\d+)", args.strip())
        if link_match:
            guild_id_parsed = int(link_match.group(1))
            channel_id_parsed = int(link_match.group(2))
        else:
            parts = args.strip().split()
            if len(parts) == 1 and parts[0].isdigit():
                channel_id_parsed = int(parts[0])
            elif len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                guild_id_parsed = int(parts[0])
                channel_id_parsed = int(parts[1])
            else:
                await ctx.send("Invalid input. Use a channel ID, `guild_id channel_id`, or a Discord channel link.", delete_after=10)
                return

        if guild_id_parsed:
            guild = self.bot.get_guild(guild_id_parsed)
            if not guild:
                await ctx.send(f"Guild `{guild_id_parsed}` not found.", delete_after=10)
                return
            target = guild.get_channel(channel_id_parsed)
        else:
            target = self.bot.get_channel(channel_id_parsed)

        if not isinstance(target, discord.VoiceChannel):
            await ctx.send("Channel not found or is not a voice channel.", delete_after=10)
            return

        try:
            status = await ctx.send(f"Joining **{target.name}** in **{target.guild.name}**...", delete_after=30)
            await self._connect_and_keep_alive(target)
            await status.delete()
            await ctx.send(f"Joined **{target.name}** in **{target.guild.name}** (muted & deafened).", delete_after=10)
        except Exception as e:
            await ctx.send(f"Error joining voice channel: {e}", delete_after=10)

    @commands.command(name="autojoin", description="Set a voice channel to auto-join on startup. Usage: ,autojoin <channel_id/link> or ,autojoin off")
    async def autojoin(self, ctx, *, args: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        config = load_config()

        if not args or args.strip().lower() == "off":
            config["bot"]["autojoin_channel"] = None
            self.save_config(config)
            await ctx.send("Auto-join disabled.", delete_after=10)
            return

        guild_id_parsed = None
        channel_id_parsed = None

        link_match = re.match(r"https?://discord\.com/channels/(\d+)/(\d+)", args.strip())
        if link_match:
            guild_id_parsed = int(link_match.group(1))
            channel_id_parsed = int(link_match.group(2))
        else:
            parts = args.strip().split()
            if len(parts) == 1 and parts[0].isdigit():
                channel_id_parsed = int(parts[0])
            elif len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                guild_id_parsed = int(parts[0])
                channel_id_parsed = int(parts[1])
            else:
                await ctx.send("Invalid input. Use a channel ID, `guild_id channel_id`, or a Discord channel link.", delete_after=10)
                return

        target = None
        if guild_id_parsed:
            guild = self.bot.get_guild(guild_id_parsed)
            if guild:
                target = guild.get_channel(channel_id_parsed)
        else:
            target = self.bot.get_channel(channel_id_parsed)

        if not isinstance(target, discord.VoiceChannel):
            await ctx.send("Channel not found or is not a voice channel.", delete_after=10)
            return

        config["bot"]["autojoin_channel"] = {"guild_id": target.guild.id, "channel_id": target.id}
        self.save_config(config)
        await ctx.send(f"Auto-join set to **{target.name}** in **{target.guild.name}**. Will join on next startup.", delete_after=15)

    @commands.command(name="leave", description="Leave a voice channel. Usage: ,leave or ,leave <guild_id>")
    async def leave(self, ctx, guild_id: int = None):
        if ctx.author.id != self.bot.owner_id:
            return

        if guild_id:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                await ctx.send(f"Guild `{guild_id}` not found.", delete_after=10)
                return
            vc = guild.voice_client
        else:
            vc = ctx.guild.voice_client if ctx.guild else None

        if vc:
            channel_name = vc.channel.name
            guild_name = vc.guild.name
            vc._keep_alive_guard = False  # Stop the keep-alive guard loop
            await vc.disconnect(force=True)
            await ctx.send(f"Left **{channel_name}** in **{guild_name}**.", delete_after=10)
        else:
            await ctx.send("Not in a voice channel.", delete_after=10)


    @commands.command(name="image", description="Manage bot pictures. Subcommands: upload, ls, download [name]")
    async def image(self, ctx, action: str = "ls", *, name: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        from utils.helpers import resource_path
        folder = resource_path("config/pictures")
        os.makedirs(folder, exist_ok=True)
        exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

        if action in ("ls", "list"):
            files = sorted([f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in exts])
            if not files:
                await ctx.send("No images in the folder yet. Use `,image upload` with an attachment.", delete_after=15)
                return
            await ctx.send(f"🖼️  **{len(files)} image(s):**", delete_after=120)
            for f in files:
                path = os.path.join(folder, f)
                try:
                    await ctx.send(f"`{f}`", file=discord.File(path), delete_after=120)
                except Exception as e:
                    await ctx.send(f"`{f}` (could not send: {e})", delete_after=60)

        elif action == "upload":
            if not ctx.message.attachments:
                await ctx.send("Attach an image to upload.", delete_after=10)
                return

            saved = []
            status_msg = await ctx.send("⏳ Saving image(s)...", delete_after=30)

            for att in ctx.message.attachments:
                ext = os.path.splitext(att.filename)[1].lower()
                if ext not in exts:
                    continue
                data = await att.read()
                dest = os.path.join(folder, att.filename)
                with open(dest, "wb") as f:
                    f.write(data)
                saved.append(att.filename)

            await status_msg.delete()

            if not saved:
                await ctx.send("No valid image attachments found.", delete_after=10)
                return

            names = ", ".join(f"`{f}`" for f in saved)
            await ctx.send(f"Saved {len(saved)} image(s): {names}", delete_after=30)

        elif action == "download":
            if not name:
                await ctx.send("Provide a filename. Use `,image ls` to see available images.", delete_after=10)
                return
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                # Try partial match
                files = [f for f in os.listdir(folder) if name.lower() in f.lower()]
                if len(files) == 1:
                    path = os.path.join(folder, files[0])
                elif len(files) > 1:
                    await ctx.send(f"Multiple matches: {', '.join(files)}. Be more specific.", delete_after=15)
                    return
                else:
                    await ctx.send(f"Image `{name}` not found.", delete_after=10)
                    return
            await ctx.send(file=discord.File(path), delete_after=60)

        elif action == "delete":
            if not name:
                await ctx.send("Provide a filename to delete.", delete_after=10)
                return
            path = os.path.join(folder, name)
            if os.path.exists(path):
                os.remove(path)
                await ctx.send(f"Deleted `{name}`.", delete_after=10)
            else:
                await ctx.send(f"Image `{name}` not found.", delete_after=10)

        else:
            await ctx.send("Usage: `,image ls` | `,image upload` | `,image download <name>` | `,image delete <name>`", delete_after=15)


async def setup(bot):
    await bot.add_cog(Management(bot))
