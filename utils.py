from PIL import Image
import tiktoken
import base64
import math
import io
import re


def ensure_jpeg(image_bytes):
    # Load the image from bytes
    image = Image.open(io.BytesIO(image_bytes))

    # Check if image is already JPEG
    if image.format == "JPEG":
        return image_bytes
    else:
        # Convert the image to JPEG
        with io.BytesIO() as output:
            image.save(output, format="JPEG")
            new_image_bytes = output.getvalue()
        return new_image_bytes


def calculate_image_token_cost(image_bytes, detail="high"):
    # Complying with https://platform.openai.com/docs/guides/vision
    # Load the image from bytes
    image = Image.open(io.BytesIO(image_bytes))

    # Get image dimensions
    width, height = image.size

    if detail == "low":
        # Fixed cost for low detail images
        return 85
    elif detail == "high":
        # Scale down to fit within 2048x2048 if necessary
        if width > 2048 or height > 2048:
            aspect_ratio = width / height
            if aspect_ratio > 1:  # Width is greater than height
                width = 2048
                height = int(2048 / aspect_ratio)
            else:
                height = 2048
                width = int(2048 * aspect_ratio)

        # Scale such that the shortest side is 768px
        if width < height:
            scaling_factor = 768 / width
        else:
            scaling_factor = 768 / height

        new_width = int(width * scaling_factor)
        new_height = int(height * scaling_factor)

        # Calculate number of 512px squares needed
        num_squares_width = math.ceil(new_width / 512)
        num_squares_height = math.ceil(new_height / 512)
        total_squares = num_squares_width * num_squares_height

        # Calculate total token cost
        total_cost = total_squares * 170 + 85

        return total_cost


def count_tokens_in_messages(messages):
    encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = 0
    for message in messages:
        num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
        for key, value in message.items():
            if isinstance(value, list):
                for item in value:
                    if item["type"] == "text":
                        prompt = item["text"]
                    elif item["type"] == "image_url":
                        # data:image/jpeg;base64,
                        b64_str_image: str = item["image_url"]["url"].replace(
                            "data:image/jpeg;base64", ""
                        )
                        image_bytes = base64.b64decode(b64_str_image.encode("utf-8"))
                        num_tokens += calculate_image_token_cost(
                            image_bytes, item["image_url"]["detail"]
                        )
            else:
                prompt = value
            num_tokens += len(encoding.encode(prompt))
            if key == "name":  # if there's a name, the role is omitted
                num_tokens += -1  # role is always required and always 1 token
    num_tokens += 2  # every reply is primed with <im_start>assistant
    return num_tokens


def escape_outside(text):
    # Define patterns for markdown styles
    patterns = [
        r"\*(.+?)\*",  # Bold
        r"_(.+?)_",  # Italic
        r"__(.+?)__",  # Underline
        r"~(.+?)~",  # Strikethrough
        r"\|\|(.+?)\|\|",  # Spoiler
        r"\`(.+?)\`",  # Single line code block
    ]

    # Function to escape special characters, skipping valid markdown
    def escape_chars(match):
        # Check each pattern to see if it matches the entire match group
        for pattern in patterns:
            if re.fullmatch(pattern, match.group(0)):
                return match.group(0)  # Return the markdown unchanged
        # If no patterns match, escape all matched special characters
        return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", match.group(0))

    # This regex matches any markdown pattern or any special character
    combined_pattern = r"(\`.+?\`|\*.+?\*|_.+?_|__.+?__|~.+?~|\|\|.+?\|\||[_*[\]()~`>#+\-=|{}.!])"

    temp = re.sub(combined_pattern, escape_chars, text)
    res = ""
    for i, char in enumerate(temp):
        if char in "!(){}[].>#=+-":
            if i > 0:
                if temp[i - 1] != "\\":
                    res += "\\" + char
                else:
                    res += char
            else:
                res += "\\" + char
        else:
            res += char

    return res


def escape_markdown(text: str):
    res = ""
    triple_backticks = "```"
    escape_inside_block = "\\`"
    inside_code = False
    for t in text.split(triple_backticks):
        if inside_code:
            for ec in escape_inside_block:
                if ec in t:
                    t = t.replace(ec, f"\\{ec}")
        else:
            t = escape_outside(t)
        res += t
        res += triple_backticks
        inside_code = not inside_code
    if res.count(triple_backticks) % 2 != 0:
        res = res[:-3]
    return res
