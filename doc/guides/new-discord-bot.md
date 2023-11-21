# Setting up a new discord bot

1. [Create an application](https://discord.com/developers/applications)
2. Create a bot
3. Enable message content intent
4. Disable public bot (optional)
  > (!) Anyone with some technical know-how can invite a public bot to their Discord server
5. Generate a token and put it in the `discord_token` file in the em's directory
```
echo TOKEN > discord_token
```
6. Generate a URL for adding the bot:
```
https://discord.com/api/oauth2/authorize?client_id=<application_id>&permissions=536879168&scope=bot
```
