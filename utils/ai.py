import sys
import json

from groq import AsyncGroq, RateLimitError
from os import getenv
from dotenv import load_dotenv
from utils.helpers import get_env_path, load_config
from utils.error_notifications import webhook_log, print_error
from utils.logger import log_model_fallback

client = None
model = None
groq_models = []
current_model_index = 0


def init_ai():
    global client, model, groq_models, current_model_index
    env_path = get_env_path()
    config = load_config()

    load_dotenv(dotenv_path=env_path)

    api_key = getenv("GROQ_API_KEY")
    if not api_key:
        print("No GROQ_API_KEY found, exiting.")
        sys.exit(1)

    client = AsyncGroq(api_key=api_key)
    groq_models = config["bot"]["groq_models"]
    current_model_index = 0
    model = groq_models[0]


def fallback_model():
    global model, current_model_index
    if not groq_models:
        return False
    old_model = model
    current_model_index += 1
    if current_model_index >= len(groq_models):
        current_model_index = 0
        return False
    model = groq_models[current_model_index]
    log_model_fallback(old_model, model)
    return True


async def _create_completion(messages):
    """Attempt completion with automatic model fallback on rate limit."""
    global model
    if not client:
        init_ai()

    while True:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return response
        except RateLimitError as e:
            print(f"[AI] RateLimitError on {model}: {e}")
            if not fallback_model():
                raise
        except Exception as e:
            print(f"[AI] {type(e).__name__} on {model}: {e}")
            if not fallback_model():
                raise


async def generate_response(prompt, instructions, history=None):
    if not client:
        init_ai()
    try:
        messages = [{"role": "system", "content": instructions}]
        if history:
            messages += history
        else:
            messages.append({"role": "user", "content": prompt})

        response = await _create_completion(messages)
        return response.choices[0].message.content
    except Exception as e:
        print_error("AI Error", e)
        await webhook_log(None, e)
        raise


async def generate_response_image(prompt, instructions, image_url, history=None):
    if not client:
        init_ai()
    try:
        image_response = await client.chat.completions.create(
            model="meta-llama/llama-4-maverick-17b-128e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Describe / Explain in detail this image sent by a Discord user to an AI who will be responding to the message '{prompt}' based on your output as the AI cannot see the image. So make sure to tell the AI any key details about the image that you think are important to include in the response, especially any text on screen that the AI should be aware of.",
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        )

        prompt_with_image = f"{prompt} [Image of {image_response.choices[0].message.content}]"

        if history:
            history.append({"role": "user", "content": prompt_with_image})
            messages = [
                {
                    "role": "system",
                    "content": instructions + " Images will be described to you, with the description wrapped in [|description|], so understand that you are to respond to the description as if it were an image you can see.",
                },
                *history,
            ]
        else:
            history = [{"role": "user", "content": prompt_with_image}]
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt_with_image},
            ]

        response = await _create_completion(messages)
        history.append({"role": "assistant", "content": response.choices[0].message.content})
        return response.choices[0].message.content
    except Exception as e:
        print_error("AI image Error", e)
        await webhook_log(None, e)
        raise


async def extract_memory(user_message: str, assistant_reply: str) -> dict:
    """Ask the LLM to extract any new personal facts the user revealed."""
    if not client:
        init_ai()

    prompt = (
        f'User message: "{user_message}"\n'
        f'Assistant reply: "{assistant_reply}"\n\n'
        "Extract ONLY concrete facts the user explicitly stated about themselves. "
        'Return a JSON object like {"name": "John", "age": "22"}. '
        "Keys must be simple: name, age, location, job, hobby, game, relationship_status. "
        "Only include facts the USER clearly stated. "
        "If nothing new was revealed return exactly: {} "
        "Return ONLY the JSON object. No explanation, no markdown, no extra text."
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a JSON-only fact extractor. You output nothing except valid JSON objects."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.1,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        if not text.startswith("{"):
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except json.JSONDecodeError:
        return {}
    except Exception as e:
        print_error("Memory Extract Error", e)
        return {}
