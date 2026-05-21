from spillover.adapters.base import Conversation, ConversationTurn


def test_conversation_turn_fields():
    t = ConversationTurn(
        role="user",
        content=[{"type": "text", "text": "hi"}],
        tool_calls=[],
        token_count=3,
    )
    assert t.role == "user"
    assert t.token_count == 3


def test_conversation_total_tokens():
    c = Conversation(
        system="be helpful",
        system_tokens=5,
        turns=[
            ConversationTurn(role="user", content="a", tool_calls=[], token_count=2),
            ConversationTurn(role="assistant", content="b", tool_calls=[], token_count=4),
        ],
    )
    assert c.total_input_tokens == 5 + 2 + 4
