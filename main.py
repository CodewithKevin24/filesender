import os
import time
import json
import uuid
import certifi
import telebot
from telebot import types
from flask import Flask, request, abort
from pymongo import MongoClient
from pymongo.server_api import ServerApi
import sys

# Environment variables for configuration
TOKEN = os.environ.get('TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID'))
ADMINS = [int(x) for x in os.environ.get('ADMINS').split(',')]
ADMINS.append(OWNER_ID)
PRIVATE_GROUP_ID = int(os.environ.get('PRIVATE_GROUP_ID'))
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID'))
CALLURL = os.environ['WEBHOOK_URL']
FORCE_SUB_CHANNEL = int(os.environ.get("FORCE_SUB_CHANNEL", "0"))
CONSOLE_CHANNEL_ID = os.environ.get('CONSOLE_CHANNEL_ID')
MONGO_URI = os.environ.get('MONGO_URI')

if not TOKEN or not MONGO_URI:
    sys.exit("TOKEN or MONGO_URI environment variable is not set.")

# Initialize the bot
bot = telebot.TeleBot(TOKEN)
bot.remove_webhook()

# Initialize Flask app
app = Flask(__name__)

# Initialize MongoDB client with SSL configuration
client = MongoClient(MONGO_URI, server_api=ServerApi('1'), tlsCAFile=certifi.where())
db = client['telegram_bot']
users_collection = db['users']
file_storage_collection = db['file_storage']

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
    bot.send_message(CONSOLE_CHANNEL_ID, "Pinged your deployment. You successfully connected to MongoDB!", parse_mode="HTML")
except Exception as e:
    print(e)
    bot.send_message(CONSOLE_CHANNEL_ID, f"Failed to connect to MongoDB: {e}", parse_mode="HTML")

def set_webhook_with_retry(url, max_retries=5, backoff_factor=2):
    for attempt in range(max_retries):
        try:
            bot.set_webhook(url=url, drop_pending_updates=False)
            print("Webhook set successfully")
            break
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                retry_after = e.result_json['parameters']['retry_after']
                print(f"Rate limit exceeded. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                print(f"Failed to set webhook: {e}")
                if attempt < max_retries - 1:
                    sleep_time = backoff_factor ** attempt
                    print(f"Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                else:
                    print("Max retries reached. Exiting.")
                    sys.exit(1)

set_webhook_with_retry(CALLURL)

@app.route('/')
def host():
    base_url = request.base_url
    return f"The HOST URL of this application is: {base_url}"

@app.route('/', methods=['POST'])
def receive_updates():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data(as_text=True)
        update = telebot.types.Update.de_json(json_string)
        if update is not None:
            try:
                bot.process_new_updates([update])
                if update.message and update.message.from_user:
                    user_first_name = update.message.from_user.first_name
                    user_id = update.message.from_user.id
                    console_message = f"User {user_first_name} (Chat ID: {user_id}) Getting Videos."
                    bot.send_message(CONSOLE_CHANNEL_ID, console_message, parse_mode="HTML")
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    print(f"Rate limit exceeded. Waiting for 10 seconds before retrying.")
                    time.sleep(10)
                    bot.process_new_updates([update])
                else:
                    print(f"Telegram API error: {e}")
        else:
            print("Received None update")
        return '', 200
    else:
        abort(403)

def save_user(chat_id):
    try:
        users_collection.update_one(
            {'chat_id': chat_id},
            {'$set': {'chat_id': chat_id}},
            upsert=True
        )
        print(f"User {chat_id} saved to the database.")
    except Exception as e:
        print(f"Failed to save user {chat_id}: {e}")

def save_file_storage(unique_id, file_info):
    try:
        file_storage_collection.update_one(
            {'unique_id': unique_id},
            {'$set': {'file_id': file_info[0], 'file_type': file_info[1]}},
            upsert=True
        )
        print(f"File {unique_id} saved to the database.")
    except Exception as e:
        print(f"Failed to save file {unique_id}: {e}")

def load_file_storage(unique_id):
    try:
        file_info = file_storage_collection.find_one({'unique_id': unique_id})
        if file_info:
            return (file_info['file_id'], file_info['file_type'])
        else:
            return None
    except Exception as e:
        print(f"Failed to load file {unique_id}: {e}")
        return None

@bot.message_handler(commands=['start'])
def handle_start(message):
    args = message.text.split()
    save_user(message.chat.id)
    if FORCE_SUB_CHANNEL != 0 and not user_joined_force_channel(message.chat.id):
        send_force_subscribe_message(message)
        return
    if len(args) > 1:
        unique_id = args[1]
        send_file_by_id(message, unique_id)
    else:
        send_welcome_message(message)

ALLOWED_PRIVATE_CHANNEL_IDS = [int(x) for x in os.environ.get('ALLOWED_PRIVATE_CHANNEL_IDS', '').split(',') if x]

def user_joined_force_channel(user_id):
    try:
        # Bypass the check for the owner
        if user_id == OWNER_ID:
            return True
        # Check if the user has joined the force subscribe channel
        user = bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return user.status in ['member', 'administrator']
    except Exception as e:
        print(f"Error checking if user joined force channel: {e}")
        return False

def send_force_subscribe_message(message):
    bot.send_message(
        message.chat.id,
        "*You need to join our compulsory channel😇 \n\nClick the link below to join 🔗 :*",
        reply_markup=types.InlineKeyboardMarkup(
            [[types.InlineKeyboardButton("Join Channel", url=f"https://t.me/{bot.get_chat(FORCE_SUB_CHANNEL).username}")]]
        ),
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['sendall'])
def handle_sendall(message):
    if message.chat.id == PRIVATE_GROUP_ID and (message.from_user.id in ADMINS or message.from_user.id == OWNER_ID):
        msg_text = message.text.split(' ', 1)
        if len(msg_text) > 1:
            bot.send_message(PRIVATE_GROUP_ID, "Please provide a message or photo to send to all users.")
            bot.register_next_step_handler(message, send_message_to_all, msg_text[1])
        else:
            bot.send_message(PRIVATE_GROUP_ID, "Please provide a message to send.")

def send_message_to_all(message, msg):
    users = users_collection.find()
    sent_count = 0
    blocked_count = 0
    try:
        if message.content_type == 'text':
            for user in users:
                user_id = user['chat_id']
                try:
                    bot.send_message(user_id, msg, parse_mode="HTML", protect_content=True)
                    sent_count += 1
                except telebot.apihelper.ApiException as e:
                    if "bot was blocked by the user" in str(e):
                        blocked_count += 1
                        # Do not delete user from database
        elif message.content_type == 'photo':
            for user in users:
                user_id = user['chat_id']
                try:
                    bot.send_photo(user_id, message.photo[-1].file_id, caption=msg, parse_mode="HTML", protect_content=True)
                    sent_count += 1
                except telebot.apihelper.ApiException as e:
                    if "bot was blocked by the user" in str(e):
                        blocked_count += 1
                        # Do not delete user from database
        else:
            bot.send_message(PRIVATE_GROUP_ID, "Invalid content type. Please provide a message or photo.")
        bot.send_message(PRIVATE_GROUP_ID, f"Message sent to {sent_count} users. {blocked_count} users have blocked the bot.")
    except Exception as e:
        bot.send_message(PRIVATE_GROUP_ID, "Error: " + str(e))

WAIT_MSG_HANDLE_FILES = "<b>⚙️ Processing link...</b>"

@bot.message_handler(func=lambda message: message.chat.id == PRIVATE_GROUP_ID and message.from_user.id in ADMINS, content_types=['photo', 'video', 'document', 'audio', 'voice'])
def handle_files(message):
    try:
        file_info = None
        if message.photo:
            file_info = (message.photo[-1].file_id, 'photo')
        elif message.video:
            file_info = (message.video.file_id, 'video')
        elif message.document:
            file_info = (message.document.file_id, 'document')
        elif message.audio:
            file_info = (message.audio.file_id, 'audio')
        elif message.voice:
            file_info = (message.voice.file_id, 'voice')
        if file_info:
            unique_id = str(uuid.uuid4())
            while load_file_storage(unique_id):  # Check for UUID collision
                unique_id = str(uuid.uuid4())
            save_file_storage(unique_id, file_info)
            shareable_link = f"https://t.me/{bot.get_me().username}?start={unique_id}"
            processing_msg = bot.send_message(message.chat.id, WAIT_MSG_HANDLE_FILES, parse_mode='HTML')
            bot.edit_message_text(
                f"<b>{message.from_user.first_name}, your file is stored!</b>\n\n<code>Use this link to access it 🔗 :\n||{shareable_link}||\n\nLeave Reaction🤪😇</code>\n\n{shareable_link}",
                message.chat.id,
                processing_msg.message_id,
                parse_mode='HTML',
                #protect_content=False
            )
        else:
            bot.reply_to(message, 'Failed to process the file.')
    except Exception as e:
        print("Error in handle_files:", e)
        bot.reply_to(message, 'An error occurred while processing the file.')

def send_file_by_id(message, unique_id):
    file_info = load_file_storage(unique_id)
    if file_info:
        send_file(message.chat.id, file_info[0], file_info[1])
    else:
        bot.send_message(message.chat.id, "File not found. It might have been deleted or the link is incorrect.")

def send_welcome_message(message):
    user_name = message.from_user.first_name or message.from_user.username
    greeting_text = f"Hello, *{user_name}*! 😉\n\nYou need to Join Our Chat Channel From "
    markup = types.InlineKeyboardMarkup(row_width=2)
    channel_button = types.InlineKeyboardButton("Chat Channel", url="https://t.me/+tvWHQ58slElmNmQ1")
    close_button = types.InlineKeyboardButton("Close", callback_data="close")
    markup.add(channel_button, close_button)
    bot.send_message(message.chat.id, greeting_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "close")
def close_button(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except telebot.apihelper.ApiException as e:
        print(f"Failed to delete message with buttons: {e}")
    try:
        prev_message_id = call.message.message_id - 1
        try:
            bot.delete_message(call.message.chat.id, prev_message_id)
        except telebot.apihelper.ApiException as prev_e:
            if "message can't be deleted" in str(prev_e):
                print(f"Skipping deletion of previous message (ID: {prev_message_id}): {prev_e}")
            else:
                print(f"Failed to delete previous message (ID: {prev_message_id}): {prev_e}")
    except telebot.apihelper.ApiException as e:
        print(f"Error: {e}")

WAIT_MSG = "<b>⏳ Please Wait...</b>"

def send_file(chat_id, file_id, file_type):
    processing_msg = None
    if not file_id:
        bot.send_message(chat_id, "File ID is not available. The file cannot be sent.")
        return
    try:
        processing_msg = bot.send_message(chat_id, WAIT_MSG, parse_mode='HTML')
        if file_type == 'photo':
            bot.send_photo(chat_id, file_id, protect_content=True)
        elif file_type == 'video':
            bot.send_video(chat_id, file_id, protect_content=True)
        elif file_type == 'document':
            bot.send_document(chat_id, file_id, protect_content=True)
        elif file_type == 'audio':
            bot.send_audio(chat_id, file_id, protect_content=True)
        elif file_type == 'voice':
            bot.send_voice(chat_id, file_id, protect_content=True)
        else:
            bot.send_message(chat_id, "Unsupported file type")
        if processing_msg:
            bot.delete_message(chat_id, processing_msg.message_id)
    except Exception as e:
        if processing_msg:
            bot.delete_message(chat_id, processing_msg.message_id)
        bot.send_message(chat_id, f"An error occurred while sending the file: {e}")

@bot.message_handler(commands=['help'])
def handle_help(message):
    bot.send_message(message.chat.id, "<b>Join the posting channel \n/start for more links!</b>", parse_mode="HTML")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'audio', 'video', 'document', 'sticker', 'voice', 'location', 'contact', 'video_note'])
def forward_to_log_channel(message):
    try:
        bot.forward_message(LOG_CHANNEL_ID, message.chat.id, message.message_id)
    except Exception as e:
        print(f"Failed to forward message: {e}")

if __name__ == '__main__':
    print("Bot is Running")
    app.run(host='0.0.0.0', port=5000, debug=True)
