import sys

from groq import AsyncGroq, RateLimitError
from openai import AsyncOpenAI as OpenAI
from os import getenv
from dotenv import load_dotenv
from utils.helpers import get_env_path, load_config
from utils.error_notifications import webhook_log, print_error

client = None
model = None
groq_models = []
current_model_index = 0


def init_ai():
    global client, model, groq_models, current_model_index
    env_path = get_env_path()
    config = load_config()

    load_dotenv(dotenv_path=env_path)

    if getenv("OPENAI_API_KEY"):
        client = OpenAI(api_key=getenv("OPENAI_API_KEY"))
        model = config["bot"]["openai_model"]
    elif getenv("GROQ_API_KEY"):
        client = AsyncGroq(api_key=getenv("GROQ_API_KEY"))
        groq_models = config["bot"]["groq_models"]
        current_model_index = 0
        model = groq_models[0]
    else:
        print("No API keys found, exiting.")
        sys.exit(1)


def fallback_model():
    """Switch to the next model in the list. Returns False if all exhausted."""
    global model, current_model_index
    if not groq_models:
        return False
    current_model_index += 1
    if current_model_index >= len(groq_models):
        current_model_index = 0  # reset for next time
        return False
    model = groq_models[current_model_index]
    print(f"[AI] Rate limited, switching to fallback model: {model}")
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
            # Reset to primary model on success
            if groq_models and model != groq_models[0]:
                model = groq_models[0]
                current_model_index = 0
            return response
        except RateLimitError:
            if not fallback_model():
                raise  # all models exhausted, let caller handle it


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
        return "Sorry, I couldn't generate a response."


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
        return "???"