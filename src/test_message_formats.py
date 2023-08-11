from declarations import Message, UserID, Author
from message_formats import python_repl_message_format


def test_python_repl_message_format():
    assert (
        python_repl_message_format.render(
            Message(Author(UserID(0, "discord"), "interpreter"), "foo")
        )
        == "foo"
    )
    assert (
        python_repl_message_format.render(
            Message(Author(UserID(0, "discord"), "user"), "foo")
        )
        == ">>> foo"
    )
    assert python_repl_message_format.parse("foo") == [("interpreter", "foo")]
    assert python_repl_message_format.parse(">>> foo") == [("user", "foo")]
    assert python_repl_message_format.parse(
        ">>> bar\n" "foo\n" "bar\n" ">>> baz\n"
    ) == [
        ("user", "bar"),
        ("interpreter", "foo\nbar"),
        ("user", "baz"),
    ]
