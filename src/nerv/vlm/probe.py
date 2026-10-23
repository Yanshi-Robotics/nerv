"""One request, local visual challenge, no robot tools or model-based grading."""
import io
import json
import secrets

from PIL import Image, ImageDraw

from .errors import ModelError

# Small, separated high-contrast shapes; the answer never enters prompt metadata.
IMAGE_SIZE = (320, 160)
COLORS = {"red": "#D62828", "blue": "#1769C2", "green": "#238636", "yellow": "#F2C200"}
SHAPES = ("circle", "square", "triangle")
PROMPT = ('What is in this image? Return only JSON with "left" and "right", each an '
          'object with "shape" (circle, square or triangle) and "color" '
          '(red, blue, green or yellow). No tools.')


def challenge():
    image = Image.new("RGB", IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    answer = {}
    for side, offset in (("left", 0), ("right", IMAGE_SIZE[0] // 2)):
        shape, color = secrets.choice(SHAPES), secrets.choice(tuple(COLORS))
        answer[side] = {"shape": shape, "color": color}
        box = (offset + 30, 30, offset + 130, 130)
        if shape == "circle":
            draw.ellipse(box, fill=COLORS[color])
        elif shape == "square":
            draw.rectangle(box, fill=COLORS[color])
        else:
            draw.polygon([(offset + 80, 30), (offset + 30, 130), (offset + 130, 130)], fill=COLORS[color])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), answer


def matches(reply, expected):
    if reply.finish_status != "completed" or reply.tool_calls:
        raise ModelError("invalid_response")
    text = reply.text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        return json.loads(text) == expected
    except (ValueError, TypeError):
        return False
