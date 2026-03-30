import discord
import os
import sys
import subprocess
import yaml
import re
import asyncio
from curl_cffi.requests import AsyncSession

from discord.ext import commands
from utils.helpers import load_instructions, load_config, resource_path
from utils.logger import log_system, log_error
from utils.db import (
    add_ignored_user,
    remove_ignored_user,
    remove_channel,
    add_channel,
    get_pending_nudges,
    mark_responded,
    add_picture_description,
    get_picture_description,
    delete_picture_db,
    rename_picture_db,
    clear_all_pictures_db,
    get_leaderboard,
)
from utils.memory import set_persona, clear_persona, get_persona


class Management(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_before_invoke(self, ctx):
        # Only add the human-like delay for non-owner users.
        # All management commands are owner-only anyway, so this is a no-op guard
        # to avoid making the owner wait on every command.
        if ctx.author.id != self.bot.owner_id:
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

    @commands.command(name="pauseuser", description="Stop the bot from responding to a specific user.")
    async def pauseuser(self, ctx, user: discord.User):
        if ctx.author.id != self.bot.owner_id:
            return
        if not hasattr(self.bot, "paused_users"):
            self.bot.paused_users = set()
        if user.id in self.bot.paused_users:
            await ctx.send(f"⚠️ {user.name} is already paused.")
        else:
            self.bot.paused_users.add(user.id)
            await ctx.send(f"🔇 Paused responses for **{user.name}**. The bot will no longer reply to them.")

    @commands.command(name="unpauseuser", description="Resume responding to a previously paused user.")
    async def unpauseuser(self, ctx, user: discord.User):
        if ctx.author.id != self.bot.owner_id:
            return
        if not hasattr(self.bot, "paused_users"):
            self.bot.paused_users = set()
        if user.id not in self.bot.paused_users:
            await ctx.send(f"⚠️ {user.name} is not paused.")
        else:
            self.bot.paused_users.discard(user.id)
            await ctx.send(f"🔊 Resumed responses for **{user.name}**.")

    @commands.command(
        name="persona",
        description=(
            "Set or clear a per-user persona override. "
            "Usage: ,persona @user <instructions>  |  ,persona @user off  |  ,persona @user show"
        ),
    )
    async def persona(self, ctx, user: discord.User, *, args: str = None):
        """Attach a custom tone/personality instruction to a specific user.

        Examples:
            ,persona @jake  Be very formal and call him 'sir' in every reply.
            ,persona @sara  off
            ,persona @jake  show
        """
        if ctx.author.id != self.bot.owner_id:
            return

        if not args or args.strip().lower() in ("off", "clear", "remove", "none"):
            clear_persona(user.id)
            # Also invalidate the in-memory cache so the next reply picks up the change
            if hasattr(self.bot, "_memory_cache") and user.id in self.bot._memory_cache:
                self.bot._memory_cache[user.id].pop("__persona__", None)
            await ctx.send(f"🗑️ Persona cleared for **{user.name}**. They will get the default instructions.")
            return

        if args.strip().lower() == "show":
            current = get_persona(user.id)
            if current:
                await ctx.send(f"🎭 Persona for **{user.name}**:\n> {current}")
            else:
                await ctx.send(f"ℹ️ No custom persona set for **{user.name}**.")
            return

        set_persona(user.id, args.strip())
        # Bust the in-memory cache so the next reply picks up the new persona immediately
        if hasattr(self.bot, "_memory_cache") and user.id in self.bot._memory_cache:
            self.bot._memory_cache[user.id]["__persona__"] = args.strip()
        await ctx.send(
            f"🎭 Persona set for **{user.name}**:\n> {args.strip()}\n"
            f"The bot will use these instructions when replying to them from now on."
        )

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

    @commands.command(name="leaderboard", aliases=["lb", "top"], description="Show the users who've talked to the bot the most.")
    async def leaderboard(self, ctx, *, filter_arg: str = None):
        if ctx.author.id != self.bot.owner_id:
            return

        # --- Parse optional time filter: 1h, 6h, 24h, 1d, 3d, 7d, 1w, 30d, 1m ---
        import re as _re
        import time as _time
        from datetime import datetime

        since_ts = None
        filter_label = "all time"
        if filter_arg:
            m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwm])", filter_arg.strip().lower())
            if m:
                amount, unit = float(m.group(1)), m.group(2)
                seconds_map = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000}
                since_ts = _time.time() - amount * seconds_map[unit]
                unit_names = {"h": "hour", "d": "day", "w": "week", "m": "month"}
                n = int(amount) if amount == int(amount) else amount
                filter_label = f"last {n} {unit_names[unit]}{'s' if n != 1 else ''}"
            else:
                await ctx.send(
                    "Invalid filter. Examples: `,leaderboard 24h` · `,leaderboard 3d` · `,leaderboard 1w`",
                    delete_after=15,
                )
                return

        rows = get_leaderboard(limit=50, since=since_ts)
        if not rows:
            await ctx.send(f"No conversations recorded ({filter_label}).", delete_after=15)
            return

        PER_PAGE = 5
        total_pages = (len(rows) + PER_PAGE - 1) // PER_PAGE

        def build_page(page: int) -> str:
            start = page * PER_PAGE
            chunk = rows[start:start + PER_PAGE]
            medal_emojis = ["🥇", "🥈", "🥉"]

            header = f"**📊 conversations** ・ {filter_label}\n**page {page + 1} / {total_pages}**\n─────────────────────"
            entry_lines = []
            for i, row in enumerate(chunk):
                rank_n = start + i
                rank_prefix = medal_emojis[rank_n] if rank_n < 3 else f"`#{rank_n + 1}`"
                first_seen = datetime.fromtimestamp(row["first_seen"]).strftime("%d %b %Y")
                msg_count = row["message_count"]
                msg_label = f"{msg_count} msg{'s' if msg_count != 1 else ''}"
                entry_lines.append(f"{rank_prefix} **{row['username']}**\n⠀⠀⠀`{msg_label}` · since {first_seen}")
            footer = "─────────────────────"
            return header + "\n" + "\n\n".join(entry_lines) + "\n" + footer

        current_page = 0
        msg = await ctx.send(build_page(current_page), delete_after=120)

        if total_pages == 1:
            return

        await msg.add_reaction("◀")
        await msg.add_reaction("▶")

        def check(reaction, user):
            return (
                user.id == self.bot.owner_id
                and str(reaction.emoji) in ("◀", "▶")
                and reaction.message.id == msg.id
            )

        import asyncio
        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            except asyncio.TimeoutError:
                # Don't call clear_reactions — saves an API call, message expires via delete_after
                break

            new_page = current_page
            if str(reaction.emoji) == "▶" and current_page < total_pages - 1:
                new_page = current_page + 1
            elif str(reaction.emoji) == "◀" and current_page > 0:
                new_page = current_page - 1

            try:
                await msg.remove_reaction(reaction.emoji, user)
            except Exception:
                pass

            if new_page != current_page:
                current_page = new_page
                try:
                    await msg.delete()
                except Exception:
                    pass
                msg = await ctx.send(build_page(current_page), delete_after=120)
                await msg.add_reaction("◀")
                await msg.add_reaction("▶")

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
            import sys as _sys
            cogs_dir = os.path.join(getattr(_sys, "_MEIPASS", os.path.abspath(".")), "cogs")
            if not os.path.exists(cogs_dir):
                await ctx.send("No cogs directory found.", delete_after=10)
                return
            for filename in os.listdir(cogs_dir):
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
            import atexit
            msg = await ctx.send("Restarting...")
            print("Restarting bot...")
            if getattr(sys, "frozen", False):
                exe_path = sys.executable
                # Register relaunch BEFORE closing — fires after the lock file is released
                atexit.register(lambda: os.startfile(exe_path))
            else:
                python = sys.executable
                args = [python] + sys.argv
                # Register relaunch BEFORE closing — fires after the lock file is released
                atexit.register(lambda: subprocess.Popen(args))
            try:
                await msg.delete()
            except Exception:
                pass
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
                async with AsyncSession(impersonate="chrome") as _s:
                    _r = await _s.get(
                        "https://api.github.com/repos/miiazertyy/Discord-LLM-Selfbot/releases/latest",
                        timeout=10
                    )
                    if _r.status_code == 200:
                        latest = _r.json().get("tag_name", "unknown")
            except Exception:
                pass
            version_str = latest if latest else "latest"
            msg = await ctx.send(f"Updating to {version_str}... brb")

        self._save_pending_messages()

        try:
            await msg.edit(content=f"Updating to {version_str}... launching updater, brb in a sec")
        except Exception:
            pass

        await asyncio.sleep(1)

        # Resolve updater path relative to this file so it works regardless of cwd
        _base = os.path.dirname(os.path.abspath(__file__))
        if sys.platform == "win32":
            updater_path = os.path.join(_base, "updater.bat")
            subprocess.Popen(
                ["cmd", "/c", updater_path, source],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                close_fds=True,
            )
        else:
            updater_path = os.path.join(_base, "updater.sh")
            subprocess.Popen(["bash", updater_path, source], start_new_session=True)

        try:
            await msg.delete()
        except Exception:
            pass

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

            # Try to get the actual last message id from the channel cache
            last_message_id = None
            try:
                ch = self.bot.get_channel(int(channel_id))
                if ch and hasattr(ch, '_state'):
                    # Pull from internal message cache
                    for cached_msg in reversed(list(ch._state._messages)):
                        if str(cached_msg.author.id) == user_id:
                            last_message_id = cached_msg.id
                            break
            except Exception:
                pass

            pending[key] = {
                "user_id": user_id,
                "channel_id": channel_id,
                "content": "\n".join(real_msgs),
                "history": history,
                "last_message_id": last_message_id,
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
                    "last_message_id": msg.id,
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
            last_msg = msgs[-1]
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
                "last_message_id": last_msg.id,
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

        # --- Fast path: check in-memory message history first ---
        matching_key = None
        for key in self.bot.message_history:
            if key.startswith(f"{user.id}-"):
                matching_key = key
                break

        if matching_key:
            channel_id = int(matching_key.split("-")[1])
            try:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = next((pc for pc in self.bot.private_channels if pc.id == channel_id), None)
                if channel is None:
                    channel = await user.create_dm()
                async for msg in channel.history(limit=5):
                    if msg.author.id == user.id:
                        recent_msgs.append(msg)
                        break
                if recent_msgs:
                    target_channel = channel
            except Exception as e:
                print(f"[Respond] Fast-path channel error for {user.name}: {e}")

        # --- Slow path: DM history scan (only if fast path missed) ---
        if not target_channel:
            try:
                dm = user.dm_channel or await user.create_dm()
                selfbot_id = getattr(self.bot, "selfbot_id", None) or self.bot.user.id
                # Collect all consecutive user messages at the top of history (unread batch)
                async for msg in dm.history(limit=15):
                    if msg.author.id == user.id:
                        recent_msgs.append(msg)
                    elif msg.author.id == selfbot_id:
                        # Hit the bot's last reply — stop here, everything above is unread
                        break
                if recent_msgs:
                    target_channel = dm
            except Exception as e:
                print(f"[Respond] DM error for {user.name}: {e}")

        # --- Fallback: scan active channels (limited to 3 to cap API calls) ---
        if not target_channel:
            checked = 0
            for channel_id in self.bot.active_channels:
                if checked >= 3:
                    break
                checked += 1  # increment before the try so exceptions don't skip the count
                try:
                    channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                    msgs = []
                    async for msg in channel.history(limit=20):
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

        key = f"{user.id}-{target_channel.id}"
        history = self.bot.message_history.get(key, [])
        if not history or history[-1].get("content") != combined_content:
            history.append({"role": "user", "content": combined_content})
            self.bot.message_history[key] = history

        # Call the AI directly and send via target_channel.send() so we never
        # depend on a stale message.channel from a fetched historical message object.
        from utils.ai import generate_response as _gen
        from utils.split_response import split_response as _split
        from utils.memory import format_memory_for_prompt, get_memory, get_persona
        from utils.mood import get_mood_prompt
        import random as _random

        uid = user.id
        if uid not in self.bot._memory_cache:
            self.bot._memory_cache[uid] = get_memory(uid)
        memory_block = format_memory_for_prompt(self.bot._memory_cache[uid])
        _mood_cfg = load_config()["bot"]["mood"]
        mood_block = f"\n\n[Right now: {get_mood_prompt()}]" if _mood_cfg.get("enabled", True) else ""
        enriched = self.bot.instructions + mood_block + memory_block
        _persona = get_persona(uid)
        if _persona:
            enriched += f"\n\n[PERSONA OVERRIDE FOR THIS USER: {_persona} Maintain this persona for the entirety of this conversation.]"

        try:
            response = await _gen(combined_content, enriched, history)
        except Exception as e:
            log_error("Respond AI", str(e))
            return False, f"AI error: {e}"

        if not response:
            return False, "AI returned no response"

        chunks = _split(response)
        if len(chunks) > 3:
            chunks = chunks[:3]

        try:
            for i, chunk in enumerate(chunks):
                if i > 0:
                    await asyncio.sleep(_random.uniform(1.5, 3.0))
                await target_channel.send(chunk)
        except discord.Forbidden:
            return False, "403 Forbidden — DMs closed"
        except Exception as e:
            log_error("Respond Send", str(e))
            return False, str(e)

        self.bot.message_history[key].append({"role": "assistant", "content": response})
        return True, "ok"

    async def _get_unreplied_users(self):
        """Return list of (user, snippet, msg_count) for all unreplied conversations.

        Two-pass approach so it works even after a restart (when message_history is empty):

        Pass 1 — in-memory history (fast, works mid-session).
            Any conversation whose last history entry is a user message counts as unreplied.

        Pass 2 — live DM channel scan (catches post-restart gaps).
            Walks bot.private_channels and fetches the last few messages of each DM.
            If the most recent message is from the other person (not the bot) and they
            are not already covered by pass 1, they get added to the results.
        """
        results = []
        seen_user_ids = set()

        # --- Pass 1: in-memory message_history ---
        for key, history in self.bot.message_history.items():
            if not history:
                continue
            if history[-1].get("role") != "user":
                continue
            try:
                user_id = int(key.split("-")[0])
                if user_id in seen_user_ids:
                    continue
                seen_user_ids.add(user_id)
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                pending_msgs = []
                for entry in reversed(history):
                    if entry["role"] == "user":
                        pending_msgs.insert(0, entry["content"])
                    else:
                        break
                last_msg = pending_msgs[-1] if pending_msgs else ""
                snippet = (last_msg[:60] + "\u2026") if len(last_msg) > 60 else last_msg
                results.append((user, snippet, len(pending_msgs)))
            except Exception:
                pass

        # --- Pass 2: live DM scan (catches anything missed after restart) ---
        # Strategy: use Discord's cached last_message_id to pre-filter channels
        # with zero API calls, then only fetch history for the ones that look
        # unreplied. This keeps the total request count very low even with many DMs.
        try:
            selfbot_id = getattr(self.bot, "selfbot_id", None) or self.bot.user.id
            for channel in self.bot.private_channels:
                if not isinstance(channel, discord.DMChannel):
                    continue
                other = channel.recipient
                if other is None:
                    continue
                if other.id in seen_user_ids:
                    continue

                # --- Zero-API pre-filter using the internal message cache ---
                # discord.py-self keeps recently seen messages in _state._messages.
                # If the cached last message for this channel is from the bot,
                # we can skip it entirely without hitting the API.
                cached_last = None
                if channel.last_message_id:
                    cached_last = channel._state._get_message(channel.last_message_id)
                if cached_last is not None:
                    if cached_last.author.id == selfbot_id:
                        continue  # Bot already replied — skip, no API call needed

                # --- API fetch (only reached if cache says unreplied or is cold) ---
                try:
                    last_msgs = [m async for m in channel.history(limit=10)]
                    if not last_msgs:
                        continue
                    last_from_other = next(
                        (m for m in last_msgs if m.author.id != selfbot_id), None
                    )
                    last_from_bot = next(
                        (m for m in last_msgs if m.author.id == selfbot_id), None
                    )
                    if last_from_other is None:
                        continue
                    if last_from_bot and last_from_bot.created_at > last_from_other.created_at:
                        continue
                    pending_count = sum(
                        1 for m in last_msgs
                        if m.author.id != selfbot_id
                        and (last_from_bot is None or m.created_at > last_from_bot.created_at)
                    )
                    snippet_text = last_from_other.content or "[attachment]"
                    snippet = (snippet_text[:60] + "\u2026") if len(snippet_text) > 60 else snippet_text
                    seen_user_ids.add(other.id)
                    results.append((other, snippet, max(pending_count, 1)))
                except Exception:
                    continue
        except Exception:
            pass

        return results

    async def _run_respond(self, ctx, args):
        """Shared logic for ,respond and ,reply."""
        if not args:
            await ctx.send(
                "Usage: `,respond <id>` \u00b7 `,respond <id1, id2>` \u00b7 `,respond check` \u00b7 `,respond all`",
                delete_after=15,
            )
            return

        keyword = args.strip().lower()

        if keyword == "check":
            status_msg = await ctx.send("🔍 Checking for unreplied conversations...", delete_after=60)
            unreplied = await self._get_unreplied_users()
            unreplied = [(u, s, c) for u, s, c in unreplied if u.id != 643945264868098049]
            if not unreplied:
                try:
                    await status_msg.edit(content="No unreplied conversations.")
                except Exception:
                    await ctx.send("No unreplied conversations.", delete_after=20)
            else:
                lines_out = []
                for user, snippet, msg_count in unreplied:
                    count_label = f" ({msg_count} msg{'s' if msg_count > 1 else ''})" if msg_count > 1 else ""
                    lines_out.append(f"\u2022 **{user.name}**{count_label} \u2014 `{snippet}`")
                result_text = "**Unreplied conversations:**\n" + "\n".join(lines_out)
                try:
                    await status_msg.edit(content=result_text)
                except Exception:
                    await ctx.send(result_text, delete_after=60)
            return

        if keyword == "all":
            status_msg = await ctx.send("🔍 Checking for unreplied conversations...", delete_after=120)
            unreplied = await self._get_unreplied_users()
            if not unreplied:
                try:
                    await status_msg.edit(content="No unreplied conversations.")
                except Exception:
                    pass
                return
            ignored = set(getattr(self.bot, "ignore_users", []))
            users = [u for u, _, _ in unreplied if u.id != 643945264868098049 and u.id not in ignored]
            if not users:
                try:
                    await status_msg.edit(content="No unreplied conversations.")
                except Exception:
                    pass
                return
            try:
                await ctx.message.delete()
            except Exception:
                pass

            async def _dm_owner_status(text: str):
                try:
                    owner = self.bot.get_user(self.bot.owner_id) or await self.bot.fetch_user(self.bot.owner_id)
                    dm = owner.dm_channel or await owner.create_dm()
                    await dm.send(text)
                except Exception:
                    pass

            try:
                await status_msg.edit(content=f"⏳ Replying to {len(users)} user(s)... (0/{len(users)})")
            except Exception:
                pass

            results_out = []
            for i, user in enumerate(users, 1):
                try:
                    await status_msg.edit(content=f"⏳ Replying to {len(users)} user(s)... ({i}/{len(users)}) — **{user.name}**")
                except Exception:
                    pass
                try:
                    success, reason = await self._respond_to_user(ctx, user)
                    icon = "✅" if success else "❌"
                    results_out.append(f"{icon} **{user.name}**{'' if success else f' — {reason}'}")
                except Exception as e:
                    results_out.append(f"❌ **{user.name}** — error: {e}")

            final_text = f"✅ Done — {len(users)} user(s):\n" + "\n".join(results_out)
            try:
                if len(final_text) <= 1900:
                    await status_msg.edit(content=final_text)
                else:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                    chunks = []
                    current = f"✅ Done — {len(users)} user(s):"
                    for line in results_out:
                        if len(current) + len(line) + 1 > 1900:
                            chunks.append(current)
                            current = line
                        else:
                            current += "\n" + line
                    if current:
                        chunks.append(current)
                    for chunk in chunks:
                        await _dm_owner_status(chunk)
            except Exception:
                await _dm_owner_status(final_text)
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

        async def _dm_owner(text: str):
            try:
                owner = self.bot.get_user(self.bot.owner_id) or await self.bot.fetch_user(self.bot.owner_id)
                dm = owner.dm_channel or await owner.create_dm()
                await dm.send(text, delete_after=60)
            except Exception:
                pass

        if len(users) == 1:
            try:
                await ctx.message.delete()
            except Exception:
                pass
            status_msg = await ctx.send(f"⏳ Replying to **{users[0].name}**...", delete_after=60)
            success, reason = await self._respond_to_user(ctx, users[0])
            result_text = f"✅ Replied to **{users[0].name}**." if success else f"❌ Couldn't reply to **{users[0].name}**: {reason}."
            try:
                await status_msg.edit(content=result_text)
            except Exception:
                await _dm_owner(result_text)
        else:
            names = ", ".join(f"**{u.name}**" for u in users)
            status_msg = await ctx.send(f"⏳ Replying to {len(users)} users... (0/{len(users)})", delete_after=120)
            results = []
            for i, user in enumerate(users, 1):
                try:
                    await status_msg.edit(content=f"⏳ Replying to {len(users)} users... ({i}/{len(users)}) — **{user.name}**")
                except Exception:
                    pass
                success, reason = await self._respond_to_user(ctx, user)
                icon = "✅" if success else "❌"
                results.append(f"{icon} {user.name} (`{user.id}`){'' if success else f' — {reason}'}")
            final_text = f"✅ Done — {len(users)} user(s):\n" + "\n".join(results)
            try:
                await status_msg.edit(content=final_text if len(final_text) <= 1900 else f"✅ Done — {len(users)} user(s). See DM for details.")
            except Exception:
                pass
            if len(final_text) > 1900:
                await _dm_owner(final_text)

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

        # Sync live bot state into the loaded config so we don't overwrite in-memory toggles
        config["bot"]["allow_dm"] = self.bot.allow_dm
        config["bot"]["allow_gc"] = self.bot.allow_gc
        config["bot"]["allow_server"] = getattr(self.bot, "allow_server", True)

        if key is None:
            bot_cfg = config["bot"]
            tts = bot_cfg.get("tts") or {}
            fr = bot_cfg.get("friend_requests") or {}
            mood = bot_cfg.get("mood") or {}
            late = bot_cfg.get("late_reply") or {}
            nudge = bot_cfg.get("nudge") or {}

            status = bot_cfg.get("status") or {}
            notif = config.get("notifications") or {}

            wait_times = bot_cfg.get("batch_wait_times") or []
            wt_str = "  ".join(f"{w['time']}s({w['weight']})" for w in wait_times)

            mood_list = ", ".join(mood.get("moods", {}).keys())

            nudge_hours = nudge.get("send_during_hours", [10, 22])

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
                "  💤  Nudge",
                f"  nudge.enabled              {nudge.get('enabled', False)}",
                f"  nudge.threshold_days       {nudge.get('threshold_days', 2)}",
                f"  nudge.check_interval_hours {nudge.get('check_interval_hours', 6)}",
                f"  nudge.send_during_hours    {nudge_hours[0]}:00 – {nudge_hours[1]}:00",
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
            # Special handling for batch_wait_times: parse "15s(30) 30s(35) ..." format
            if keys[-1] == "batch_wait_times":
                parsed = []
                for token in v.split():
                    m = re.fullmatch(r"(\d+)s\((\d+)\)", token.strip())
                    if m:
                        parsed.append({"time": int(m.group(1)), "weight": int(m.group(2))})
                if parsed:
                    return parsed
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
                async with AsyncSession(impersonate="chrome") as session:
                    resp = await session.get(url)
                    if resp.status_code != 200:
                        await ctx.send(f"Failed to fetch image (status {resp.status_code}).", delete_after=10)
                        return
                    image_data = resp.content
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
        """Connect to a voice channel muted/deafened and keep alive.

        Requires discord.py-self >= 2.1.0 and the `davey` package for DAVE E2EE
        support (Discord enforced DAVE on ~March 2, 2026 — clients without it are
        kicked with close code 4017).

        Install / upgrade with:
            pip install -U discord.py-self davey
        """
        # Close any existing connection on this guild first
        existing = target.guild.voice_client
        if existing:
            existing._keep_alive_guard = False
            await existing.disconnect(force=True)

        vc = await target.connect(self_mute=True, self_deaf=True)

        # --- DAVE / close-code constants ---
        # 4017 = DAVE protocol not supported (enforced by Discord since Mar 2026)
        # 4014 = disconnected by server, 4006 = session no longer valid
        _FATAL_CLOSE_CODES = {4006, 4014, 4017}

        async def _guard(channel, voice_client):
            """Keep the voice connection alive, but bail on fatal close codes."""
            voice_client._keep_alive_guard = True
            consecutive_failures = 0

            while getattr(voice_client, '_keep_alive_guard', False):
                await asyncio.sleep(20)

                vc_now = channel.guild.voice_client
                if vc_now is not None:
                    # Still connected — reset failure counter and loop
                    consecutive_failures = 0
                    continue

                # Connection dropped — check why before trying to reconnect
                if not getattr(voice_client, '_keep_alive_guard', False):
                    break  # intentional ,leave — stop silently

                # Inspect close code if available
                close_code = None
                ws = getattr(voice_client, '_connection', None) or getattr(voice_client, 'ws', None)
                if ws is not None:
                    close_code = getattr(ws, '_close_code', None) or getattr(ws, 'close_code', None)

                if close_code in _FATAL_CLOSE_CODES:
                    if close_code == 4017:
                        log_error(
                            "Voice Keep-Alive",
                            "Kicked with close code 4017 (DAVE E2EE not supported). "
                            "Run: pip install -U discord.py-self davey"
                        )
                    else:
                        log_error("Voice Keep-Alive", f"Fatal close code {close_code} — not reconnecting.")
                    voice_client._keep_alive_guard = False
                    break

                # Non-fatal drop — attempt reconnect with backoff
                consecutive_failures += 1
                if consecutive_failures > 3:
                    log_error("Voice Keep-Alive", "Too many consecutive failures — giving up.")
                    voice_client._keep_alive_guard = False
                    break

                try:
                    new_vc = await channel.connect(self_mute=True, self_deaf=True)
                    new_vc._keep_alive_guard = True
                    voice_client = new_vc
                    consecutive_failures = 0
                    log_system(f"Rejoined voice channel: {channel.name}")
                except Exception as e:
                    log_error("Voice Keep-Alive", str(e))
                    await asyncio.sleep(10 * consecutive_failures)  # exponential-ish backoff

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
            err_str = str(e)
            if "4017" in err_str or "dave" in err_str.lower():
                await ctx.send(
                    "❌ **Discord kicked the bot (DAVE protocol not supported).**\n"
                    "Fix: upgrade the library and install the DAVE crypto package:\n"
                    "```\npip install -U discord.py-self davey\n```",
                    delete_after=30,
                )
            else:
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

            total = len(files)
            current = 0

            def build_image_page(index: int):
                fname = files[index]
                fpath = os.path.join(folder, fname)
                desc = get_picture_description(fname)
                cap = f"\U0001f5bc `{fname}` \u2014 {index + 1}/{total}" + (f"\n> {desc}" if desc else "\n> *(no description yet)*")
                return cap, fpath

            caption, fpath = build_image_page(current)
            try:
                msg = await ctx.send(caption, file=discord.File(fpath), delete_after=180)
            except Exception as e:
                await ctx.send(f"Could not send image: {e}", delete_after=15)
                return

            if total == 1:
                return

            await msg.add_reaction("\u25c0")
            await msg.add_reaction("\u25b6")

            def check(reaction, user):
                return (
                    user.id == self.bot.owner_id
                    and str(reaction.emoji) in ("\u25c0", "\u25b6")
                    and reaction.message.id == msg.id
                )

            while True:
                try:
                    reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
                except asyncio.TimeoutError:
                    # Don't call clear_reactions — saves an API call, message will expire via delete_after
                    break

                new_page = current
                if str(reaction.emoji) == "\u25b6" and current < total - 1:
                    new_page = current + 1
                elif str(reaction.emoji) == "\u25c0" and current > 0:
                    new_page = current - 1

                if new_page == current:
                    # Same page — just remove the reaction, no resend needed
                    try:
                        await msg.remove_reaction(reaction.emoji, user)
                    except Exception:
                        pass
                    continue

                current = new_page
                caption, fpath = build_image_page(current)
                # delete+resend is unavoidable since Discord won't let us edit file attachments
                try:
                    await msg.delete()
                except Exception:
                    pass
                try:
                    msg = await ctx.send(caption, file=discord.File(fpath), delete_after=180)
                    await msg.add_reaction("\u25c0")
                    await msg.add_reaction("\u25b6")
                except Exception:
                    break

        elif action == "upload":
            if not ctx.message.attachments:
                await ctx.send("Attach an image to upload.", delete_after=10)
                return

            saved = []
            status_msg = await ctx.send("⏳ Saving & analysing image(s)...", delete_after=60)

            existing = [f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in exts]
            # Find the next unused index to avoid duplicates after deletions
            used_indices = set()
            for f in existing:
                stem = os.path.splitext(f)[0]
                if stem.startswith("IMG_") and stem[4:].isdigit():
                    used_indices.add(int(stem[4:]))
            next_index = 1
            while next_index in used_indices:
                next_index += 1

            from utils.ai import _create_image_completion
            from utils.helpers import load_config as _load_cfg
            import base64

            _cfg = _load_cfg()
            _image_model = _cfg["bot"].get("groq_image_model", "meta-llama/llama-4-scout-17b-16e-instruct")
            mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                        ".gif": "image/gif", ".webp": "image/webp"}

            results = []
            for att in ctx.message.attachments:
                ext = os.path.splitext(att.filename)[1].lower()
                if ext not in exts:
                    continue
                data = await att.read()
                new_name = f"IMG_{next_index}{ext}"
                dest = os.path.join(folder, new_name)
                with open(dest, "wb") as f:
                    f.write(data)

                # Run vision on the saved file immediately
                description = ""
                try:
                    mime = mime_map.get(ext, "image/jpeg")
                    b64 = base64.b64encode(data).decode()
                    data_url = f"data:{mime};base64,{b64}"
                    vision_resp = await _create_image_completion(
                        _image_model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Describe this image in full detail exactly as you see it. "
                                            "Include all visible text, objects, people, colors, layout, and context."
                                        ),
                                    },
                                    {"type": "image_url", "image_url": {"url": data_url}},
                                ],
                            }
                        ],
                    )
                    description = vision_resp.choices[0].message.content.strip()
                    add_picture_description(new_name, description)
                except Exception as ve:
                    log_error("Vision on Upload", str(ve))

                saved.append(new_name)
                results.append((new_name, description))
                next_index += 1

            await status_msg.delete()

            if not saved:
                await ctx.send("No valid image attachments found.", delete_after=10)
                return

            lines = []
            for name, desc in results:
                lines.append(f"`{name}` — {desc[:120] + '…' if len(desc) > 120 else desc or '*(vision failed)*'}")
            await ctx.send("Saved & analysed:\n" + "\n".join(lines), delete_after=60)

        elif action == "download":
            if not name:
                await ctx.send("Provide a number or filename. Use `,image ls` to see images.", delete_after=10)
                return
            files = sorted([f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in exts])
            # Accept just a number like "3" → IMG_3.jpg
            if name.isdigit():
                index = int(name)
                matches = [f for f in files if os.path.splitext(f)[0] == f"IMG_{index}"]
                if not matches:
                    await ctx.send(f"No image with number `{index}` found. Use `,image ls` to see images.", delete_after=10)
                    return
                name = matches[0]
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                # Try partial match
                matches = [f for f in files if name.lower() in f.lower()]
                if len(matches) == 1:
                    path = os.path.join(folder, matches[0])
                elif len(matches) > 1:
                    await ctx.send(f"Multiple matches: {', '.join(matches)}. Be more specific.", delete_after=15)
                    return
                else:
                    await ctx.send(f"Image `{name}` not found.", delete_after=10)
                    return
            await ctx.send(file=discord.File(path), delete_after=60)

        elif action in ("delete", "remove"):
            if not name:
                await ctx.send("Provide a number or filename to delete. Use `,image ls` to see images.", delete_after=10)
                return
            files = sorted([f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in exts])
            # Accept just a number like "3" → IMG_3.jpg
            if name.isdigit():
                index = int(name)
                matches = [f for f in files if os.path.splitext(f)[0] == f"IMG_{index}"]
                if not matches:
                    await ctx.send(f"No image with number `{index}` found. Use `,image ls` to see images.", delete_after=10)
                    return
                name = matches[0]
            elif name.lower() == "all":
                if not files:
                    await ctx.send("No images to delete.", delete_after=10)
                    return
                for f in files:
                    os.remove(os.path.join(folder, f))
                clear_all_pictures_db()
                await ctx.send(f"Deleted all {len(files)} image(s).", delete_after=10)
                return
            path = os.path.join(folder, name)
            if os.path.exists(path):
                os.remove(path)
                delete_picture_db(name)
                # Renumber remaining IMG_N files to fill the gap
                remaining = sorted(
                    [f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in exts],
                    key=lambda f: int(os.path.splitext(f)[0][4:]) if os.path.splitext(f)[0].startswith("IMG_") and os.path.splitext(f)[0][4:].isdigit() else 99999
                )
                for i, fname in enumerate(remaining, start=1):
                    stem, ext = os.path.splitext(fname)
                    if stem.startswith("IMG_") and stem[4:].isdigit() and int(stem[4:]) != i:
                        new_fname = f"IMG_{i}{ext}"
                        os.rename(os.path.join(folder, fname), os.path.join(folder, new_fname))
                        rename_picture_db(fname, new_fname)
                await ctx.send(f"Deleted `{name}`.", delete_after=10)
            else:
                await ctx.send(f"Image `{name}` not found.", delete_after=10)

        elif action == "vision":
            await ctx.send(
                "💡 Descriptions are now generated automatically on upload and shown in `,image ls`.",
                delete_after=15,
            )

        else:
            await ctx.send("Usage: `,image ls` | `,image upload` | `,image download <n>` | `,image delete <n>` | `,image vision [n]`", delete_after=15)


async def setup(bot):
    await bot.add_cog(Management(bot))
