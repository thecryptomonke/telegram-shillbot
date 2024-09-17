import json
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
from tabulate import tabulate
import pytz
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, RPCError
import pandas as pd
import os
import subprocess
import asyncio
from tqdm.asyncio import tqdm
from getpass import getpass

# Load configuration from config.json
with open('config.json', 'r') as config_file:
    config = json.load(config_file)

api_id = config["api_id"]
api_hash = config["api_hash"]
phone = config["phone"]
password = config["password"]
channel_url = config["channel_url"]
timezone_config = config["timezone"]
output_directory = config.get("output_directory", os.getcwd())

# Define the timezone based on the configuration
local_tz = pytz.timezone(timezone_config)
end_date = datetime.now(pytz.utc)


def parse_metric(text):
    try:
        return int(text.split()[0].replace("(", "").replace("+", ""))
    except (ValueError, IndexError):
        return 0


def calculate_avg_time_diffs(timestamps):
    """Calculates the average time difference (in seconds) between a list of datetime objects."""
    if len(timestamps) < 2:
        return None  # Not enough timestamps to calculate an average

    time_diffs = []
    for i in range(1, len(timestamps)):
        time_diff = (timestamps[i] - timestamps[i - 1]).total_seconds()
        time_diffs.append(time_diff)

    avg_time_diff = sum(time_diffs) / len(time_diffs)

    # Convert the average time difference back into a readable format (e.g., minutes and seconds)
    minutes, seconds = divmod(avg_time_diff, 60)
    return f"{int(minutes)}:{int(seconds):02d}"


def search_token_in_message(message, token_id):
    """Searches for a token ID within a message text or associated URLs."""
    message_text = message.get("message", "")

    # Check in the message text
    if token_id.lower() in message_text.lower():
        return True

    # Check in the URLs in the message's entities
    if "entities" in message:
        for entity in message["entities"]:
            if entity.get("_") == "MessageEntityTextUrl":
                url = entity.get("url", "")
                if token_id.lower() in url.lower():
                    return True

    return False


def convert_and_format_date_utc_plus_1(date_str):
    if not date_str:
        return "Invalid date"
    try:
        utc_time = datetime.fromisoformat(date_str)
        target_tz = timezone(timedelta(hours=1))
        localized_time = utc_time.astimezone(target_tz)
        formatted_date = localized_time.strftime("%H:%M:%S %d/%m/%Y")
        return formatted_date
    except ValueError:
        return "Invalid date"


def get_start_date():
    while True:
        choice = input("Do you want to fetch messages from the start of today (T) or from a specific date (D)? Enter T or D: ").strip().upper()
        if choice in ['T', 'D']:
            break
        else:
            print("Invalid input. Please enter 'T' or 'D'.")

    if choice == 'D':
        date_str = input("Enter the start date in YYYY-MM-DD format: ").strip()
        try:
            start_date = datetime.strptime(date_str, "%Y-%m-%d")
            start_date = local_tz.localize(start_date)
            print(f"Fetching messages from {start_date.isoformat()} until now...")
        except ValueError:
            print("Invalid date format. Using the start of today instead.")
            start_date = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        print(f"Fetching messages from the start of today {start_date.isoformat()} until now...")

    return start_date


def serialize_message(message_dict):
    """Recursively converts datetime objects in the message to strings."""
    for key, value in message_dict.items():
        if isinstance(value, datetime):
            message_dict[key] = value.isoformat()
        elif isinstance(value, dict):
            serialize_message(value)
    return message_dict


def clean_message_text(message_text):
    patterns_to_remove = [
        r"📈 Chart   ⏫ Trending   ✳️ Events",
        r"🐬 \| D\.RAIDBOARD #[0-9]+ \| [0-9]+⚡️",
        r"[🐳🐬⚡️]"
    ]
    for pattern in patterns_to_remove:
        message_text = re.sub(pattern, '', message_text).strip()
    return message_text


def extract_token_name(message_text):
    # Try multiple patterns to extract token name
    patterns = [
        r'^(.*?)\s(?:Started|Just)',          # Matches "TokenName Started..." or "TokenName Just..."
        r'^🚀\s*(.*?)\s*🚀',                   # Matches "🚀 TokenName 🚀"
        r'^Token:\s*(\S+)',                   # Matches "Token: TokenName"
        r'Launching\s*(\S+)',                 # Matches "Launching TokenName"
        r'^New Shill:\s*(\S+)',               # Matches "New Shill: TokenName"
    ]
    for pattern in patterns:
        match = re.search(pattern, message_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return "Unknown Token"


def calculate_disparity(text):
    try:
        a = int(text.split()[0])
        x = int(text.split("(+")[1].replace(")", ""))
        return a - x
    except (ValueError, IndexError):
        return 0


def display_selected_fields(messages, filter_date=None):
    processed_data = {"messages": []}

    for message in messages:
        date = message.get("date", "N/A")
        date_utc_plus_1 = convert_and_format_date_utc_plus_1(date)
        if filter_date:
            try:
                message_date = datetime.strptime(date_utc_plus_1, "%H:%M:%S %d/%m/%Y")
            except ValueError:
                continue  # Skip messages with invalid dates
            if message_date.date() != filter_date:
                continue

        message_text = clean_message_text(message.get("message", "N/A"))
        token_name = extract_token_name(message_text)
        url = ""
        x_com_link = ""  # Initialize x.com link

        # Extract links from entities
        for entity in message.get("entities", []):
            if entity.get("_") == "MessageEntityTextUrl":
                entity_url = entity.get("url", "")
                if entity_url.startswith("https://dexscreener.com/"):
                    url = entity_url
                elif entity_url.startswith("https://x.com/"):
                    x_com_link = entity_url  # Extract x.com link

        # Extract x.com link from message text if not found in entities
        if not x_com_link:
            x_com_match = re.search(r'https://x\.com/\S+', message_text)
            if x_com_match:
                x_com_link = x_com_match.group(0)

        # **Updated Extraction of Chart Links from Message Text**
        # If chart link not found in entities, search in message text
        if not url:
            chart_match = re.search(r'https://dexscreener\.com/\S+', message_text)
            if chart_match:
                url = chart_match.group(0)

        views = message.get("views", "0")
        forwards = message.get("forwards", "0")

        processed_data["messages"].append({
            "date": date_utc_plus_1,
            "token_name": token_name,
            "message_text": message_text,
            "url": url,
            "x_com_link": x_com_link,  # Add x.com link to processed data
            "views": views,
            "forwards": forwards
        })

    process_messages(processed_data)


def process_messages(processed_data):
    chart_counter = Counter()
    chart_timestamps = defaultdict(list)
    likes_data = defaultdict(list)
    retweets_data = defaultdict(list)
    replies_data = defaultdict(list)
    bookmarks_data = defaultdict(list)
    disparity_data = defaultdict(lambda: defaultdict(list))
    disparity_dates = defaultdict(lambda: defaultdict(list))
    top_metrics_data = defaultdict(list)

    for message in processed_data["messages"]:
        date = message.get("date", "")
        try:
            datetime_obj = datetime.strptime(date, "%H:%M:%S %d/%m/%Y")
        except ValueError:
            continue  # Skip messages with invalid dates
        message_text = message["message_text"]

        token_name = message["token_name"]
        chart_href = message["url"]
        x_com_link = message.get("x_com_link", "")
        likes_text = retweets_text = replies_text = bookmarks_text = ""

        found_likes = found_retweets = found_replies = found_bookmarks = False
        for text_item in message_text.split('\n'):
            if " Likes: " in text_item:
                likes_text = text_item.split(" Likes: ")[-1]
                found_likes = True
            elif " Retweets: " in text_item:
                retweets_text = text_item.split(" Retweets: ")[-1]
                found_retweets = True
            elif " Replies: " in text_item:
                replies_text = text_item.split(" Replies: ")[-1]
                found_replies = True
            elif " Bookmarks: " in text_item:
                bookmarks_text = text_item.split(" Bookmarks: ")[-1]
                found_bookmarks = True

        if chart_href:
            key = (token_name, chart_href)  # **Exclude x_com_link from key for Most Recurring Charts**
            chart_counter[key] += 1
            chart_timestamps[key].append(datetime_obj)

            # Update data collections using key with x_com_link included
            detailed_key = (token_name, chart_href, x_com_link)
            if found_likes:
                likes_data[detailed_key].append(parse_metric(likes_text))
                disparity = calculate_disparity(likes_text)
                disparity_data['Likes'][detailed_key].append(disparity)
                disparity_dates['Likes'][detailed_key].append(date)
            if found_retweets:
                retweets_data[detailed_key].append(parse_metric(retweets_text))
                disparity = calculate_disparity(retweets_text)
                disparity_data['Retweets'][detailed_key].append(disparity)
                disparity_dates['Retweets'][detailed_key].append(date)
            if found_replies:
                replies_data[detailed_key].append(parse_metric(replies_text))
                disparity = calculate_disparity(replies_text)
                disparity_data['Replies'][detailed_key].append(disparity)
                disparity_dates['Replies'][detailed_key].append(date)
            if found_bookmarks:
                bookmarks_data[detailed_key].append(parse_metric(bookmarks_text))
                disparity = calculate_disparity(bookmarks_text)
                disparity_data['Bookmarks'][detailed_key].append(disparity)
                disparity_dates['Bookmarks'][detailed_key].append(date)

            top_metrics_data['Likes'].append((token_name, "Likes", parse_metric(likes_text), date, chart_href, x_com_link))
            top_metrics_data['Retweets'].append((token_name, "Retweets", parse_metric(retweets_text), date, chart_href, x_com_link))
            top_metrics_data['Replies'].append((token_name, "Replies", parse_metric(replies_text), date, chart_href, x_com_link))
            top_metrics_data['Bookmarks'].append((token_name, "Bookmarks", parse_metric(bookmarks_text), date, chart_href, x_com_link))

    prepare_and_save_tables(processed_data, chart_counter, chart_timestamps, likes_data, retweets_data, replies_data, bookmarks_data, disparity_data, disparity_dates, top_metrics_data)


def prepare_and_save_tables(processed_data, chart_counter, chart_timestamps, likes_data, retweets_data, replies_data, bookmarks_data, disparity_data, disparity_dates, top_metrics_data):
    disparity_tables = {}
    for metric in ['Likes', 'Retweets', 'Replies', 'Bookmarks']:
        popping_table_data = []
        for key, disparities in disparity_data[metric].items():
            token_name, chart_href, x_com_link = key
            if disparities:
                max_disparity = max(disparities)
                max_disparity_index = disparities.index(max_disparity)
                disparity_date = disparity_dates[metric][key][max_disparity_index]
            else:
                max_disparity = 0
                disparity_date = "N/A"
            popping_table_data.append((token_name, chart_href, x_com_link, max_disparity, disparity_date))

        popping_table_data.sort(key=lambda x: x[3], reverse=True)
        disparity_tables[metric] = popping_table_data[:10]

    metrics_tables = {}
    for metric in ['Likes', 'Retweets', 'Replies', 'Bookmarks']:
        top_metrics_data[metric].sort(key=lambda x: x[2], reverse=True)
        metrics_tables[metric] = top_metrics_data[metric][:10]

    most_recurring_charts = []
    for key, count in chart_counter.most_common(10):
        token_name, chart_href = key
        avg_time_diff_str = calculate_avg_time_diffs(chart_timestamps[key]) or "N/A"

        # Collect metrics data across all x_com_links for the same token_name and chart_href
        likes_list = []
        retweets_list = []
        replies_list = []
        bookmarks_list = []
        for detailed_key in likes_data.keys():
            if detailed_key[0] == token_name and detailed_key[1] == chart_href:
                likes_list.extend(likes_data[detailed_key])
                retweets_list.extend(retweets_data.get(detailed_key, []))
                replies_list.extend(replies_data.get(detailed_key, []))
                bookmarks_list.extend(bookmarks_data.get(detailed_key, []))

        avg_likes = sum(likes_list) / len(likes_list) if likes_list else 0
        avg_retweets = sum(retweets_list) / len(retweets_list) if retweets_list else 0
        avg_replies = sum(replies_list) / len(replies_list) if replies_list else 0
        avg_bookmarks = sum(bookmarks_list) / len(bookmarks_list) if bookmarks_list else 0

        most_recurring_charts.append((token_name, chart_href, count, avg_time_diff_str, f"{avg_likes:.2f}", f"{avg_retweets:.2f}", f"{avg_replies:.2f}", f"{avg_bookmarks:.2f}"))

    # Ensure 'views' and 'forwards' are integers and sort accordingly
    for message in processed_data["messages"]:
        try:
            message["views"] = int(message["views"])
        except ValueError:
            message["views"] = 0
        try:
            message["forwards"] = int(message["forwards"])
        except ValueError:
            message["forwards"] = 0

    top_views = sorted(processed_data["messages"], key=lambda x: x["views"], reverse=True)[:10]
    top_forwards = sorted(processed_data["messages"], key=lambda x: x["forwards"], reverse=True)[:10]

    save_to_excel = input("\nDo you want to save the tables to an Excel file? (y/n): ").strip().lower()

    if save_to_excel == "y":
        # Ensure the output directory exists
        if not os.path.exists(output_directory):
            os.makedirs(output_directory)

        use_date_name = input("Do you want to use the current date as the file name? (y/n): ").strip().lower()

        if use_date_name == "y":
            current_date = datetime.now().strftime("%d.%m.%Y")
            file_name_base = f"{current_date}"
            file_path = generate_versioned_filename(file_name_base, output_directory)
        else:
            file_name = input("Enter the Excel file name (without extension): ").strip()
            if not file_name:
                file_name_base = datetime.now().strftime("%d.%m.%Y")
                file_path = generate_versioned_filename(file_name_base, output_directory)
            else:
                file_path = generate_versioned_filename(file_name, output_directory)

        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            # Writing Most Recurring Charts table as the first sheet
            # Exclude x.com Link from this sheet
            df_recurring_charts = pd.DataFrame(most_recurring_charts, columns=["Token", "📈 Chart Link", "Occur.", "Avg Time (m:s)", "❤️", "🔄", "💬", "🔖"])
            df_recurring_charts.to_excel(writer, sheet_name="Most Recurring Charts", index=False)

            worksheet = writer.sheets["Most Recurring Charts"]
            for i, col in enumerate(df_recurring_charts.columns):
                max_len = max(df_recurring_charts[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + i)].width = max_len

            # Writing Top 10 Disparity tables for all metrics in one sheet
            row = 0
            for metric, data in disparity_tables.items():
                if not data:  # Skip if no data
                    continue
                df = pd.DataFrame(data, columns=["Token", "📈 Chart Link", "x.com Link", f"Max {metric} Disparity", "Date"])
                df.to_excel(writer, sheet_name="Top 10 Disparities", index=False, startrow=row)

                worksheet = writer.sheets["Top 10 Disparities"]
                for i, col in enumerate(df.columns):
                    max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                    worksheet.column_dimensions[chr(65 + i)].width = max_len
                row += len(df) + 3  # Adding space between tables

            # Writing Top Metrics Instances for all metrics in one sheet
            row = 0
            for metric, data in metrics_tables.items():
                df = pd.DataFrame(data, columns=["Token", "Metric", "Value", "Date", "📈 Chart Link", "x.com Link"])
                df.to_excel(writer, sheet_name="Top Metrics Instances", index=False, startrow=row)

                worksheet = writer.sheets["Top Metrics Instances"]
                for i, col in enumerate(df.columns):
                    max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                    worksheet.column_dimensions[chr(65 + i)].width = max_len
                row += len(df) + 3  # Adding space between tables

            # Writing Top Views and Forwards in a new sheet
            df_top_views = pd.DataFrame(top_views)
            df_top_forwards = pd.DataFrame(top_forwards)

            # Add x.com link to the DataFrames
            df_top_views['x.com Link'] = df_top_views['x_com_link']
            df_top_forwards['x.com Link'] = df_top_forwards['x_com_link']

            # Select columns to display
            df_top_views = df_top_views[["date", "token_name", "message_text", "url", "x.com Link", "views", "forwards"]]
            df_top_forwards = df_top_forwards[["date", "token_name", "message_text", "url", "x.com Link", "views", "forwards"]]

            # Write the tables to the sheet
            df_top_views.to_excel(writer, sheet_name="Top Views & Forwards", index=False, startrow=0)

            df_top_forwards.to_excel(writer, sheet_name="Top Views & Forwards", index=False, startrow=len(df_top_views) + 2)

            worksheet = writer.sheets["Top Views & Forwards"]
            for i, col in enumerate(df_top_views.columns):
                max_len_views = max(df_top_views[col].astype(str).map(len).max(), len(col)) + 2
                max_len_forwards = max(df_top_forwards[col].astype(str).map(len).max(), len(col)) + 2
                max_len = max(max_len_views, max_len_forwards)
                column_letter = chr(65 + i)
                worksheet.column_dimensions[column_letter].width = max_len

        print(f"\nThe tables have been saved to {file_path}")

        open_file = input("Do you want to open the Excel file now? (y/n): ").strip().lower()
        if open_file == "y":
            try:
                if os.name == 'nt':  # For Windows
                    os.startfile(file_path)
                elif os.name == 'posix':
                    subprocess.Popen(['open', file_path])
            except Exception as e:
                print(f"Unable to open the file: {e}")
    else:
        print("\nThe tables were not saved.")


def generate_versioned_filename(base_name, directory, extension="xlsx"):
    version = 1
    while True:
        versioned_name = f"{base_name} V{version}.{extension}"
        file_path = os.path.join(directory, versioned_name)
        if not os.path.exists(file_path):
            return file_path
        version += 1


def read_input(prompt):
    """Reads input from the user in a non-blocking way."""
    return input(prompt)


def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%H:%M:%S %d/%m/%Y")
        return True
    except ValueError:
        return False


async def fetch_messages(client, channel, start_date):
    messages = []
    try:
        async for message in tqdm(client.iter_messages(channel, reverse=True), desc="Fetching messages"):
            # Ensure we only capture messages from the start_date onwards
            if message.date < start_date:
                continue
            if message.date > end_date:
                continue

            message_dict = message.to_dict()
            serialized_message = serialize_message(message_dict)
            messages.append(serialized_message)
    except (FloodWaitError, RPCError) as e:
        print(f"An error occurred: {e}")
        await asyncio.sleep(e.seconds if isinstance(e, FloodWaitError) else 5)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    return messages


async def shillbot_main():
    client = TelegramClient('session_name', api_id, api_hash)

    await client.start()
    if not await client.is_user_authorized():
        try:
            await client.sign_in(phone)
        except SessionPasswordNeededError:
            password = getpass('Please enter your password: ')
            await client.sign_in(password=password)

    # Fetch the channel by its URL or username
    channel = await client.get_entity(channel_url)

    # Prompt for the start date
    start_date = get_start_date()

    messages = await fetch_messages(client, channel, start_date)

    # Save to JSON
    with open('raidboard_chat_history.json', 'w', encoding='utf-8') as f:
        json.dump(messages, f, ensure_ascii=False, indent=4)

    # Call the function to process and display the messages
    display_selected_fields(messages)


def search_token_instance(token_id):
    # Prompt the user to fetch new messages or use existing data
    use_existing_data = input("Do you want to use existing data (Y) or fetch new messages (N)? Enter Y or N: ").strip().upper()

    if use_existing_data == 'N':
        # If the user chooses to fetch new messages, run the Shillbot main function
        asyncio.run(shillbot_main())

    # Load the JSON data from the file
    with open('raidboard_chat_history.json', 'r', encoding='utf-8') as f:
        messages = json.load(f)

    # Initialize counters and data structures
    chart_counter = Counter()
    chart_timestamps = defaultdict(list)
    likes_data = defaultdict(list)
    retweets_data = defaultdict(list)
    replies_data = defaultdict(list)
    bookmarks_data = defaultdict(list)
    x_com_instances = []  # To store instances with https://x.com/ links
    all_token_counts = Counter()

    # First, count occurrences of all tokens
    for message in messages:
        message_text = message.get("message", "")
        token_name = extract_token_name(message_text)
        if token_name:
            all_token_counts[token_name] += 1

    # Sort tokens by occurrences to assign ranks
    sorted_tokens = sorted(all_token_counts.items(), key=lambda x: x[1], reverse=True)
    token_ranks = {token: rank + 1 for rank, (token, _) in enumerate(sorted_tokens)}

    # Search through the messages for the specified token ID
    for message in messages:
        if search_token_in_message(message, token_id):
            message_text = message.get("message", "")
            token_name = extract_token_name(message_text)
            url = None

            # Correctly extract the URL that starts with "https://dexscreener.com/"
            # **Updated to include the correct pattern**
            if "entities" in message:
                for entity in message["entities"]:
                    if entity.get("_") == "MessageEntityTextUrl":
                        temp_url = entity.get("url", "")
                        if temp_url.startswith("https://dexscreener.com/"):
                            url = temp_url
                            break

            # If URL not found in entities, search in message text
            if not url:
                chart_match = re.search(r'https://dexscreener\.com/\S+', message_text)
                if chart_match:
                    url = chart_match.group(0)

            chart_href = url

            date_str = message.get("date", "")
            if date_str:
                try:
                    datetime_obj = datetime.fromisoformat(date_str)
                except ValueError:
                    datetime_obj = None
            else:
                datetime_obj = None

            # Add to x.com instances if the link exists
            if "https://x.com/" in message_text:
                x_com_link = re.search(r'https://x\.com/\S+', message_text)
                if x_com_link:
                    x_com_link = x_com_link.group(0)
                    datetime_obj_utc_plus_one = convert_and_format_date_utc_plus_1(datetime_obj.isoformat()) if datetime_obj else "Invalid date"
                    x_com_instances.append((token_name, x_com_link, datetime_obj_utc_plus_one))

            if chart_href and datetime_obj:  # Only use valid datetime objects
                chart_counter[(token_name, chart_href)] += 1
                chart_timestamps[(token_name, chart_href)].append(datetime_obj)

                likes_text = retweets_text = replies_text = bookmarks_text = ""

                # Process the message text for metrics
                for text_item in message_text.split('\n'):
                    if " Likes: " in text_item:
                        likes_text = text_item.split(" Likes: ")[-1]
                    elif " Retweets: " in text_item:
                        retweets_text = text_item.split(" Retweets: ")[-1]
                    elif " Replies: " in text_item:
                        replies_text = text_item.split(" Replies: ")[-1]
                    elif " Bookmarks: " in text_item:
                        bookmarks_text = text_item.split(" Bookmarks: ")[-1]

                key = (token_name, chart_href)
                likes_data[key].append(parse_metric(likes_text))
                retweets_data[key].append(parse_metric(retweets_text))
                replies_data[key].append(parse_metric(replies_text))
                bookmarks_data[key].append(parse_metric(bookmarks_text))

    # Print the main token table if data was found
    if chart_counter:
        for (token_name, chart_href), count in chart_counter.items():
            avg_time_diff_str = calculate_avg_time_diffs(chart_timestamps[(token_name, chart_href)]) or "N/A"
            avg_likes = sum(likes_data[(token_name, chart_href)]) / len(likes_data[(token_name, chart_href)]) if likes_data[(token_name, chart_href)] else 0
            avg_retweets = sum(retweets_data[(token_name, chart_href)]) / len(retweets_data[(token_name, chart_href)]) if retweets_data[(token_name, chart_href)] else 0
            avg_replies = sum(replies_data[(token_name, chart_href)]) / len(replies_data[(token_name, chart_href)]) if replies_data[(token_name, chart_href)] else 0
            avg_bookmarks = sum(bookmarks_data[(token_name, chart_href)]) / len(bookmarks_data[(token_name, chart_href)]) if bookmarks_data[(token_name, chart_href)] else 0

            rank = token_ranks.get(token_name, "N/A")

            table_data = [[rank, token_name, chart_href, count, avg_time_diff_str, f"{avg_likes:.2f}", f"{avg_retweets:.2f}", f"{avg_replies:.2f}", f"{avg_bookmarks:.2f}"]]
            headers = ["Rank", "Token", "📈 Chart Link", "Occur.", "Avg Time (m:s)", "❤️", "🔄", "💬", "🔖"]
            print(tabulate(table_data, headers, tablefmt="fancy_grid"))

    # Print the most recent 5 x.com instances table if data was found
    if x_com_instances:
        x_com_instances = sorted(x_com_instances, key=lambda x: x[2], reverse=True)  # Sort by most recent
        most_recent_x_com_instances = x_com_instances[:5]  # Get only the 5 most recent instances
        x_com_table_data = [[token_name, x_com_link, datetime_obj] for token_name, x_com_link, datetime_obj in most_recent_x_com_instances]
        x_com_headers = ["Token", "🔗 x.com Link", "Time (UTC+1)"]
        print(tabulate(x_com_table_data, x_com_headers, tablefmt="fancy_grid"))
    else:
        print(f"\nNo information could be found from this token,")
        print(f"this can mean the token CA is incorrect or there")
        print(f"has been no interaction between raidboard and this token '{token_id}'.")


def main():
    choice = input("Choose an option:\n1. Shillbot (check what is being shilled today or from a specific date)\n2. Search a token\nEnter 1 or 2: ").strip()

    if choice == '1':
        # Run the Shillbot
        asyncio.run(shillbot_main())
    elif choice == '2':
        # Run the Detect feature
        token_id = input("Enter the token CA (contract address): ").strip()
        search_token_instance(token_id)
    else:
        print("Invalid choice. Exiting.")


if __name__ == "__main__":
    main()
