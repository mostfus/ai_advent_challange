"""Simple CLI to chat with the DeepSeek LLM API."""

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI, APIError, APIConnectionError, AuthenticationError

MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
]

DEFAULT_MODEL = MODELS[0]
SYSTEM_PROMPT = "You are a helpful assistant."
BASE_URL = "https://api.deepseek.com"


def load_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Error: DEEPSEEK_API_KEY is not set.")
        print("Create a .env file in this folder (see .env.example) and add your DeepSeek API key.")
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def print_welcome(current_model: str) -> None:
    print("=" * 60)
    print("DeepSeek CLI Chat")
    print("=" * 60)
    print("Type your message and press Enter to send it to the model.")
    print("Commands:")
    print("  /models   - list available models and switch the active one")
    print("  /exit     - quit the CLI")
    print(f"Current model: {current_model}")
    print("=" * 60)


def choose_model(current_model: str) -> str:
    print("Available models:")
    for idx, name in enumerate(MODELS, start=1):
        marker = " (current)" if name == current_model else ""
        print(f"  {idx}. {name}{marker}")

    choice = input("Enter model number (or press Enter to keep current): ").strip()
    if not choice:
        return current_model

    if choice.isdigit() and 1 <= int(choice) <= len(MODELS):
        selected = MODELS[int(choice) - 1]
        print(f"Switched to model: {selected}")
        return selected

    print("Invalid choice, keeping current model.")
    return current_model


def ask_model(client: OpenAI, model: str, user_message: str) -> str:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content
    except AuthenticationError:
        return "Error: authentication failed. Check your DEEPSEEK_API_KEY."
    except APIConnectionError:
        return "Error: could not connect to the DeepSeek API. Check your network connection."
    except APIError as exc:
        return f"Error: DeepSeek API returned an error: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any unexpected error to the user
        return f"Error: unexpected error: {exc}"


def main() -> None:
    client = load_client()
    current_model = DEFAULT_MODEL
    print_welcome(current_model)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("Goodbye!")
            break

        if user_input == "/models":
            current_model = choose_model(current_model)
            continue

        answer = ask_model(client, current_model, user_input)
        print(f"\n{answer}")


if __name__ == "__main__":
    main()
