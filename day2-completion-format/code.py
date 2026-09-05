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

# Per DeepSeek API docs (response_format): only "text" and "json_object" are supported.
RESPONSE_FORMATS = ["text", "json_object"]

# Per DeepSeek API docs (stop): up to 16 sequences are accepted.
STOP_SEQUENCES_LIMIT = 16

DEFAULT_MODEL = MODELS[0]
DEFAULT_RESPONSE_FORMAT = RESPONSE_FORMATS[0]
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


def print_welcome(current_model: str, stop: list[str] | None, response_format: str) -> None:
    print("=" * 60)
    print("DeepSeek CLI Chat")
    print("=" * 60)
    print("Type your message and press Enter to send it to the model.")
    print("Commands:")
    print("  /models          - list available models and switch the active one")
    print("  /stop            - set stop sequence(s) (up to 16)")
    print("  /response_format - choose response format: text or json_object")
    print("  /exit            - quit the CLI")
    print("-" * 60)
    print(f"Current model: {current_model}")
    print(f"Current stop sequences: {', '.join(stop) if stop else 'not set'}")
    print(f"Current response format: {response_format}")
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


def set_stop_sequences(current_stop: list[str] | None) -> list[str] | None:
    current_display = ", ".join(current_stop) if current_stop else "not set"
    print(f"Current stop sequences: {current_display}")
    raw = input(f"Enter stop sequence(s), comma-separated (max {STOP_SEQUENCES_LIMIT}), or press Enter to clear: ")

    if not raw.strip():
        print("Stop sequences cleared.")
        return None

    sequences = [part.strip() for part in raw.split(",") if part.strip()]
    if not sequences:
        print("No valid sequences entered. Keeping current value.")
        return current_stop

    if len(sequences) > STOP_SEQUENCES_LIMIT:
        print(f"Only the first {STOP_SEQUENCES_LIMIT} sequences will be used (DeepSeek allows up to {STOP_SEQUENCES_LIMIT}).")
        sequences = sequences[:STOP_SEQUENCES_LIMIT]

    print(f"Stop sequences set to: {sequences}")
    return sequences


def set_response_format(current_format: str) -> str:
    print("Available response formats:")
    for idx, fmt in enumerate(RESPONSE_FORMATS, start=1):
        marker = " (current)" if fmt == current_format else ""
        print(f"  {idx}. {fmt}{marker}")

    choice = input("Enter format number (or press Enter to keep current): ").strip()
    if not choice:
        return current_format

    if choice.isdigit() and 1 <= int(choice) <= len(RESPONSE_FORMATS):
        selected = RESPONSE_FORMATS[int(choice) - 1]
        print(f"Switched to response format: {selected}")
        if selected == "json_object":
            print(
                "Note: DeepSeek requires your message to explicitly ask for JSON output "
                "(e.g. mention 'json' and describe the desired structure), otherwise "
                "generation may run until the token limit is reached."
            )
        return selected

    print("Invalid choice, keeping current format.")
    return current_format


def ask_model(
    client: OpenAI,
    model: str,
    user_message: str,
    stop: list[str] | None,
    response_format: str,
) -> str:
    request_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    }
    if stop:
        request_kwargs["stop"] = stop
    if response_format != "text":
        request_kwargs["response_format"] = {"type": response_format}

    try:
        response = client.chat.completions.create(**request_kwargs)
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
    stop: list[str] | None = None
    response_format = DEFAULT_RESPONSE_FORMAT
    print_welcome(current_model, stop, response_format)

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

        if user_input == "/stop":
            stop = set_stop_sequences(stop)
            continue

        if user_input == "/response_format":
            response_format = set_response_format(response_format)
            continue

        answer = ask_model(client, current_model, user_input, stop, response_format)
        print(f"\n{answer}")


if __name__ == "__main__":
    main()
