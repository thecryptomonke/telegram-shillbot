import json
import pytz
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# Load configuration from config.json
with open('config.json', 'r') as f:
    config = json.load(f)

api_id = config['api_id']
api_hash = config['api_hash']
phone = config['phone']
password = config['password']
channel_url = config['channel_url']
timezone = config['timezone']

# Setup timezone
local_tz = pytz.timezone(timezone)

# Initialize Telegram client
client = TelegramClient('session_name', api_id, api_hash)

async def main():
    await client.start(phone)
    try:
        await client.sign_in(phone)
    except SessionPasswordNeededError:
        await client.sign_in(password=password)
    
    # Confirm connection by printing account info
    me = await client.get_me()
    print(f"Connected to Telegram as {me.first_name} {me.last_name} (username: {me.username})")
    
    # Fetch the channel to confirm access
    channel = await client.get_entity(channel_url)
    print(f"Successfully accessed channel: {channel.title}")

    # Display current time in the configured timezone
    current_time = datetime.now(local_tz)
    print(f"Current time in {timezone} is: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")

with client:
    client.loop.run_until_complete(main())
