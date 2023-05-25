import json
import openai
import telebot
from time import sleep
from pathlib import Path
from openai.error import RateLimitError
from telebot.types import Message, BotCommand, BotCommandScopeChat

TELEGRAM_API_KEY = ""
OPENAI_API_KEY = ""


class ChatGPT:
    def __init__(
        self, api_key, model_engine="gpt-3.5-turbo", max_tokens=1024, context_size=4
    ) -> None:
        self.max_tokens = max_tokens
        self.model_engine = model_engine
        self.context = {}
        self.perma_context = [
            "You are LockwardGPT, Carlos Fernandez's personal AI",
            "If asked for code you return it in markdown code block format",
            "You always try to keep your answers as short and concise as possible unless asked otherwise",
        ]
        self.context_size = context_size
        self.image_size = 512
        openai.api_key = api_key

    def __count_tokens(self, text):
        tokens = text.split()  # split the string into tokens
        num_tokens = len(tokens)  # count the number of tokens
        return num_tokens

    def __count_tokens_in_messages(self, messages):
        full_content = ""
        for i in messages:
            full_content += i["content"] + " "

        full_content = full_content.strip()
        return self.__count_tokens(full_content)

    def __trim_messages(self, messages, rm_num=300):
        rm_num = int(rm_num)
        rm_count = 0
        res = []
        for i in messages:
            if rm_count < rm_num:
                if i["role"] != "system":
                    tokens = i["content"].split()
                    if len(tokens) > rm_num:
                        res.append({"role": i["role"], "content": i["content"][rm_num:]})
                        rm_count = rm_num
                    elif len(tokens) == rm_num:
                        rm_count = rm_num
                    else:
                        rm_count = len(tokens)
                else:
                    res.append(i)
            else:
                res.append(i)
        return res

    def image(self, prompt: str):
        response = openai.Image.create(
            prompt=prompt, n=1, size=f"{self.image_size}x{self.image_size}"
        )
        return response["data"][0]["url"]

    def chat(self, prompt: str, chat_id, talking_to=None):
        if chat_id not in self.context.keys():
            self.context[chat_id] = []
        extra = f"You are talking to {talking_to}" if talking_to else ""

        messages = (
            [{"role": "system", "content": f"{' '.join(self.perma_context)} {extra}"}]
            + self.context[chat_id]
            + [{"role": "user", "content": prompt}]
        )

        num_tokens = self.__count_tokens_in_messages(messages)

        if num_tokens * 2 > 4097.0:
            print("!! Message too long. Trimming... !!")
            messages = self.__trim_messages(messages, (num_tokens * 2) - 4097.0)

        for _ in range(3):
            completion = openai.ChatCompletion.create(
                model=self.model_engine,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=0.6,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0,
                timeout=60,
            )

            response = completion.choices[0].get("message")
            if response:
                break
            sleep(0.5)

        if response:
            print(f"{talking_to.split(' ')[0] if talking_to else 'Prompt'}: {prompt}")
            print(f"ChatGPT: {response['content']}")

            # Remove old context
            for _ in range(min(len(self.context[chat_id]) - (self.context_size - 2), 0)):
                self.context[chat_id].pop(0)

            self.context[chat_id].append({"role": "user", "content": prompt})
            self.context[chat_id].append(response)

        return response["content"]


class LockwardBot:
    def __init__(self, chatgpt: ChatGPT, telegram_api_key, user_path="users.json") -> None:
        self.bot = telebot.TeleBot(telegram_api_key)
        self.bot.register_message_handler(self.handle_msg)
        self.callback = {}
        self.chatgpt = chatgpt
        self.user_path = user_path
        self.commands = {
            "context": {"func": self.get_context, "desc": "Gets the current context."},
            "clear_context": {"func": self.clear_context, "desc": "Clears the current context."},
            "image": {
                "func": self.generate_image,
                "desc": "Generates an image based on the user's prompt.",
            },
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
                "desc": "Lists current allowed users. (Admin Only)",
            },
        }

        self.command_list = []
        for key, val in self.commands.items():
            self.command_list.append(BotCommand(f"/{key}", val.get("desc", "")))

        self.bot.set_my_commands(self.command_list)
        self.admins = ["carloslockward"]

        self.admin_command_list = []
        for key, val in self.admin_commands.items():
            self.admin_command_list.append(BotCommand(f"/{key}", val.get("desc", "")))

        self.init_admin_cmds = False

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
        return username

    def get_context(self, message: Message):
        chat_id = message.chat.id

        context = self.chatgpt.context.get(chat_id)

        if context is None or len(context) == 0:
            self.send_message_bot(chat_id, f"Context is currently empty")
            return

        full_context = ""
        for msg in context:
            full_context += (
                "\n"
                + ("LockwardGPT" if msg["role"] == "assistant" else message.from_user.full_name)
                + ": "
                + msg["content"]
            )

        self.send_message_bot(chat_id, f"Context: {full_context}")

    def __split_string(self, string: str, max_length=4096) -> list[str]:
        return [string[i : i + max_length] for i in range(0, len(string), max_length)]

    def send_message_bot(self, *args, **kwargs):
        ex = None
        for _ in range(5):
            try:
                new_text = ""
                if len(args) == 2:
                    if len(args[1]) > 4096:
                        new_text = args.pop(1)
                if "text" in kwargs.keys():
                    if len(kwargs["text"]) > 4096:
                        new_text = kwargs.pop("text")

                if new_text != "":
                    start_block = False
                    last = None
                    for t in self.__split_string(new_text):
                        if start_block:
                            t = "```" + t
                            start_block = False
                        else:
                            if t.count("```") % 2 != 0:
                                t += "```"
                                start_block = True

                        kwargs["text"] = t
                        last = self.bot.send_message(*args, **kwargs)
                    return last

                else:
                    return self.bot.send_message(*args, **kwargs)

            except Exception as e:
                ex = e
            sleep(0.5)
        if ex is not None:
            raise e

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
                else:
                    self.send_message_bot(chat_id, f"User @{clean_user} already had access!")
        if save:
            self.save_users()
            self.send_message_bot(chat_id, f"Granted access to user @{clean_user}")

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
            self.send_message_bot(chat_id, f"Revoked access to user @{clean_user}")

    def list_users(self, message: Message):
        self.users = self.get_users()
        chat_id = message.chat.id

        new_line = "\n"

        self.send_message_bot(
            chat_id, f"Current users are:\n\n{new_line.join(self.users['users'])}"
        )

    def clear_context(self, message: Message):
        chat_id = message.chat.id

        if chat_id in self.chatgpt.context.keys():
            self.chatgpt.context[chat_id] = []

        self.send_message_bot(chat_id, "Context has been cleared!")

    def generate_image(self, message: Message):
        msg = message.text
        chat_id = message.chat.id

        msg = msg.replace("/image", "").strip()

        if msg:
            try:
                image_url = self.chatgpt.image(msg)
            except Exception as e:
                if "safety system" in str(e):
                    self.send_message_bot(
                        chat_id,
                        "This image can't be generated because of OpenAI's safety system.",
                    )
                    return
                else:
                    raise e
            if image_url:
                self.bot.send_photo(chat_id, photo=image_url)
        else:
            self.send_message_bot(
                chat_id,
                "You must provide a prompt. Usage:\n `/image <prompt>`",
                parse_mode="Markdown",
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
            self.send_message_bot(chat_id, "OpenAI servers are overloaded. Try again later.")
            return
        if response:
            if "```" in response:
                self.send_message_bot(chat_id, response, parse_mode="Markdown")
            else:
                self.send_message_bot(chat_id, response)

    def handle_msg(self, message: Message):
        if not self.init_admin_cmds and message.from_user.username in self.admins:
            self.bot.set_my_commands(
                self.admin_command_list + self.command_list,
                scope=BotCommandScopeChat(message.chat.id),
            )
            self.init_admin_cmds = True
        if message.from_user.username in self.users["users"]:
            func = self.determine_function(message)
            func(message)
        else:
            self.send_message_bot(
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
