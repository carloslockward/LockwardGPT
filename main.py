import telebot
from telebot.types import Message, BotCommand
import openai

TELEGRAM_API_KEY = ""
OPENAI_API_KEY = ""


class ChatGPT:
    def __init__(
        self, api_key, model_engine="text-davinci-003", max_tokens=1024, context_size=4
    ) -> None:
        self.max_tokens = max_tokens
        self.model_engine = model_engine
        self.context = []
        self.perma_context = ["You are LockwardGPT, Carlos Fernandez's personal AI"]
        self.context_size = context_size
        openai.api_key = api_key

    def chat(self, prompt: str, talking_to=None):
        extra = f"You are talking to {talking_to}" if talking_to else ""
        full_prompt = f"context: {' '.join(self.perma_context)} {extra} {' '.join(self.context)} \n\n prompt: {prompt}"
        completion = openai.Completion.create(
            engine=self.model_engine,
            prompt=full_prompt,
            max_tokens=self.max_tokens,
            temperature=0.5,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
        )

        response = completion.choices[0].text

        print(f"Prompt: {prompt}")
        print(f"ChatGPT: {response}")
        print(f"Context: {self.context}")

        if response:
            # Remove old context
            for _ in range(min(len(self.context) - (self.context_size - 2), 0)):
                self.context.pop(0)

            self.context.append(prompt)
            self.context.append(response)

        return response


class LockwardBot:
    def __init__(self, chatgpt: ChatGPT, telegram_api_key) -> None:
        self.bot = telebot.TeleBot(telegram_api_key)
        self.bot.register_message_handler(self.handle_msg)
        self.callback = {}
        self.chatgpt = chatgpt

        self.commands = {
            "test": {"func": self.__do_nothing, "desc": "Does Nothing... Pretty lame huh?"}
        }

        command_list = []
        for key, val in self.commands.items():
            command_list.append(BotCommand(f"/{key}", val.get("desc", "")))

        self.bot.set_my_commands(command_list)
        self.admins = ["carloslockward"]

    def __do_nothing(self, message):
        pass

    def determine_function(self, message: Message):
        if message.content_type == "text":
            msg = message.text.strip()

            # Handle messages with commands.
            if msg.startswith("/"):

                for cmd, cmd_info in self.commands.items():
                    if f"/{cmd}" in msg:
                        return cmd_info["func"]
            # Handle generic messages
            return self.chat

    def chat(self, message: Message):
        msg = message.text
        username = message.from_user.username
        chat_id = message.chat.id
        if username in self.admins:
            response = self.chatgpt.chat(msg, message.from_user.full_name)
            self.bot.send_message(chat_id, response)
        else:
            self.bot.send_message(
                chat_id,
                "You dont have access to LockwardGPT. Ask @carloslockward to grant you access.",
            )

    def handle_msg(self, message: Message):
        func = self.determine_function(message)
        func(message)

    def start_listening(self):
        print("Bot started!")
        try:
            self.bot.polling()
        except KeyboardInterrupt:
            print("Bot is done!")


if __name__ == "__main__":
    chatgpt = ChatGPT(OPENAI_API_KEY)
    bot = LockwardBot(chatgpt, TELEGRAM_API_KEY)
    bot.start_listening()
