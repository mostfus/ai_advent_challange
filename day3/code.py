"""Simple CLI to chat with the DeepSeek LLM API."""

import os
import sys
import time
from dataclasses import dataclass

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

SYSTEM_PROMPTS = [
    ("Default", "You are a helpful assistant."),
    (
        "Step-by-step reasoning",
        "You are a careful, methodical assistant. Break every problem down into "
        "clear, numbered steps and work through them one at a time before giving "
        "your final answer. Show your reasoning, then clearly state the final "
        "answer at the end.",
    ),
]

DEFAULT_MODEL = MODELS[0]
DEFAULT_RESPONSE_FORMAT = RESPONSE_FORMATS[0]
DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPTS[0][1]
BASE_URL = "https://api.deepseek.com"

COMMANDS = {"/exit", "/models", "/system", "/stop", "/response_format"}


def load_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Error: DEEPSEEK_API_KEY is not set.")
        print("Create a .env file in this folder (see .env.example) and add your DeepSeek API key.")
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def print_welcome(
    current_model: str,
    stop: list[str] | None,
    response_format: str,
    current_system_prompt: str,
) -> None:
    print("=" * 60)
    print("DeepSeek CLI Chat")
    print("=" * 60)
    print("Type your message and press Enter to send it to the model.")
    print("For a multi-line message, keep pressing Enter between lines (this also")
    print("lets you paste text with line breaks); an empty line sends the message.")
    print("Commands:")
    print("  /models          - list available models and switch the active one")
    print("  /system          - list system prompts and switch the active one")
    print("  /stop            - set stop sequence(s) (up to 16)")
    print("  /response_format - choose response format: text or json_object")
    print("  /exit            - quit the CLI")
    print("-" * 60)
    print(f"Current model: {current_model}")
    print(f"Current system prompt: {current_system_prompt}")
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


def choose_system_prompt(current_system_prompt: str) -> str:
    print("Available system prompts:")
    for idx, (name, prompt) in enumerate(SYSTEM_PROMPTS, start=1):
        marker = " (current)" if prompt == current_system_prompt else ""
        print(f"  {idx}. {name}{marker}")
    print("  0. Enter a custom system prompt")

    choice = input("Enter choice number (or press Enter to keep current): ").strip()
    if not choice:
        return current_system_prompt

    if choice == "0":
        custom_prompt = input("Enter custom system prompt: ").strip()
        if not custom_prompt:
            print("Empty input, keeping current system prompt.")
            return current_system_prompt
        print("System prompt updated.")
        return custom_prompt

    if choice.isdigit() and 1 <= int(choice) <= len(SYSTEM_PROMPTS):
        name, prompt = SYSTEM_PROMPTS[int(choice) - 1]
        print(f"Switched to system prompt: {name}")
        return prompt

    print("Invalid choice, keeping current system prompt.")
    return current_system_prompt


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


def read_message(prompt: str) -> str | None:
    """Read one user message, supporting text of any length pasted across lines.

    A blank first line (just pressing Enter) or one of the known ``/`` commands
    is returned immediately, same as before. Anything else is treated as the
    start of a message: more lines keep being read - which is what lets a
    multi-line paste come through intact instead of being cut after its first
    line break - until a blank line is entered, then all the lines are joined
    back together and returned as the message to send.

    Returns ``None`` if the user wants to quit (EOF / Ctrl+C).
    """
    try:
        first_line = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None

    stripped_first = first_line.strip()
    if not stripped_first or stripped_first in COMMANDS:
        return stripped_first

    lines = [first_line]
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "":
            break
        lines.append(line)

    return "\n".join(lines).strip()


@dataclass
class ModelReply:
    """Everything about one exchange with the model - not just the text answer.

    `request` and `response` are plain JSON-able dicts (the exact kwargs sent
    to the API, and the API's raw response) so callers - namely the web GUI's
    debug panel - can display exactly what went over the wire, alongside
    token usage (inside `response["usage"]`) and timing.
    """

    answer: str
    request: dict
    response: dict | None = None
    error: str | None = None
    elapsed_ms: float | None = None


def ask_model(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_message: str,
    stop: list[str] | None,
    response_format: str,
) -> ModelReply:
    request_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    if stop:
        request_kwargs["stop"] = stop
    if response_format != "text":
        request_kwargs["response_format"] = {"type": response_format}

    start = time.perf_counter()
    try:
        response = client.chat.completions.create(**request_kwargs)
    except AuthenticationError:
        response = None
        error = "Error: authentication failed. Check your DEEPSEEK_API_KEY."
    except APIConnectionError:
        response = None
        error = "Error: could not connect to the DeepSeek API. Check your network connection."
    except APIError as exc:
        response = None
        error = f"Error: DeepSeek API returned an error: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any unexpected error to the user
        response = None
        error = f"Error: unexpected error: {exc}"
    else:
        error = None
    elapsed_ms = (time.perf_counter() - start) * 1000

    if error is not None:
        return ModelReply(answer=error, request=request_kwargs, error=error, elapsed_ms=elapsed_ms)

    return ModelReply(
        answer=response.choices[0].message.content,
        request=request_kwargs,
        response=response.model_dump(mode="json"),
        elapsed_ms=elapsed_ms,
    )


def main() -> None:
    client = load_client()
    current_model = DEFAULT_MODEL
    current_system_prompt = DEFAULT_SYSTEM_PROMPT
    stop: list[str] | None = None
    response_format = DEFAULT_RESPONSE_FORMAT
    print_welcome(current_model, stop, response_format, current_system_prompt)

    while True:
        user_input = read_message("\n> ")

        if user_input is None:
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

        if user_input == "/system":
            current_system_prompt = choose_system_prompt(current_system_prompt)
            continue

        if user_input == "/stop":
            stop = set_stop_sequences(stop)
            continue

        if user_input == "/response_format":
            response_format = set_response_format(response_format)
            continue

        reply = ask_model(client, current_model, current_system_prompt, user_input, stop, response_format)
        print(f"\n{reply.answer}")


if __name__ == "__main__":
    main()
