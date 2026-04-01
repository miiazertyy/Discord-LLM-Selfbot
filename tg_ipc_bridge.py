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
                    # Apply in-memory for live keys the bot uses directly
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

                # ── reply_check ──────────────────────────────────────────────
                elif cmd == "reply_check":
                    # Pull unreplied from in-memory history (fast path)
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

                    # Find last message from user
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
