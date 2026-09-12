"""Content-only language guesses must avoid overconfident labels and unbounded work."""

import pytest
from pygments.lexers import get_lexer_by_name

from marknotes import code_language
from marknotes.code_language import detect_code_language


@pytest.mark.parametrize(
    "expected,source",
    [
        ("python", 'def greet(name):\n    return f"Hello {name}"'),
        ("python", 'print("hello")'),
        ("python", "from pathlib import Path\nprint(Path.cwd())"),
        ("python", "for item in items:\n    print(item)"),
        ("javascript", "const greet = (name) => { console.log(name); };"),
        ("javascript", "function greet(name) { return name; }"),
        ("javascript", 'console.log("hello");'),
        ("typescript", 'interface User { name: string; }\nconst user: User = { name: "A" };'),
        ("typescript", "type UserId = string | number;"),
        ("typescript", "function greet(name: string): string { return name; }"),
        ("json", '{"name": "A", "enabled": true, "items": [1, null]}'),
        ("json", '[{"id":1},{"id":2}]'),
        ("json", "{}"),
        ("html", "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>"),
        ("html", '<div class="card">Hello</div>'),
        (
            "html",
            '<script>\nfunction greet() {\n  const name = "A";\n  console.log(name);\n}\n</script>',
        ),
        ("xml", '<?xml version="1.0"?><config><name>A</name></config>'),
        ("xml", '<configuration><option name="a">1</option></configuration>'),
        ("css", ".card { color: red; padding: 1rem; }"),
        ("css", "@media screen { body { color: black; } }"),
        ("bash", 'for name in *.txt; do\n  echo "$name"\ndone'),
        ("bash", 'export PATH="$HOME/bin:$PATH"'),
        ("powershell", "Get-ChildItem -Path . | Where-Object { $_.Length -gt 0 }"),
        ("powershell", "param([string]$Path)\nWrite-Host $Path"),
        ("sql", "SELECT name, age FROM users WHERE age > 18;"),
        ("sql", "select * from users where active = 1"),
        ("sql", "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);"),
        ("c", '#include <stdio.h>\nint main(void) { printf("Hello"); return 0; }'),
        ("cpp", '#include <stdio.h>\n#include <iostream>\nint main() { std::cout << "Hi"; }'),
        (
            "csharp",
            'using System;\nnamespace Demo { public class App { static void Main() { Console.WriteLine("Hello"); } } }',
        ),
        (
            "java",
            'public class App { public static void main(String[] args) { System.out.println("Hello"); } }',
        ),
        ("rust", 'fn main() {\n    let mut count = 0;\n    println!("{}", count);\n}'),
        ("go", 'package main\nimport "fmt"\nfunc main() { fmt.Println("Hello") }'),
    ],
)
def test_representative_languages_use_real_pygments_aliases(expected, source):
    assert detect_code_language(source) == expected
    assert get_lexer_by_name(expected).aliases[0] == expected


@pytest.mark.parametrize(
    "source",
    [
        "",
        "   \n\t",
        "This is ordinary prose about functions and classes.",
        "日本語の文章です。これはコードではありません。",
        "select a book from the shelf",
        "The class has several methods.\nReturn the value to the caller.",
        "import tax is an important topic",
        "name: Alice\nage: 30",
        "x = 1",
        "return value;",
        "let count = 0;",
        "public class User {}",
        "int main() { return 0; }",
        "123",
        '"Hello"',
        "true",
        "null",
        "[NaN]",
        '{"value": Infinity}',
        "```python\nprint('hello')\n```",
        "~~~javascript\nconsole.log('hi')\n~~~",
        "some\x00binary data",
        "| A | B |\n| --- | --- |\n| 1 | 2 |",
    ],
)
def test_ambiguous_and_prose_inputs_stay_unlabelled(source):
    assert detect_code_language(source) == ""


@pytest.mark.parametrize(
    "shebang,expected",
    [
        ("#!/usr/bin/env python3", "python"),
        ("#!/usr/bin/python3.13 -u", "python"),
        ("#!/usr/bin/env -S node --no-warnings", "javascript"),
        ("#!/usr/bin/env ts-node", "typescript"),
        ("#!/bin/sh", "bash"),
        ("#!/usr/bin/env pwsh", "powershell"),
    ],
)
def test_shebang_is_stronger_than_an_ambiguous_body(shebang, expected):
    assert detect_code_language(shebang + "\nx = 1\n") == expected


def test_bom_and_crlf_do_not_prevent_detection():
    assert detect_code_language('\ufeffdef greet():\r\n    print("hello")\r\n') == "python"


def test_conflicting_families_fall_back_instead_of_guessing_obscure_language():
    text = "def python_name():\n    return 1\nfunction javascriptName() { return 1; }"
    assert detect_code_language(text) == ""


def test_large_input_inspects_a_prefix_and_does_not_parse_truncated_json(monkeypatch):
    limit = code_language.MAX_ANALYSIS_CHARS
    huge = " " * limit + "\ndef hidden_code():\n    return 1"
    assert detect_code_language(huge) == ""
    assert detect_code_language("#!/usr/bin/env python3\n" + "x" * 1_000_000) == "python"
    huge_json = '{"payload": "' + "x" * limit + '"}'

    def forbidden(*args, **kwargs):
        pytest.fail("Oversized input must not go through whole-document JSON parsing")

    monkeypatch.setattr(code_language.json, "loads", forbidden)
    assert detect_code_language(huge_json) == ""


def test_lexer_confirmation_is_bounded_and_required(monkeypatch):
    from pygments.token import Name

    lengths = []

    class ProseLexer:
        aliases = ("python",)

        def get_tokens(self, text):
            lengths.append(len(text))
            yield Name, text

        def analyse_text(self, text):
            lengths.append(len(text))
            return 0.0

    monkeypatch.setattr(code_language, "_lexer", lambda _: ProseLexer())
    source = "def main():\n    return 0\n" + "# comment\n" * 10_000
    assert detect_code_language(source) == ""
    assert lengths and max(lengths) <= code_language.MAX_LEX_CHARS
