# 🌙 Discord LLM Selfbot

A fully AI-powered Discord selfbot using the Groq API — **completely free**.

> **Need help?** Join the Discord: https://discord.gg/connard

---

> ⚠️ **Disclaimer**
> Using this on a user account violates [Discord's Terms of Service](https://discord.com/terms) and may result in your account being banned. Only use this on an account you're willing to lose. I take no responsibility for any actions taken against your account.
> It is **strongly recommended** to control the bot exclusively through the Telegram controller — running commands directly on Discord creates selfbot-detectable activity on your account.

---

## ☮️ Features

- Fully customizable personality via `instructions.txt`
- Responds in the same language as the user automatically
- Realistic typing speed with variable pauses and occasional typos
- Batches multiple messages before responding, just like a real person
- Weighted random reply delays — quick, distracted, or away
- Mood system that shifts automatically and affects how the AI writes
- Per-user persistent memory — remembers names, hobbies, and personal facts
- Reads the user's Discord profile (display name, status, bio) and uses it in responses
- Sees images, embedded links (imgur, tenor, giphy, etc.), and voice messages
- Sends real Discord voice message bubbles using Groq Orpheus TTS
- Transcribes incoming voice messages via Groq Whisper and responds to them
- Multiple Groq API keys + models with automatic fallback when rate limited
- Responds to trigger words, mentions, and replies — server-aware
- Holds conversations naturally in DMs and group chats
- Auto-accepts friend requests with a configurable delay
- Late reply openers when responding after a long pause — woven in naturally by the AI
- Global send lock prevents two responses from sending simultaneously
- Anti-spam cooldown per user
- Random status cycling on a configurable schedule
- All credentials stored securely in `.env`
- Everything configurable in `config.yaml` — editable live from Telegram

---

## 🌐 Telegram Controller (Recommended)

Instead of running commands directly on Discord, control everything through a private Telegram bot. This is the **safer and recommended approach** — 100% of management activity stays off Discord.

### Telegram Setup

**Step 1 — Create a Telegram bot**
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token it gives you (looks like `123456:ABC-...`)

**Step 2 — Get your Telegram user ID**
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your numeric user ID

**Step 3 — Add to your `.env`**
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_OWNER_ID=your_telegram_user_id_here
```

**Step 4 — Run**

Both `main.py` and the Telegram controller run at the same time. If you use `run.bat` / `run.sh` the controller launches automatically in a separate window. To start it manually:
```bash
python telegram/telegram_controller.py
```

### Multi-Account Support

If you run multiple Discord tokens (`DISCORD_TOKEN_1`, `DISCORD_TOKEN_2` ...), each selfbot instance gets its own IPC channel. Use `/account <n>` in Telegram to switch between them:

```
/account        — show which account is currently targeted
/account 2      — switch to account #2
```

---

## 📋 Telegram Commands

### 🌙 AI
| Command | Description |
|---|---|
| `/pause` | Pause / unpause AI responses |
| `/pauseuser <user>` | Stop responding to a specific user |
| `/unpauseuser <user>` | Resume responding to a user |
| `/persona <user> [text]` | Set, clear, or view a per-user persona |
| `/wipe` | Clear conversation history |
| `/analyse <user>` | Generate a psychological read of a user |

### 💬 Replies
| Command | Description |
|---|---|
| `/reply <user>` · `/response <user>` | Manually trigger a reply to a user's last message |
| `/reply check` | Show users with unread messages |
| `/reply all` | Respond to every user with unread messages |

### ⚙️ Instructions & Config
| Command | Description |
|---|---|
| `/prompt [text]` | View, set, or clear instructions inline |
| `/instructions` | Upload a new `instructions.txt` (attach file) |
| `/getinstructions` | Download the current `instructions.txt` |
| `/config` | View full config |
| `/config <key> <value>` | Edit a config value using dot notation |
| `/getconfig` | Download the current `config.yaml` |
| `/setconfig` | Upload a new `config.yaml` (attach file) |

### 📡 Channels
| Command | Description |
|---|---|
| `/toggleactive <id>` | Toggle a channel as active by ID |
| `/toggledm` | Toggle DM responses |
| `/togglegc` | Toggle group chat responses |
| `/toggleserver` | Toggle server mention/reply responses |
| `/ignore <user>` | Ignore / unignore a user |

### 🎙️ Voice
| Command | Description |
|---|---|
| `/join <channel_id / link>` | Join a voice channel (muted & deafened) |
| `/leave` | Leave the current voice channel |
| `/autojoin <channel_id / link>` | Auto-join a voice channel on every startup |
| `/autojoin off` | Disable auto-join |

### 🖼️ Images
| Command | Description |
|---|---|
| `/imagels` · `/imagelist` | List all pictures with descriptions |
| `/imageupload` | Upload picture(s) (attach photo) — auto-analysed |
| `/imagedownload <n>` · `/imagedl <n>` | Download a picture by number |
| `/imagedelete <n> [n2 n3 ...]` | Delete one or more pictures by number |
| `/imagedeleteall` | Delete all pictures |

### 🎭 Profile & Status
| Command | Description |
|---|---|
| `/setstatus [emoji] [text]` | Set a custom Discord status |
| `/bio [text]` | Set profile bio |
| `/pfp <url>` | Change profile picture |
| `/mood [name]` | View or set the current mood |

### 🛠️ System
| Command | Description |
|---|---|
| `/addfriend <user_id>` | Send a friend request by user ID |
| `/reload` | Reload all cogs and instructions |
| `/restart` | Restart the bot |
| `/shutdown` | Shut down the bot |
| `/update` | Update to the latest stable release |
| `/update main` | Update to the latest commit |
| `/getdb` | Download the memory database |
| `/leaderboard [filter]` | Show top users (e.g. `/leaderboard 7d`, `/leaderboard 1w`) |
| `/ping` | Check the controller is running |

---

## ❔ Setup

### Step 1 — Clone the repository
```bash
git clone https://github.com/miiazertyy/Discord-LLM-Selfbot
cd Discord-LLM-Selfbot
```

### Step 2 — Get your Discord token
1. Open [Discord](https://discord.com) in your browser and log in
2. Press `Ctrl+Shift+I` (Windows) or `Cmd+Opt+I` (Mac) to open DevTools
3. Go to the **Network** tab
4. Send a message or switch server
5. Find a request named `messages?limit=50`, `science`, or `preview`
6. Scroll to **Request Headers** and copy the `Authorization` value — that's your token

### Step 3 — Get a Groq API key
1. Sign up at [console.groq.com](https://console.groq.com/keys)
2. Create a free API key (looks like `gsk_...`)

### Step 4 — Configure credentials
- Rename `example.env` to `.env`
- Fill in your Discord token and Groq API key
- Optionally add your Telegram bot token and owner ID (recommended)

### Step 5 — Run the bot

**Windows:**
```
run.bat
```
Or manually:
```bash
python -m venv bot-env
bot-env\Scripts\activate.bat
pip install -r requirements.txt
python main.py
# In a separate terminal:
python telegram/telegram_controller.py
```

**Linux:**
```bash
sudo apt install python3 ffmpeg -y
chmod +x run.sh updater.sh
./run.sh
```

---

## 💭 Customizing the Personality

Edit `config/instructions.txt` to set the bot's personality, tone, and behavior. You can also update it live from Telegram using `/instructions` (attach a `.txt` file) or `/prompt <text>`.
