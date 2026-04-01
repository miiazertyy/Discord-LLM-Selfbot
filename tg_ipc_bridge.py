"""
tg_ipc_bridge.py — Paste this into main.py

HOW TO INTEGRATE:
  1. Copy the _tg_ipc_loop() function below into main.py (anywhere above on_ready)
  2. In on_ready(), add this line at the bottom:
       asyncio.create_task(_tg_ipc_loop())
  3. That's it. The selfbot will now poll for Telegram commands every 2 seconds.

The bridge reads from config/tg_commands.json (written by telegram_controller.py)
and writes results to config/tg_results.json (read back by telegram_controller.py).
"""

# ── Paste this function into main.py ──────────────────────────────────────────

async def _tg_ipc_loop():
    """Poll for commands from the Telegram controller and execute them."""
    import json as _json
    import re as _re
    from pathlib import Path as _Path

    _CMD_FILE    = _Path(resource_path("config/tg_commands.json"))
    _RESULT_FILE = _Path(resource_path("config/tg_results.json"))
    _POLL_INTERVAL = 2.0  # seconds between polls

    def _write_result(cmd_id: str, data: dict):
        results = {}
        if _RESULT_FILE.exists():
            try:
                results = _json.loads(_RESULT_FILE.read_text())
            except Exception:
                pass
        results[cmd_id] = data
        _RESULT_FILE.write_text(_json.dumps(results))

    await bot.wait_until_ready()
    log_system("Telegram IPC bridge started — polling for commands")

    while not bot.is_closed():
        await asyncio.sleep(_POLL_INTERVAL)
        if not _CMD_FILE.exists():
            continue
        try:
            raw = _CMD_FILE.read_text()
            commands = _json.loads(raw)
        except Exception:
            continue
        if not commands:
            continue

        remaining = []
        for entry in commands:
            cmd_id  = entry.get("id", "")
            cmd     = entry.get("cmd", "")
            payload = entry.get("payload", {})

            try:
                # ── pause ────────────────────────────────────────────────────
                if cmd == "pause":
                    bot.paused = not bot.paused
                    _write_result(cmd_id, {"paused": bot.paused})

                # ── wipe ─────────────────────────────────────────────────────
                elif cmd == "wipe":
                    bot.message_history.clear()
                    _write_result(cmd_id, {"ok": True})

                # ── toggle_dm ────────────────────────────────────────────────
                elif cmd == "toggle_dm":
                    bot.allow_dm = not bot.allow_dm
                    cfg = load_config()
                    cfg["bot"]["allow_dm"] = bot.allow_dm
                    config_path = resource_path("config/config.yaml")
                    import yaml as _yaml
                    with open(config_path, "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"allow_dm": bot.allow_dm})

                # ── toggle_gc ────────────────────────────────────────────────
                elif cmd == "toggle_gc":
                    bot.allow_gc = not bot.allow_gc
                    cfg = load_config()
                    cfg["bot"]["allow_gc"] = bot.allow_gc
                    config_path = resource_path("config/config.yaml")
                    import yaml as _yaml
                    with open(config_path, "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"allow_gc": bot.allow_gc})

                # ── toggle_server ────────────────────────────────────────────
                elif cmd == "toggle_server":
                    bot.allow_server = not getattr(bot, "allow_server", True)
                    cfg = load_config()
                    cfg["bot"]["allow_server"] = bot.allow_server
                    config_path = resource_path("config/config.yaml")
                    import yaml as _yaml
                    with open(config_path, "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"allow_server": bot.allow_server})

                # ── toggle_active ─────────────────────────────────────────────
                elif cmd == "toggle_active":
                    channel_id = int(payload["channel_id"])
                    if channel_id in bot.active_channels:
                        bot.active_channels.discard(channel_id)
                        from utils.db import remove_channel
                        remove_channel(channel_id)
                        _write_result(cmd_id, {"active": False})
                    else:
                        bot.active_channels.add(channel_id)
                        from utils.db import add_channel
                        add_channel(channel_id)
                        _write_result(cmd_id, {"active": True})

                # ── ignore_add ───────────────────────────────────────────────
                elif cmd == "ignore_add":
                    uid = int(payload["user_id"])
                    if uid not in bot.ignore_users:
                        bot.ignore_users.append(uid)
                    _write_result(cmd_id, {"ok": True})

                # ── ignore_remove ────────────────────────────────────────────
                elif cmd == "ignore_remove":
                    uid = int(payload["user_id"])
                    if uid in bot.ignore_users:
                        bot.ignore_users.remove(uid)
                    _write_result(cmd_id, {"ok": True})

                # ── pauseuser ────────────────────────────────────────────────
                elif cmd == "pauseuser":
                    uid = int(payload["user_id"])
                    bot.paused_users.add(uid)
                    _write_result(cmd_id, {"ok": True})

                # ── unpauseuser ──────────────────────────────────────────────
                elif cmd == "unpauseuser":
                    uid = int(payload["user_id"])
                    bot.paused_users.discard(uid)
                    _write_result(cmd_id, {"ok": True})

                # ── persona_set ──────────────────────────────────────────────
                elif cmd == "persona_set":
                    from utils.memory import set_persona
                    uid = int(payload["user_id"])
                    persona = payload["persona"]
                    set_persona(uid, persona)
                    if uid in bot._memory_cache:
                        bot._memory_cache[uid]["__persona__"] = persona
                    _write_result(cmd_id, {"ok": True})

                # ── persona_clear ────────────────────────────────────────────
                elif cmd == "persona_clear":
                    from utils.memory import clear_persona
                    uid = int(payload["user_id"])
                    clear_persona(uid)
                    if uid in bot._memory_cache:
                        bot._memory_cache[uid].pop("__persona__", None)
                    _write_result(cmd_id, {"ok": True})

                # ── mood_set ─────────────────────────────────────────────────
                elif cmd == "mood_set":
                    import utils.mood as _mood_mod
                    _mood_mod.current_mood = payload["mood"]
                    _write_result(cmd_id, {"ok": True})

                # ── instructions_update ──────────────────────────────────────
                elif cmd == "instructions_update":
                    bot.instructions = payload["text"]
                    _write_result(cmd_id, {"ok": True})

                # ── config_update ────────────────────────────────────────────
                elif cmd == "config_update":
                    key   = payload.get("key", "")
                    value = payload.get("value")
                    _live_map = {
                        "allow_dm":           lambda v: setattr(bot, "allow_dm", v),
                        "allow_gc":           lambda v: setattr(bot, "allow_gc", v),
                        "allow_server":       lambda v: setattr(bot, "allow_server", v),
                        "realistic_typing":   lambda v: setattr(bot, "realistic_typing", v),
                        "batch_messages":     lambda v: setattr(bot, "batch_messages", v),
                        "hold_conversation":  lambda v: setattr(bot, "hold_conversation", v),
                        "reply_ping":         lambda v: setattr(bot, "reply_ping", v),
                        "disable_mentions":   lambda v: setattr(bot, "disable_mentions", v),
                        "anti_age_ban":       lambda v: setattr(bot, "anti_age_ban", v),
                    }
                    leaf = key.split(".")[-1]
                    if leaf in _live_map:
                        _live_map[leaf](value)
                    _write_result(cmd_id, {"ok": True})

                # ── analyse_user ─────────────────────────────────────────────
                elif cmd == "analyse_user":
                    uid = int(payload["user_id"])
                    try:
                        user = bot.get_user(uid) or await bot.fetch_user(uid)
                    except Exception:
                        _write_result(cmd_id, {"ok": False, "reason": f"User {uid} not found."})
                        continue

                    # Collect messages from DMs or any active channel
                    message_history_list = []
                    try:
                        dm = user.dm_channel or await user.create_dm()
                        async for msg in dm.history(limit=200):
                            if msg.author.id == uid and msg.content:
                                message_history_list.append(msg.content)
                    except Exception:
                        pass

                    # If DM history is thin, supplement from active channels
                    if len(message_history_list) < 20:
                        for ch_id in list(bot.active_channels)[:3]:
                            try:
                                ch = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
                                async for msg in ch.history(limit=200):
                                    if msg.author.id == uid and msg.content:
                                        message_history_list.append(msg.content)
                            except Exception:
                                pass

                    if not message_history_list:
                        _write_result(cmd_id, {"ok": False, "reason": f"No messages found for user {uid}."})
                        continue

                    instructions = (
                        bot.instructions +
                        f"\n\nSomeone asked you to give your honest read on {user.name} based on their messages. "
                        "Stay in character. Give your real unfiltered opinion like you would to a friend. "
                        "Be casual, funny, and direct. Roast them a bit but also be real about what you actually see. "
                        "Reference specific things they said to back up your points. "
                        "Keep it conversational — no bullet points, no formal structure, just talk like yourself. "
                        "Don't be overly mean but don't sugarcoat either. Max 3-4 short paragraphs."
                    )
                    prompt = "Here are their messages: " + " | ".join(message_history_list[-200:])

                    from utils.ai import generate_response
                    profile = await generate_response(prompt, instructions, history=None)
                    if profile:
                        _write_result(cmd_id, {"ok": True, "profile": profile})
                    else:
                        _write_result(cmd_id, {"ok": False, "reason": "AI returned an empty response."})

                # ── reply_check ──────────────────────────────────────────────
                elif cmd == "reply_check":
                    users_out = []
                    for hist_key, history in bot.message_history.items():
                        if not history or history[-1].get("role") != "user":
                            continue
                        try:
                            user_id = int(hist_key.split("-")[0])
                            user = bot.get_user(user_id)
                            pending = [e for e in reversed(history) if e["role"] == "user"]
                            last_msg = pending[0]["content"] if pending else ""
                            snippet = (last_msg[:60] + "…") if len(last_msg) > 60 else last_msg
                            users_out.append({
                                "id": user_id,
                                "name": user.name if user else str(user_id),
                                "snippet": snippet,
                                "count": len(pending),
                            })
                        except Exception:
                            pass
                    _write_result(cmd_id, {"users": users_out})

                # ── reply_user ───────────────────────────────────────────────
                elif cmd == "reply_user":
                    user_id = int(payload["user_id"])
                    user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                    if not user:
                        _write_result(cmd_id, {"success": False, "reason": "user not found"})
                        continue

                    target_msg = None
                    target_channel = None
                    for hist_key in bot.message_history:
                        if hist_key.startswith(f"{user_id}-"):
                            channel_id = int(hist_key.split("-")[1])
                            try:
                                ch = bot.get_channel(channel_id) or await user.create_dm()
                                async for msg in ch.history(limit=10):
                                    if msg.author.id == user_id:
                                        target_msg = msg
                                        target_channel = ch
                                        break
                            except Exception:
                                pass
                            break

                    if not target_msg:
                        try:
                            dm = user.dm_channel or await user.create_dm()
                            async for msg in dm.history(limit=10):
                                if msg.author.id == user_id:
                                    target_msg = msg
                                    target_channel = dm
                                    break
                        except Exception:
                            pass

                    if not target_msg:
                        _write_result(cmd_id, {"success": False, "reason": "no recent message found"})
                        continue

                    hist_key = f"{user_id}-{target_channel.id}"
                    history = bot.message_history.get(hist_key, [])
                    combined = target_msg.content or "[attachment]"
                    if not history or history[-1].get("content") != combined:
                        history.append({"role": "user", "content": combined})
                        bot.message_history[hist_key] = history

                    response = await generate_response_and_reply(
                        target_msg, combined, history,
                        bypass_cooldown=True, bypass_typing=True
                    )
                    if response:
                        bot.message_history[hist_key].append({"role": "assistant", "content": response})
                        _write_result(cmd_id, {"success": True})
                    else:
                        _write_result(cmd_id, {"success": False, "reason": "couldn't generate response"})

                # ── reply_all ────────────────────────────────────────────────
                elif cmd == "reply_all":
                    results_out = []
                    seen = set()
                    for hist_key, history in list(bot.message_history.items()):
                        if not history or history[-1].get("role") != "user":
                            continue
                        try:
                            user_id = int(hist_key.split("-")[0])
                            if user_id in seen:
                                continue
                            seen.add(user_id)
                            channel_id = int(hist_key.split("-")[1])
                            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                            ch = bot.get_channel(channel_id) or await user.create_dm()
                            target_msg = None
                            async for msg in ch.history(limit=5):
                                if msg.author.id == user_id:
                                    target_msg = msg
                                    break
                            if not target_msg:
                                results_out.append({"id": user_id, "name": user.name, "success": False, "reason": "no message"})
                                continue
                            combined = "\n".join(e["content"] for e in history if e["role"] == "user" and history.index(e) >= len(history) - 3)
                            response = await generate_response_and_reply(
                                target_msg, combined, history,
                                bypass_cooldown=True, bypass_typing=True
                            )
                            if response:
                                bot.message_history[hist_key].append({"role": "assistant", "content": response})
                                results_out.append({"id": user_id, "name": user.name, "success": True})
                            else:
                                results_out.append({"id": user_id, "name": user.name, "success": False, "reason": "no response"})
                        except Exception as _e:
                            results_out.append({"id": 0, "name": "unknown", "success": False, "reason": str(_e)})
                    _write_result(cmd_id, {"total": len(results_out), "results": results_out})

                # ── voice_join ───────────────────────────────────────────────
                elif cmd == "voice_join":
                    import discord as _discord
                    args_str = payload.get("args", "").strip()
                    guild_id_parsed = None
                    channel_id_parsed = None
                    link_match = _re.match(r"https?://discord\.com/channels/(\d+)/(\d+)", args_str)
                    if link_match:
                        guild_id_parsed = int(link_match.group(1))
                        channel_id_parsed = int(link_match.group(2))
                    elif args_str.isdigit():
                        channel_id_parsed = int(args_str)
                    else:
                        parts = args_str.split()
                        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                            guild_id_parsed = int(parts[0])
                            channel_id_parsed = int(parts[1])

                    if not channel_id_parsed:
                        _write_result(cmd_id, {"ok": False, "reason": "Invalid channel ID or link."})
                        continue

                    target = None
                    if guild_id_parsed:
                        guild = bot.get_guild(guild_id_parsed)
                        if guild:
                            target = guild.get_channel(channel_id_parsed)
                    else:
                        target = bot.get_channel(channel_id_parsed)

                    if not isinstance(target, _discord.VoiceChannel):
                        _write_result(cmd_id, {"ok": False, "reason": "Channel not found or is not a voice channel."})
                        continue

                    try:
                        # Use the same keep-alive approach as management.py
                        existing = target.guild.voice_client
                        if existing:
                            existing._keep_alive_guard = False
                            await existing.disconnect(force=True)
                        await target.connect(self_mute=True, self_deaf=True)
                        _write_result(cmd_id, {"ok": True, "channel": target.name, "guild": target.guild.name})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── voice_leave ──────────────────────────────────────────────
                elif cmd == "voice_leave":
                    import discord as _discord
                    guild_id = payload.get("guild_id")
                    if guild_id:
                        guild = bot.get_guild(int(guild_id))
                        vc = guild.voice_client if guild else None
                    else:
                        vc = next((g.voice_client for g in bot.guilds if g.voice_client), None)

                    if vc:
                        ch_name = vc.channel.name
                        g_name = vc.guild.name
                        vc._keep_alive_guard = False
                        await vc.disconnect(force=True)
                        _write_result(cmd_id, {"ok": True, "channel": ch_name, "guild": g_name})
                    else:
                        _write_result(cmd_id, {"ok": False, "reason": "Not in a voice channel."})

                # ── voice_autojoin ───────────────────────────────────────────
                elif cmd == "voice_autojoin":
                    import discord as _discord
                    import yaml as _yaml
                    args_str = payload.get("args", "").strip()

                    if not args_str or args_str.lower() == "off":
                        cfg = load_config()
                        cfg["bot"]["autojoin_channel"] = None
                        config_path = resource_path("config/config.yaml")
                        with open(config_path, "w", encoding="utf-8") as _f:
                            _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                        _write_result(cmd_id, {"ok": True, "disabled": True})
                        continue

                    guild_id_parsed = None
                    channel_id_parsed = None
                    link_match = _re.match(r"https?://discord\.com/channels/(\d+)/(\d+)", args_str)
                    if link_match:
                        guild_id_parsed = int(link_match.group(1))
                        channel_id_parsed = int(link_match.group(2))
                    elif args_str.isdigit():
                        channel_id_parsed = int(args_str)
                    else:
                        parts = args_str.split()
                        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                            guild_id_parsed = int(parts[0])
                            channel_id_parsed = int(parts[1])

                    if not channel_id_parsed:
                        _write_result(cmd_id, {"ok": False, "reason": "Invalid channel ID or link."})
                        continue

                    target = None
                    if guild_id_parsed:
                        guild = bot.get_guild(guild_id_parsed)
                        if guild:
                            target = guild.get_channel(channel_id_parsed)
                    else:
                        target = bot.get_channel(channel_id_parsed)

                    if not isinstance(target, _discord.VoiceChannel):
                        _write_result(cmd_id, {"ok": False, "reason": "Channel not found or is not a voice channel."})
                        continue

                    cfg = load_config()
                    cfg["bot"]["autojoin_channel"] = {"guild_id": target.guild.id, "channel_id": target.id}
                    config_path = resource_path("config/config.yaml")
                    with open(config_path, "w", encoding="utf-8") as _f:
                        _yaml.dump(cfg, _f, default_flow_style=False, allow_unicode=True)
                    _write_result(cmd_id, {"ok": True, "disabled": False, "channel": target.name, "guild": target.guild.name})

                # ── set_status ───────────────────────────────────────────────
                elif cmd == "set_status":
                    import discord as _discord
                    emoji = payload.get("emoji")
                    text = payload.get("text")
                    try:
                        await bot.change_presence(
                            activity=_discord.CustomActivity(name=text or "", emoji=emoji or None)
                        )
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── set_bio ──────────────────────────────────────────────────
                elif cmd == "set_bio":
                    text = payload.get("text", "")
                    try:
                        await bot.user.edit(bio=text)
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── set_pfp ──────────────────────────────────────────────────
                elif cmd == "set_pfp":
                    url = payload.get("url", "")
                    try:
                        from curl_cffi.requests import AsyncSession as _AsyncSession
                        async with _AsyncSession(impersonate="chrome") as _session:
                            resp = await _session.get(url)
                            if resp.status_code != 200:
                                _write_result(cmd_id, {"ok": False, "reason": f"Failed to fetch image (status {resp.status_code})."})
                                continue
                            image_data = resp.content
                        await bot.user.edit(avatar=image_data)
                        _write_result(cmd_id, {"ok": True})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── add_friend ───────────────────────────────────────────────
                elif cmd == "add_friend":
                    user_id = int(payload["user_id"])
                    try:
                        from curl_cffi.requests import AsyncSession as _AsyncSession
                        token = bot._connection.http.token
                        async with _AsyncSession(impersonate="chrome") as _session:
                            resp = await _session.put(
                                f"https://discord.com/api/v9/users/@me/relationships/{user_id}",
                                headers={
                                    "Authorization": token,
                                    "Content-Type": "application/json",
                                },
                                json={"type": 1},
                            )
                            if resp.status_code in (200, 204):
                                _write_result(cmd_id, {"ok": True})
                            else:
                                try:
                                    data = resp.json()
                                    msg = data.get("message", str(data))
                                except Exception:
                                    msg = f"HTTP {resp.status_code}"
                                _write_result(cmd_id, {"ok": False, "reason": msg})
                    except Exception as _e:
                        _write_result(cmd_id, {"ok": False, "reason": str(_e)})

                # ── image_delete ─────────────────────────────────────────────
                elif cmd == "image_delete":
                    import os as _os
                    name = payload.get("name", "")
                    folder = resource_path("config/pictures")
                    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    files = sorted([f for f in _os.listdir(folder) if _os.path.splitext(f)[1].lower() in exts])
                    target_file = None
                    if name.isdigit():
                        idx = int(name)
                        matches = [f for f in files if _os.path.splitext(f)[0] == f"IMG_{idx}"]
                        if matches:
                            target_file = matches[0]
                    if not target_file:
                        matches = [f for f in files if name.lower() in f.lower()]
                        if len(matches) == 1:
                            target_file = matches[0]

                    if not target_file:
                        _write_result(cmd_id, {"ok": False, "reason": f"Image '{name}' not found."})
                        continue

                    path = _os.path.join(folder, target_file)
                    if _os.path.exists(path):
                        _os.remove(path)
                        from utils.db import delete_picture_db, rename_picture_db
                        delete_picture_db(target_file)
                        # Renumber remaining
                        remaining_files = sorted(
                            [f for f in _os.listdir(folder) if _os.path.splitext(f)[1].lower() in exts],
                            key=lambda f: int(_os.path.splitext(f)[0][4:]) if _os.path.splitext(f)[0].startswith("IMG_") and _os.path.splitext(f)[0][4:].isdigit() else 99999
                        )
                        for i, fname in enumerate(remaining_files, start=1):
                            stem, ext = _os.path.splitext(fname)
                            if stem.startswith("IMG_") and stem[4:].isdigit() and int(stem[4:]) != i:
                                new_fname = f"IMG_{i}{ext}"
                                _os.rename(_os.path.join(folder, fname), _os.path.join(folder, new_fname))
                                rename_picture_db(fname, new_fname)
                        _write_result(cmd_id, {"ok": True})
                    else:
                        _write_result(cmd_id, {"ok": False, "reason": f"Image '{name}' not found."})

                # ── image_delete_all ─────────────────────────────────────────
                elif cmd == "image_delete_all":
                    import os as _os
                    folder = resource_path("config/pictures")
                    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                    files = [f for f in _os.listdir(folder) if _os.path.splitext(f)[1].lower() in exts]
                    for f in files:
                        _os.remove(_os.path.join(folder, f))
                    from utils.db import clear_all_pictures_db
                    clear_all_pictures_db()
                    _write_result(cmd_id, {"ok": True, "count": len(files)})

                # ── reload ───────────────────────────────────────────────────
                elif cmd == "reload":
                    import sys as _sys
                    import os as _os
                    cogs_dir = _os.path.join(getattr(_sys, "_MEIPASS", _os.path.abspath(".")), "cogs")
                    errors = []
                    if _os.path.exists(cogs_dir):
                        for filename in _os.listdir(cogs_dir):
                            if filename.endswith(".py"):
                                try:
                                    await bot.unload_extension(f"cogs.{filename[:-3]}")
                                    await bot.load_extension(f"cogs.{filename[:-3]}")
                                except Exception as _e:
                                    errors.append(f"{filename}: {_e}")
                    bot.instructions = load_instructions()
                    _write_result(cmd_id, {"ok": True, "errors": errors})

                # ── update ───────────────────────────────────────────────────
                elif cmd == "update":
                    import atexit as _atexit
                    import subprocess as _sp
                    source = payload.get("source", "release")
                    # Trigger the same update logic used in management.py
                    # We just delegate by finding the Management cog
                    mgmt_cog = bot.cogs.get("Management")
                    if mgmt_cog and hasattr(mgmt_cog, "_do_update"):
                        asyncio.create_task(mgmt_cog._do_update(source))
                    else:
                        # Fallback: just restart
                        if getattr(sys, "frozen", False):
                            _atexit.register(lambda: os.startfile(sys.executable))
                        else:
                            _atexit.register(lambda: _sp.Popen([sys.executable] + sys.argv))
                        await bot.close()
                        sys.exit(0)

                # ── restart ──────────────────────────────────────────────────
                elif cmd == "restart":
                    import atexit as _atexit
                    log_system("Restart requested via Telegram controller")
                    if getattr(sys, "frozen", False):
                        _atexit.register(lambda: os.startfile(sys.executable))
                    else:
                        import subprocess as _sp
                        _atexit.register(lambda: _sp.Popen([sys.executable] + sys.argv))
                    await bot.close()
                    sys.exit(0)

                # ── shutdown ─────────────────────────────────────────────────
                elif cmd == "shutdown":
                    log_system("Shutdown requested via Telegram controller")
                    await bot.close()
                    sys.exit(0)

                else:
                    remaining.append(entry)

            except Exception as _err:
                log_error("TG IPC", f"cmd={cmd} error={_err}")
                remaining.append(entry)

        _CMD_FILE.write_text(_json.dumps(remaining))


# ── In on_ready(), add this line: ─────────────────────────────────────────────
# asyncio.create_task(_tg_ipc_loop())
