from declarations import Message, Author
from message_formats import python_repl_message_format


def test_python_repl_message_format():
    assert (
        python_repl_message_format.render(Message(Author("interpreter"), "foo"))
        == "foo"
    )
    assert (
        python_repl_message_format.render(Message(Author("user"), "foo")) == ">>> foo"
    )
    assert python_repl_message_format.parse("foo") == [
        Message(Author("interpreter"), "foo")
    ]
    assert python_repl_message_format.parse(">>> foo") == [
        Message(Author("user"), "foo")
    ]
    assert python_repl_message_format.parse(
        ">>> bar\n" "foo\n" "bar\n" ">>> baz\n"
    ) == [
        ("user", "bar"),
        ("interpreter", "foo\nbar"),
        ("user", "baz"),
    ]
