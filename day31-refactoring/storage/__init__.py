"""Where everything lives when the process is not running.

Day 7's whole point: a place to put a transcript so it is still there next
time. This package knows nothing about agents, HTTP or LLMs. It stores
dictionaries and hands them back.

    core  ->  ConversationStore.load(id)   ->  Agent(history=...)
          ->  ConversationStore.append_turn(id, ...)

**Why on the server and not in the browser.** The transcript has two jobs:
it is drawn on screen, and it is replayed to the model - and the second one
decides where it has to live. Chat-completions APIs are stateless: every
request carries the whole conversation, and in this app the agent is what
assembles that request. The store owns it; the browser only draws what it is
given.

**Why JSON files and not a database.** One file per record (or one file per
store), written whole, replaced atomically. It is the smallest thing that
answers the requirement, and it stays legible - `cat data/conversations/
single.json` is the entire debugging story. SQLite is the next step, and only
this package would have to change.

    common          StoreError, now_iso, validate_id, DATA_DIR
    conversations   Message, Summary, Facts, TaskState,
                    Conversation, ConversationStore      data/conversations/
    memory          MemoryItem, Task, TaskStore,         data/tasks/
                    LongTermStore                        data/memory/
    personality     Profile, Personality, PersonalityStore   data/personality/
    invariants      Rule, Invariants, InvariantStore         data/invariants/
    mcp             McpServer, McpStore                      data/mcp/
    project         ProjectStore                             data/project/

Each directory can be pointed elsewhere by an environment variable
(`CHAT_STORE_DIR`, `CHAT_TASKS_DIR`, `CHAT_MEMORY_DIR`, `CHAT_PERSONALITY_DIR`,
`CHAT_INVARIANTS_DIR`, `CHAT_MCP_DIR`, `CHAT_PROJECT_DIR`) or by passing a directory to the store
- the UI tests and the unit tests aim them all at temp directories.
"""

from storage.common import DATA_DIR, StoreError, now_iso, validate_id
from storage.conversations import (
    SINGLE_CONVERSATION_ID,
    Conversation,
    ConversationStore,
    Facts,
    Message,
    Summary,
    TaskState,
)
from storage.invariants import MAX_RULES, InvariantStore, Invariants, Rule
from storage.mcp import MAX_MCP_SERVERS, McpServer, McpStore
from storage.memory import LongTermStore, MemoryItem, Task, TaskStore
from storage.personality import MAX_PROFILES, Personality, PersonalityStore, Profile
from storage.project import ProjectStore

__all__ = [
    "DATA_DIR",
    "MAX_MCP_SERVERS",
    "MAX_PROFILES",
    "MAX_RULES",
    "SINGLE_CONVERSATION_ID",
    "Conversation",
    "ConversationStore",
    "Facts",
    "InvariantStore",
    "Invariants",
    "LongTermStore",
    "McpServer",
    "McpStore",
    "MemoryItem",
    "Message",
    "Personality",
    "PersonalityStore",
    "Profile",
    "ProjectStore",
    "Rule",
    "StoreError",
    "Summary",
    "Task",
    "TaskState",
    "TaskStore",
    "now_iso",
    "validate_id",
]
