# 🌙 Discord LLM Selfbot

Made with python, uses Groq LLM provider **ALL FOR FREE**
Mostly supports French and fully supports English

### JOIN IF YOU NEED HELP : https://discord.gg/connard

> There is always the slight risk of a ban when using selfbots, so make sure to use this selfbot on an account you don't mind losing, but the risk is incredibly low and I have used it for over a year without any issues.

### **❗ Important:**  
*I take no responsibility for any actions taken against your account for using these selfbots or how users use my open-source code.*

<strong>Using this on a user account is prohibited by the [Discord TOS](https://discord.com/terms) and can lead to your account getting banned in _very_ rare cases.</strong>

Preview :
<img width="1623" height="645" alt="SOLbKIc" src="https://github.com/user-attachments/assets/6b75131f-716c-448c-97ed-5275241cb8ef" />




# ☮️ Features

- [x] Fully customizable personality via `instructions.txt`
- [x] Responds in the same language as the user automatically
- [x] Realistic typing speed with variable pauses and occasional typos
- [x] Batches multiple messages before responding, just like a real person
- [x] Weighted random reply delays — quick, distracted, or away
- [x] Mood system that shifts automatically and affects how the AI writes
- [x] Per-user persistent memory — remembers names, hobbies, and personal facts across conversations
- [x] Reads the user's Discord profile (display name, status, bio) and factors it into responses
- [x] Supports images, voice messages, and stickers
- [x] Sends real Discord voice message bubbles using Groq Orpheus TTS
- [x] Transcribes incoming voice messages using Groq Whisper and responds to them
- [x] Multiple Groq models with automatic fallback when rate limited
- [x] Responds to trigger words, mentions, and replies — server-aware
- [x] Holds conversations naturally in DMs and group chats
- [x] Auto-accepts friend requests with a configurable delay
- [x] Late reply openers when responding after a long pause
- [x] Anti-spam cooldown per user
- [x] Random status cycling on a configurable schedule
- [x] Secure credential storage via `.env`
- [x] Everything configurable in `config.yaml` with live editing from Discord

##  📋 Commands

###  🤖  AI
    ,pause              pause/unpause AI responses
    ,wipe               clear conversation history
    ,reply [user]       manually reply to a user's last message
    ,reply check        checks for unresponded users
    ,analyse [user]     psychological profile of a user

###  ⚙️   Config
    ,config             view/edit config inline
    ,getconfig          download config.yaml
    ,setconfig          upload a new config.yaml
    ,instructions       upload new instructions.txt
    ,getinstructions    download instructions.txt
    ,prompt [text]      view/set/clear instructions inline

###  📡  Channels
    ,toggleactive       toggle current channel
    ,toggledm           toggle DM responses
    ,togglegc           toggle group chat responses
    ,toggleserver       toggle server responses
    ,ignore [user]      ignore/unignore a user

###  🛠️   System
    ,update             update to latest release
    ,update main        update to latest commit
    ,reload             reload all cogs + instructions
    ,restart            restart the bot
    ,shutdown           shut down the bot
    ,ping               show latency
    ,getdb              download memory database
    ,image ls           list bot pictures
    ,image upload       upload a picture (attach file)
    ,image download [n]  download a picture from folder
    ,status [emoji] [text]  set custom status
    ,bio [text]         set profile bio
    ,pfp [url/attach]   change profile picture
    ,mood [name]        view or set current mood

### Step 1: Download the Selfbot
- Go to Release and download the lastest stable version

### Step 2: Extract the files
- Extract the files to a folder of your choice, using 7Zip or Windows Explorer.

# 🛠️ Setting up the bot manually:

### Step 1: Git clone repository

```
git clone https://github.com/miiazertyy/Discord-LLM-Selfbot
```

### Step 2: Changing directory to cloned directory

```
cd Discord-LLM-Selfbot
```

### Step 3: Getting your Discord token

-   Go to [Discord](https://discord.com) and login to the account you want the token of
-   Press `Ctrl + Shift + I` (If you are on Windows) or `Cmd + Opt + I` (If you are on a Mac).
-   Go to the `Network` tab
-   Type a message in any chat, or change server
-   Find one of the following headers: `"messages?limit=50"`, `"science"` or `"preview"` under `"Name"` and click on it
-   Scroll down until you find `"Authorization"` under `"Request Headers"`
-   Copy the value which is your token

### Step 4: Getting a Groq API key

-   Go to [Groq](https://console.groq.com/keys) and sign up for a free account
-   Get your API key, which should look like `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` (this is an example key, it isn't real)

### Step 5: Install all the dependencies and run the bot

Windows:

-   Simply open `run.bat` if you're on Windows. This will install all pre-requisites, guide you through the process of setting up the bot and run it for you.
-   
-   If `run.bat` doesn't work, then open CMD and run `cd Discord-AI-Selfbot` to change directory to the bot files directory
-   Create a virtual environment by running `python -m venv bot-env`
-   Activate the virtual environment by running `bot-env\Scripts\activate.bat`
-   Run `pip install -r requirements.txt` to install all the dependencies
-   Fill out `example.env` with your own credentials and rename it to `.env`
-   Fill out the `config.yaml` file with your own settings
-   Run the bot using `python3 main.py`

Linux:

-   Fill out `example.env` with your own credentials and rename it to `.env`
-   Fill out the `config.yaml` file with your own settings
-   In terminal :
  sudo apt install python3
  sudo apt install ffmpeg -y
  chmod +x run.sh
  chmod +x updater.sh
-   Run the bot using `./run.sh`

# 🗨️ How to talk to the bot

-   To activate it in a channel use **~toggleactive channelid** (channelid is optional).
-   To see all commands use **(prefix)help**
-   You can also set a trigger word within the `config.yaml` or with the ,config command, this is the word that the bot will respond to.
# 💭 Changing the Personality of the bot

To change the personality of the bot and set custom instructions, simply go into the `config` folder and edit the default instructions in `instructions.txt` to whatever you want! 
