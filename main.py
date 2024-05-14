import json
import base64
import openai
import telebot
import traceback
from utils import *
from time import sleep
from pathlib import Path
from openai import RateLimitError
from telebot.types import Message, BotCommand, BotCommandScopeChat

TELEGRAM_API_KEY = ""
OPENAI_API_KEY = ""


class ChatGPT:
    def __init__(
        self, api_key, model_engine="gpt-4o", max_tokens=2048, context={}, context_size=4
    ) -> None:
        self.max_tokens = max_tokens
        self.model_engine = model_engine
        self.context = context
        self.perma_context = [
            "You are LockwardGPT, Carlos Fernandez's personal AI",
            "If asked for code you return it in markdown code block format",
            "If asked to generate an image in any way, you will respond with 'IMAGE_REQUESTED_123' followed by the prompt. This will automatically trigger an API call to DALL-E 3, effectively allowing you to generate images directly."
            "You always try to keep your answers as short and concise as possible unless asked otherwise",
        ]
        self.context_size = context_size
        self.image_size = 1024
        self.openai_client = openai.OpenAI(api_key=api_key)

    def __trim_messages(self, messages: list, trim_to):
        trim_to = int(trim_to)
        res = messages.copy()
        while True:
            if len(res) > 2:
                res.pop(1)
                if count_tokens_in_messages(res) <= trim_to:
                    return res
            else:
                return res

    def image(self, prompt: str):
        response = self.openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size=f"{self.image_size}x{self.image_size}",
            quality="hd",
        )
        return response.data[0].url

    def chat(self, prompt: str, chat_id, image_data=None, talking_to=None):
        if chat_id not in self.context.keys():
            self.context[chat_id] = []
        extra = f"You are talking to {talking_to}" if talking_to else ""

        if image_data:
            messages = (
                [{"role": "system", "content": f"{' '.join(self.perma_context)} {extra}"}]
                + self.context[chat_id]
                + [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": image_data},
                        ],
                    }
                ]
            )
        else:
            messages = (
                [{"role": "system", "content": f"{' '.join(self.perma_context)} {extra}"}]
                + self.context[chat_id]
                + [{"role": "user", "content": prompt}]
            )

        num_tokens = count_tokens_in_messages(messages)

        local_max_tokens = self.max_tokens

        if num_tokens > 2048:
            print("!! Message too long. Trimming... !!")
            messages = self.__trim_messages(messages, 2048)

            new_num_tokens = count_tokens_in_messages(messages)
            print(f"Old tokens: {num_tokens}. New tokens: {new_num_tokens}")

            # If after trimming message is still too long, lets remove some tokens from the response to make room.
            if new_num_tokens > 2048:
                local_max_tokens = 4096 - new_num_tokens
                print(f"Had to reduce response length! Max response tokens: {local_max_tokens}")

        for _ in range(3):
            completion = self.openai_client.chat.completions.create(
                model=self.model_engine,
                messages=messages,
                max_tokens=local_max_tokens,
                temperature=0.6,
                frequency_penalty=0.1,
                presence_penalty=0.1,
                timeout=60,
            )
            response = completion.choices[0].message
            usage = completion.usage.total_tokens
            if response:
                break
            sleep(0.5)

        if response:
            print(f"{talking_to.split(' ')[0] if talking_to else 'Prompt'}: {prompt}")
            print(f"ChatGPT: {response.content}")

            # Remove old context
            for _ in range(min(len(self.context[chat_id]) - (self.context_size - 2), 0)):
                self.context[chat_id].pop(0)

            self.context[chat_id].append({"role": "user", "content": prompt})
            self.context[chat_id].append({"role": "assistant", "content": response.content})

        return response.content, usage


class CustomMessage:
    def __init__(self, text, chat, from_user):
        self.chat = chat
        self.text = text
        self.from_user = from_user


class LockwardBot:
    def __init__(self, chatgpt: ChatGPT, telegram_api_key, user_path="users.json") -> None:
        self.bot = telebot.TeleBot(telegram_api_key)
        self.bot.register_message_handler(self.handle_msg)
        self.callback = {}
        self.chatgpt = chatgpt
        self.user_path = user_path
        self.token_usage = {}
        self.image_usage = {}
        self.commands = {
            "context": {"func": self.get_context, "desc": "Gets the current context."},
            "context_length": {
                "func": self.get_context_length,
                "desc": "Gets the current context length.",
            },
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
            "token_usage": {
                "func": self.get_token_usage,
                "desc": "Get general token usage by username",
            },
            "image_usage": {
                "func": self.get_image_usage,
                "desc": "Get general image usage by username",
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

        context = self.chatgpt.context.get(str(chat_id))

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

    def __split_string(self, string: str, max_length=4090) -> list[str]:
        return [string[i : i + max_length] for i in range(0, len(string), max_length)]

    def send_message_bot(self, *args, **kwargs):
        ex = None
        # If message fails, retry 4 more times
        for _ in range(5):
            try:
                new_text = ""
                if len(args) >= 2:
                    if len(args[1]) > 4096:
                        new_text = args[1]
                elif "text" in kwargs.keys():
                    if len(kwargs["text"]) > 4096:
                        new_text = kwargs["text"]

                if new_text:
                    start_block = False
                    last = None
                    for t in self.__split_string(new_text):
                        if "```" in new_text:
                            if start_block:
                                t = "```" + t
                                start_block = False
                            if t.count("```") % 2 != 0:
                                t += "```"
                                start_block = True
                        new_args = list(args)
                        if "text" in kwargs.keys():
                            kwargs["text"] = t
                        else:
                            new_args[1] = t
                        last = self.bot.send_message(*new_args, **kwargs)
                    return last

                else:
                    return self.bot.send_message(*args, **kwargs)

            except Exception as e:
                if "can't parse" in str(e):
                    raise e
                elif "message is too long" in str(e):
                    raise e
                else:
                    ex = e
            sleep(0.5)
        if ex is not None:
            raise ex

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
            if len(users_list) > 1:
                self.send_message_bot(chat_id, f"Granted access to {len(users_list)} users")
            else:
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

        if str(chat_id) in self.chatgpt.context.keys():
            self.chatgpt.context[str(chat_id)] = []

        self.send_message_bot(chat_id, "Context has been cleared!")

    def get_context_length(self, message: Message):
        chat_id = message.chat.id
        num_tokens = 0
        if str(chat_id) in self.chatgpt.context.keys():
            num_tokens = count_tokens_in_messages(self.chatgpt.context[str(chat_id)])

        self.send_message_bot(chat_id, f"Your context is {num_tokens} tokens long.")

    def get_token_usage(self, message: Message):
        chat_id = message.chat.id
        if len(self.token_usage) > 0:
            res = "Token usage per Username:\n"
            for username, num_tokens in sorted(
                self.token_usage.items(), key=lambda item: item[1], reverse=True
            ):
                res += f"@{username}: {num_tokens}\n"

            self.send_message_bot(chat_id, res.strip())
        else:
            self.send_message_bot(chat_id, "No token usage so far...")

    def get_image_usage(self, message: Message):
        chat_id = message.chat.id
        if len(self.image_usage) > 0:
            res = "Number of images generated per Username:\n"
            for username, num_images in sorted(
                self.image_usage.items(), key=lambda item: item[1], reverse=True
            ):
                res += f"@{username}: {num_images}\n"

            self.send_message_bot(chat_id, res.strip())
        else:
            self.send_message_bot(chat_id, "No image usage so far...")

    def generate_image(self, message: Message):
        msg = message.text
        chat_id = message.chat.id
        username = message.from_user.username

        msg = msg.replace("/image", "").strip()

        if msg:
            self.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
            try:
                image_url = self.chatgpt.image(msg)
                print(f"Image Generated! Prompt: '{msg}'")
            except Exception as e:
                if "safety system" in str(e) or "content filters" in str(e):
                    self.send_message_bot(
                        chat_id,
                        "This image can't be generated because of OpenAI's safety systems.",
                    )
                    return
                else:
                    if username in self.admins:
                        try:
                            self.send_message_bot(
                                chat_id,
                                f"An error has occurred: Exception:\n```{traceback.format_exc()}```",
                                parse_mode="Markdown",
                            )
                        except Exception as e:
                            if "can't parse" in str(e):
                                self.send_message_bot(
                                    chat_id,
                                    f"An error has occurred: Exception:\n```{traceback.format_exc()}```",
                                )
                    else:
                        self.send_message_bot(
                            chat_id,
                            f"An error has occurred. Try clearing the context and try again. If the issue persists contact @carloslockward",
                        )
                        raise e
            if image_url:
                if username not in self.image_usage.keys():
                    self.image_usage[username] = 0
                self.image_usage[username] += 1
                self.bot.send_photo(chat_id, photo=image_url)
        else:
            self.send_message_bot(
                chat_id,
                "You must provide a prompt\. Usage:\n `/image <prompt>`",
                parse_mode="MarkdownV2",
            )

    def get_users(self):
        users = {"users": ["carloslockward"]}
        try:
            with Path(self.user_path).open("r") as uf:
                users = json.load(uf)
        except:
            pass
        return users

    def command_not_found(self, message: Message):
        msg = message.text.strip()
        chat_id = message.chat.id
        self.bot.send_chat_action(chat_id=chat_id, action="typing")
        self.send_message_bot(chat_id, f"Command {msg.split()[0]} is invalid")

    def determine_function(self, message: Message):
        if message.content_type == "text":
            msg = message.text.strip()

            # Handle messages with commands.
            if msg.startswith("/"):
                for cmd, cmd_info in self.commands.items():
                    if msg.split()[0] == f"/{cmd}":
                        return cmd_info["func"]

                if message.from_user.username in self.admins:
                    for cmd, cmd_info in self.admin_commands.items():
                        if msg.split()[0] == f"/{cmd}":
                            return cmd_info["func"]
                return self.command_not_found
            # Handle generic messages
            return self.chat
        elif message.content_type == "photo":
            # TODO: See if there is a neat way of supporting multiple images.
            return self.chat
        return self.__do_nothing

    def chat(self, message: Message):
        if message.content_type == "photo":
            if message.caption:
                msg = message.caption
            else:
                msg = ""
        else:
            msg = message.text
        chat_id = message.chat.id
        username = message.from_user.username

        # TODO: This only lasts 5 seconds, should find a way of making it last as long as the promt completion
        self.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            image_data = None
            if message.content_type == "photo":
                detail = "low"
                if "-h" in msg or "--high" in msg:
                    detail = "high"
                    msg = msg.replace("--high", "").replace("-h", "").strip()
                # Download file and ensure is jpeg!
                file = self.bot.get_file(message.photo[-1].file_id)
                downloaded_file = ensure_jpeg(self.bot.download_file(file.file_path))
                base64_image = base64.b64encode(downloaded_file).decode("utf-8")
                image_data = {"url": f"data:image/jpeg;base64,{base64_image}", "detail": detail}
            response, usage = self.chatgpt.chat(
                msg, str(chat_id), image_data, message.from_user.full_name
            )
            if username not in self.token_usage.keys():
                self.token_usage[username] = 0
            self.token_usage[username] += usage
        except RateLimitError as rle:
            exception_text = traceback.format_exc()
            if message.from_user.username in self.admins:
                self.send_message_bot(
                    chat_id,
                    f"OpenAI servers are overloaded. Try again later. \nException:\n```{exception_text}```",
                    parse_mode="Markdown",
                )
            else:
                if "insufficient_quota" in exception_text:
                    self.send_message_bot(
                        chat_id, "LockwardGPT is unavailable at the moment. Try again later."
                    )
                else:
                    self.send_message_bot(
                        chat_id, f"OpenAI servers are overloaded. Try again later."
                    )
            return
        except Exception as e:
            if message.from_user.username in self.admins:
                try:
                    self.send_message_bot(
                        chat_id,
                        f"An error has occurred: Exception:\n```{traceback.format_exc()}```",
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    if "can't parse" in str(e):
                        self.send_message_bot(
                            chat_id,
                            f"An error has occurred: Exception:\n```{traceback.format_exc()}```",
                        )
            else:
                self.send_message_bot(
                    chat_id,
                    f"An error has occurred. Try clearing the context and try again. If the issue persists contact @carloslockward",
                )
                raise e
            return
        if response:
            # If we need to generate an image!
            if response.startswith("IMAGE_REQUESTED_123"):
                self.generate_image(
                    CustomMessage(
                        response.replace("IMAGE_REQUESTED_123:", ""),
                        message.chat,
                        message.from_user,
                    )
                )
            else:
                # Otherwise send ChatGPT's response to the user!
                try:
                    self.send_message_bot(
                        chat_id,
                        escape_markdown(response),
                        parse_mode="MarkdownV2",
                    )
                except Exception as e:
                    if "can't parse" in str(e):
                        try:
                            print("!! Couldn't parse Markdown V2 !!")
                            self.send_message_bot(chat_id, response, parse_mode="Markdown")
                        except Exception as e2:
                            if "can't parse" in str(e2):
                                print("!! Couldn't parse Markdown !!")
                                self.send_message_bot(chat_id, response)
                            else:
                                raise e2
                    else:
                        raise e

    def handle_msg(self, message: Message):
        username = message.from_user.username
        chat_id = message.chat.id
        if not self.init_admin_cmds and username in self.admins:
            self.bot.set_my_commands(
                self.admin_command_list + self.command_list,
                scope=BotCommandScopeChat(chat_id),
            )
            self.init_admin_cmds = True
        if username in self.users["users"]:
            func = self.determine_function(message)
            func(message)
        else:
            self.send_message_bot(
                chat_id,
                "You dont have access to LockwardGPT. Ask @carloslockward to grant you access.",
            )

    def start_listening(self):
        print("Bot started!")
        self.bot.infinity_polling()


if __name__ == "__main__":
    while True:
        try:
            context = {}
            if Path("context.json").exists():
                try:
                    with Path("context.json").open("r") as cf:
                        context = json.load(cf)
                except:
                    print(f"Failed to load context! Exception:\n {traceback.format_exc()}")
            chatgpt = ChatGPT(OPENAI_API_KEY, context=context, context_size=10)
            bot = LockwardBot(chatgpt, TELEGRAM_API_KEY)
            bot.start_listening()
            print("Bot is done!")
            break
        except KeyboardInterrupt:
            print("Bot is done!")
            break
        except Exception as e:
            print(f"Exception {e}. Restarting...")
        finally:
            try:
                print("Saving context...")
                with Path("context.json").open("w") as cf:
                    json.dump(chatgpt.context, cf)
            except:
                print(f"Failed to save context! Exception:\n {traceback.format_exc()}")
