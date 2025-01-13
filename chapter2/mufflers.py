import re
from re import sub, match, compile as re_compile, I, M
from typing import Callable, Union, Literal

Muffler = Callable[[str, str], bool]


def context_sentence_repetition(context: str, reply: str):
    """
    Determine if result repeats any of the prompt sentences
    """
    prompt_sentences = divide_sentences(context) or []
    completion_sentences = divide_sentences(reply) or []
    return any(sentence in prompt_sentences for sentence in completion_sentences)


def has_url(context: str, reply: str):
    return match("https?://", reply) and not match(
        r"https://(cdn\.)?discord(app)?.com/", reply
    )


def has_pump_fun_ca(context: str, reply: str):
    return match(r"[a-zA-Z0-9]{40}pump", reply)


def has_img_url_token(context: str, reply: str):
    return match(r"<begin_of_img_url>", reply)


mufflers: dict[str, Muffler] = {
    "context_sentence_repetition": context_sentence_repetition,
    "has_url": has_url,
    "has_pump_fun_ca": has_pump_fun_ca,
    "has_img_url_token": has_img_url_token,
}


def divide_sentences(prompt):
    """
    Args:
        prompt (str): a prompt string to extract sentences from

    Returns:
        list: a list of sentence strings
    """
    prompt_detector = re_compile(r"^<[^>\n]+>\s+", I | M)
    delimiter_detector = re_compile(r"^#+", I | M)
    sign_detector = re_compile(r"^[\.\!\?\s]+", M)
    # Rejoin a list of prompt items to a string
    tmp_prompt = "\n".join(prompt) if isinstance(prompt, list) else prompt
    # Strip prompt
    tmp_prompt = tmp_prompt.strip()
    # Remove nicknames at the start of each line by using a negative group
    tmp_prompt = sub(prompt_detector, "", tmp_prompt)
    # Detect and remove the prompt delimiters
    tmp_prompt = sub(delimiter_detector, "", tmp_prompt)
    # Detect and remove sign-only noise
    tmp_prompt = sub(sign_detector, "", tmp_prompt)
    # Clear empty lines
    tmp_prompt = "\n".join([line for line in tmp_prompt.splitlines() if line])
    # Split to the sentences
    sentences = []
    try:
        sentences = sentence_tokenize(tmp_prompt)
    except TypeError as nltk_type_exception:
        print("NLTK exception: incorrect input supplied.")
        print(nltk_type_exception)
        print(f"Input type: {type(tmp_prompt)}")
        print(f'Input: "{tmp_prompt}"')
    return sentences


def sentence_tokenize(s):
    import nltk

    try:
        nltk_sentences = nltk.sent_tokenize(s)
    except LookupError:
        nltk.download("punkt")
        return sentence_tokenize(s)
    sentences = []
    for sentence in nltk_sentences:
        if re.match(r"^[\.\!\?\s]+$", sentence):
            sentences[-1] += sentence
        else:
            sentences.extend(sentence.splitlines())
    return sentences
