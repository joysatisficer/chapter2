from declarations import Message, Author
from message_formats import PythonREPLMessageFormat, InfrastructMessageFormat


def test_python_repl_message_format():
    assert (
        PythonREPLMessageFormat.render(Message(Author("interpreter"), "foo")) == "foo\n"
    )
    assert PythonREPLMessageFormat.render(Message(Author("user"), "foo")) == ">>> foo\n"
    assert PythonREPLMessageFormat.parse("foo") == [
        Message(Author("interpreter"), "foo")
    ]
    assert PythonREPLMessageFormat.parse(">>> foo") == [Message(Author("user"), "foo")]
    assert PythonREPLMessageFormat.parse(">>> bar\n" "foo\n" "bar\n" ">>> baz\n") == [
        Message(Author("user"), "bar"),
        Message(Author("interpreter"), "foo\nbar"),
        Message(Author("user"), "baz"),
    ]


def test_infrastruct_message_format_parse_empty():
    assert InfrastructMessageFormat.parse("") == []


# todo: investigate testing with hypothesis library
