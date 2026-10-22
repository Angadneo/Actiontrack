"""T-M2-10: inline-markup stripping and description cleaning."""

import time
import unicodedata

import pytest

from actiontrack.parsing.items import clean_description, clean_item_lines

CASES = [
    # AC-01.19 description clause: owner, due phrase and period all kept
    ("Priya to send the revised quote by Friday.", "Priya to send the revised quote by Friday."),
    # emphasis
    ("**Priya** to send _revised_ quote", "Priya to send revised quote"),
    ("__Priya__ to send *revised* quote", "Priya to send revised quote"),
    ("~~old~~ new", "old new"),
    ("***bold italic*** text", "bold italic text"),
    ("**bold _nested_ inside**", "bold nested inside"),
    ("**Note:** ship it", "Note: ship it"),
    ("**Priya**'s quote", "Priya's quote"),
    # code
    ("`idempotency` keys", "idempotency keys"),
    ("run ``a ` b`` now", "run a ` b now"),
    ("keep `**not bold**` literal", "keep **not bold** literal"),
    ("`snake_case` and `*x*`", "snake_case and *x*"),
    # links, images, autolinks
    ("see [the quote](https://x.test/q)", "see the quote"),
    ("see [the quote](https://x.test/q \"Title\") now", "see the quote now"),
    ("see [wiki](https://x.test/a_(b))", "see wiki"),
    ("see [the quote][q1] and [two][]", "see the quote and two"),
    ("![a](b.png)", "a"),
    ("[![a](b.png)](https://x.test)", "a"),
    ("[**bold link**](u)", "bold link"),
    ("read <https://x.test/doc> now", "read https://x.test/doc now"),
    ("mail <sam@example.com> now", "mail sam@example.com now"),
    # html
    ("Sam to ship it <!-- maybe --> by Friday", "Sam to ship it by Friday"),
    ("a<!--x-->b", "ab"),
    ("one <b>bold</b> two<br>three", "one bold two three"),
    ("fill in <date> later", "fill in <date> later"),
    # escapes
    (r"price \*not bold\* and \_x\_", "price *not bold* and _x_"),
    (r"\[not a link\](u)", "[not a link](u)"),
    (r"back\\slash", r"back\slash"),
    (r"literal \`tick and `code`", "literal `tick and code"),
    (r"a\`b\`c", "a`b`c"),
    # untouched
    ("snake_case_word", "snake_case_word"),
    ("5 * 3", "5 * 3"),
    ("a * b * c", "a * b * c"),
    ("cut cloud costs by 10%", "cut cloud costs by 10%"),
    ("Priya N. to send revised quote to Acme — by Friday", "Priya N. to send revised quote to Acme — by Friday"),
    ("Lena to pull Acme's usage report", "Lena to pull Acme's usage report"),
    ("@anita: review it", "@anita: review it"),
    ("x [unclosed bracket", "x [unclosed bracket"),
    ("[bare ref] stays", "[bare ref] stays"),
    ("unclosed **bold", "unclosed **bold"),
    ("unclosed <!-- comment", "unclosed <!-- comment"),
    ("tilde ~ single ~", "tilde ~ single ~"),
    ("a _ b _ c", "a _ b _ c"),
    # unicode
    ("李雷 to ship 🚀 now", "李雷 to ship 🚀 now"),
    ("Zoë to send", "Zoë to send"),
    # whitespace
    ("  a \t b   c  ", "a b c"),
    ("a b", "a b"),
    ("", ""),
]


@pytest.mark.parametrize("raw,expected", CASES, ids=[f"row{i}" for i in range(len(CASES))])
def test_clean_description(raw, expected):
    assert clean_description(raw) == expected


@pytest.mark.ac("AC-01.19")
def test_description_keeps_owner_due_and_period():
    got = clean_item_lines(["Priya to send the revised quote by Friday."])
    assert got == "Priya to send the revised quote by Friday."


@pytest.mark.ac("AC-01.6")
def test_continuation_lines_joined_with_one_space():
    got = clean_item_lines(["Priya to send", "  the revised quote  ", "\tby Friday."])
    assert got == "Priya to send the revised quote by Friday."


def test_markup_spanning_lines_is_cleaned_after_join():
    assert clean_item_lines(["**Priya to", "send** it"]) == "Priya to send it"


def test_result_is_nfc_and_idempotent():
    out = clean_description("Zoë **x**")
    assert out == unicodedata.normalize("NFC", out) == "Zoë x"
    assert clean_description(out) == out


def test_decomposed_input_not_required_to_be_preserved():
    assert clean_description("Zoë") == "Zoë"


def test_sentinel_characters_in_input_do_not_break():
    assert clean_description("a 0 `b`") == "a 0 b"


@pytest.mark.parametrize(
    "line",
    [
        "a" * 1_000_000,
        "*_" * 500_000,
        "[" * 100_000,
        "[a](" * 50_000,
        "[a][" * 50_000,
        "<!--" * 50_000,
        "`" * 1_000_000,
        "`a" * 100_000,
        "\\" * 1_000_000,
        "<a " * 100_000,
        "**a " * 100_000,
        "[a]( " * 50_000,
    ],
    ids=[f"pathological-{i}" for i in range(12)],
)
def test_linear_time(line):
    t = time.perf_counter()
    clean_description(line)
    # Generous wall-clock bound (observed ~0.1-3.4 s depending on machine load, e.g. several test runs in
    # parallel): quadratic behaviour on these 1M-character inputs would take minutes, so 20 s still
    # detects it without being flaky on a loaded machine.
    assert time.perf_counter() - t < 20.0
