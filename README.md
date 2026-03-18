# 🌙 Discord LLM Selfbot

Made with python, uses Groq LLM provider **ALL FOR FREE**
Mostly supports French and fully supports English

### JOIN IF YOU NEED HELP : https://discord.gg/connard

> There is always the slight risk of a ban when using selfbots, so make sure to use this selfbot on an account you don't mind losing, but the risk is incredibly low and I have used it for over a year without any issues.

### **❗ Important:**  
*I take no responsibility for any actions taken against your account for using these selfbots or how users use my open-source code.*

<strong>Using this on a user account is prohibited by the [Discord TOS](https://discord.com/terms) and can lead to your account getting banned in _very_ rare cases.</strong>

Preview :
<img width="959" height="344" alt="image" src="https://github.com/user-attachments/assets/4d0e63ea-9f98-48a3-9332-820376694c61" />


# ☮️ Features

-   [x] Discord Selfbot: Runs on a genuine Discord account, allowing you to use it without even needing to invite a bot.
-   [x] Custom AI Instructions: You can replace the text inside of `instructions.txt` and make the AI act however you'd like!
-   [x] Realistic Typing: The bot types like a real person, with varying speeds and pauses.
-   [x] Free LLM Model: Enjoy the powerful capabilities of this language model without spending a dime.
-   [x] Mention Recognition: The bot only responds when you mention it or say its trigger word.
-   [x] Reply Recognition: If replied to, the bot will continue to reply to you. It's like having a conversation with a real person.
-   [x] Message Handling: The bot knows when you're replying to someone else, so it won't cause confusion. It's like having a mind reader in your server; It can also handle numerous messages at once!
-   [x] Image Recognition: The bot can recognize images and respond to them in character.
-   [x] Channel-Specific Responses: Use the `,toggleactive` command to pick what channel the bot responds in.
-   [x] Anti-spam: The bot has a built-in anti-spam feature to prevent people from abusing it.
-   [x] Psychoanalysis Command: Use the `,analyse` command to analyse a mentioned user's messages and find insights on their personality.
-   [x] Runs on Meta AI's Llama-3: The bot uses the Llama-3 model from Meta AI, which is one of the most powerful models available.
-   [x] Secure Credential Management: Keep your credentials secure using environment variables.
-   [x] Priority prefix "=" to bypass wait times between responses usable by everyone.
-   [x] Auto switches between LLMs when out of tokens.
-   [x] Changes mood automatically, replacing the temperature from the LLM and is fully customizable.
-   [x] Per user memory stocked and fetched in an SQL file.
-   [ ] Auto accept friend requests

And a bunch other quality of life features

## 📜 Commands

-   pause - Pause the bot from producing AI responses
-   analyse [user] - Analyze a user's message history and provides a - gical profile
-   wipe - Clears history of the bot
-   ping - Shows the bot's latency
-   toggleactive [channelID] - Toggle the current channel to the list of active channels
-   toggledm - Toggle if the bot should be active in DM's or not
-   togglegc - Toggle if the bot should be active in group chats or not
-   ignore [user] - Stop a user from using the bot
-   reload - Reloads all cogs
-   instructions - Changes the instruction.txt directly from Discord DMs
-   getinstructions - Get the instructions.txt in chat
-   setconfig - Sets the config.yaml from the chat
-   getconfig - Sends config.yaml in chat 
-   getdb - Get database with users memorys
-   update (repo) - Updates and restart the bot to the lastest stable using git
-   restart - Restarts the entire bot
-   shutdown - Shuts down the bot

# ❔ Getting Started:

### Step 1: Download the Selfbot
- Go to Release and download the lastest stable version

### Step 2: Extract the files
- Extract the files to a folder of your choice, using 7Zip or Windows Explorer.

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

### Step 5: Running the bot

Windows: 

- Simply run "Discord AI Selfbot.exe" and follow the instructions in the console to set up the bot.

Linux:

- Open a terminal and run `chmod +x "Discord-AI-Selfbot"` to make the file executable.
- Run `./"Discord-AI-Selfbot"` to start the bot and follow the instructions in the console to set it up.

# 🛠️ Setting up the bot manually:

If you want to set up the bot manually because you don't trust the executable or want to edit the code yourself, follow the instructions below:

### Step 1: Git clone repository

```
git clone https://github.com/Najmul190/Discord-AI-Selfbot
```

### Step 2: Changing directory to cloned directory

```
cd Discord-AI-Selfbot
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
-   Run the bot using `run.sh`

# 🗨️ How to talk to the bot

-   To activate it in a channel use **~toggleactive channelid** (channelid is optional).
-   To see all commands use **(prefix)help**
-   Bear in mind that the bot will only respond to **other accounts** and not itself, including any commands.
-   You can also set a trigger word within the `config.yaml`, this is the word that the bot will respond to. For example, if you set the trigger word to `John`, people must say "Hey `John`, how are you today?" for the bot to respond.


# 💭 Changing the Personality of the bot

To change the personality of the bot and set custom instructions, simply go into the `config` folder and edit the default instructions in `instructions.txt` to whatever you want! 
