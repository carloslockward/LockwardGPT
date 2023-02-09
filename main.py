import json
import openai
import telebot
from time import sleep
from pathlib import Path
from openai.error import RateLimitError
from telebot.types import Message, BotCommand

TELEGRAM_API_KEY = ""
OPENAI_API_KEY = ""


class ChatGPT:
    def __init__(
        self, api_key, model_engine="text-davinci-003", max_tokens=1024, context_size=4
    ) -> None:
        self.max_tokens = max_tokens
        self.model_engine = model_engine
        self.context = {}
        self.perma_context = [
            "You are LockwardGPT, Carlos Fernandez's personal AI",
            "If asked for code you return it in markdown code block format",
        ]
        self.context_size = context_size
        openai.api_key = api_key

    def chat(self, prompt: str, chat_id, talking_to=None):
        if chat_id not in self.context.keys():
            self.context[chat_id] = []
        extra = f"You are talking to {talking_to}" if talking_to else ""

        if "<END>" in prompt:
            prompt = prompt.replace("<END>", "")

        for _ in range(3):
            full_prompt = f"context: {' '.join(self.perma_context)} {extra} {' '.join(self.context[chat_id])} \n\n prompt: {prompt}<END>"
            completion = openai.Completion.create(
                engine=self.model_engine,
                prompt=full_prompt,
                max_tokens=self.max_tokens,
                temperature=0.5,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                stop="<END>",
            )

            response = completion.choices[0].text

            if response.strip():
                break
            sleep(0.5)

        print(f"{talking_to.split(' ')[0] if talking_to else 'Prompt'}: {prompt}")
        print(f"ChatGPT: {response.strip()}")
        print(f"Context: {' '.join(self.context[chat_id])}")

        if response:
            # Remove old context
            for _ in range(min(len(self.context[chat_id]) - (self.context_size - 2), 0)):
                self.context[chat_id].pop(0)

            self.context[chat_id].append(prompt)
            self.context[chat_id].append(response)

        return response


class LockwardBot:
    def __init__(self, chatgpt: ChatGPT, telegram_api_key, user_path="users.json") -> None:
        self.bot = telebot.TeleBot(telegram_api_key)
        self.bot.register_message_handler(self.handle_msg)
        self.callback = {}
        self.chatgpt = chatgpt
        self.user_path = user_path
        self.commands = {
            "test": {"func": self.__do_nothing, "desc": "Does nothing... Pretty lame right?"},
            "context": {"func": self.get_context, "desc": "Gets the current context."},
        }

        self.admin_commands = {
            "grant": {
                "func": self.grant_access,
                "desc": "Grants access to a user. (Admin Only) Usage: /grant <username>",
            },
            "revoke": {
                "func": self.revoke_access,
                "desc": "Revokes access to a user. (Admin Only) Usage: /revoke <username>",
            },
            "list_users": {
                "func": self.list_users,
                "desc": "Lists current allowed users.",
            },
        }

        command_list = []
        for key, val in self.commands.items():
            command_list.append(BotCommand(f"/{key}", val.get("desc", "")))

        self.bot.set_my_commands(command_list)
        self.admins = ["carloslockward"]

        if not Path(self.user_path).exists():
            Path(self.user_path).touch()
            self.users = {"users": ["carloslockward"]}
        else:
            self.users = self.get_users()

    def __do_nothing(self, message):
        pass

    def save_users(self):
        with Path(self.user_path).open("w") as j:
            json.dump(self.users, j)

    def is_user_valid(self, username):
        # TODO: Actually validate the username using regex or something else.
        return True

    def get_context(self, message: Message):
        chat_id = message.chat.id

        context = self.chatgpt.context.get(chat_id, ["Context is currently empty"])

        self.bot.send_message(chat_id, f"Context: {' '.join(context)}")

    def grant_access(self, message: Message):
        self.users = self.get_users()
        msg = message.text
        chat_id = message.chat.id
        users_list = msg.replace("/grant", "").strip().split(" ")
        save = False
        for user in users_list:
            if self.is_user_valid(user):
                clean_user = user.replace("@", "").strip()
                if clean_user not in self.users["users"]:
                    self.users["users"].append(clean_user)
                    save = True
        if save:
            self.save_users()
            self.bot.send_message(chat_id, f"Granted access to user @{clean_user}")

    def revoke_access(self, message: Message):
        self.users = self.get_users()
        msg = message.text
        chat_id = message.chat.id

        users_list = msg.replace("/revoke", "").strip().split(" ")
        save = False
        for user in users_list:
            if self.is_user_valid(user):
                clean_user = user.replace("@", "").strip()
                if clean_user in self.users["users"]:
                    self.users["users"].remove(clean_user)
                    save = True
        if save:
            self.save_users()
            self.bot.send_message(chat_id, f"Revoked access to user @{clean_user}")

    def list_users(self, message: Message):
        self.users = self.get_users()
        chat_id = message.chat.id

        new_line = "\n"

        self.bot.send_message(
            chat_id, f"Current users are:\n\n{new_line.join(self.users['users'])}"
        )

    def get_users(self):
        users = {"users": ["carloslockward"]}
        try:
            with Path(self.user_path).open("r") as uf:
                users = json.load(uf)
        except:
            pass
        return users

    def determine_function(self, message: Message):
        if message.content_type == "text":
            msg = message.text.strip()

            # Handle messages with commands.
            if msg.startswith("/"):
                for cmd, cmd_info in self.commands.items():
                    if f"/{cmd}" in msg:
                        return cmd_info["func"]

                if message.from_user.username in self.admins:
                    for cmd, cmd_info in self.admin_commands.items():
                        if f"/{cmd}" in msg:
                            return cmd_info["func"]
            # Handle generic messages
            return self.chat

    def chat(self, message: Message):
        msg = message.text
        chat_id = message.chat.id

        try:
            response = self.chatgpt.chat(msg, chat_id, message.from_user.full_name)
        except RateLimitError:
            self.bot.send_message(chat_id, "OpenAI servers are overloaded. Try again later.")
            return
        if response:
            if "```" in response:
                self.bot.send_message(chat_id, response, parse_mode="Markdown")
            else:
                self.bot.send_message(chat_id, response)

    def handle_msg(self, message: Message):
        if message.from_user.username in self.users["users"]:
            func = self.determine_function(message)
            func(message)
        else:
            self.bot.send_message(
                message.chat.id,
                "You dont have access to LockwardGPT. Ask @carloslockward to grant you access.",
            )

    def start_listening(self):
        print("Bot started!")
        self.bot.infinity_polling()


if __name__ == "__main__":
    while True:
        try:
            chatgpt = ChatGPT(OPENAI_API_KEY)
            bot = LockwardBot(chatgpt, TELEGRAM_API_KEY)
            bot.start_listening()
            print("Bot is done!")
            break
        except KeyboardInterrupt:
            print("Bot is done!")
            break
        except Exception as e:
            print(f"Exception {e}. Restarting...")
