from spillover.budget.profile import (
    CODING,
    CONVERSATION,
    DEFAULT,
    RESEARCH,
    select_profile,
)


def test_override_explicit():
    assert select_profile({}, "coding") == CODING
    assert select_profile({}, "research") == RESEARCH
    assert select_profile({}, "conversation") == CONVERSATION


def test_auto_detects_coding_by_tools_field():
    p = select_profile({"tools": [{"name": "Read"}]})
    assert p.name == "coding"


def test_auto_detects_coding_by_tool_use_in_content():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Edit"},
                    {"type": "tool_use", "name": "Bash"},
                ],
            }
        ]
    }
    assert select_profile(payload).name == "coding"


def test_auto_detects_conversation_by_message_count():
    payload = {"messages": [{"role": "user", "content": "hi"}] * 11}
    assert select_profile(payload).name == "conversation"


def test_default_falls_back():
    p = select_profile({"messages": [{"role": "user", "content": "hi"}]})
    assert p == DEFAULT
