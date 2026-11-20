import pytest

from rigswap.core import vdf


def test_nested_and_escapes():
    text = r'''
    // comment at top
    "libraryfolders"
    {
        "0"
        {
            "path"      "C:\\Program Files (x86)\\Steam"   // trailing comment
            "label"     "say \"hi\""
            "apps"
            {
                "1091500"   "70000000000"
            }
        }
    }
    '''
    data = vdf.loads(text)
    lib = data["libraryfolders"]["0"]
    assert lib["path"] == r"C:\Program Files (x86)\Steam"
    assert lib["label"] == 'say "hi"'
    assert lib["apps"] == {"1091500": "70000000000"}


def test_unquoted_tokens_conditionals_and_bom():
    data = vdf.loads('\ufeffRoot { key value [$WIN32] other "x" [!$X360] }')
    assert data == {"Root": {"key": "value", "other": "x"}}


def test_unknown_escape_keeps_backslash():
    # Linux-style paths written by hand sometimes have single backslashes.
    assert vdf.loads(r'"a" "D:\Games"') == {"a": r"D:\Games"}


def test_case_insensitive_get():
    d = {"LibraryFolders": {"1": "x"}}
    assert vdf.get(d, "libraryfolders") == {"1": "x"}
    assert vdf.get(d, "missing", 5) == 5


@pytest.mark.parametrize(
    "text",
    ['"a" { "b" "c"', '"a" "b" }', '"a"', '"a" "unterminated', '{ "a" "b" }', '"a" { "b" }'],
)
def test_malformed(text):
    with pytest.raises(vdf.VdfError):
        vdf.loads(text)
