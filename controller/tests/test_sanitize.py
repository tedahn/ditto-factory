from controller.integrations.sanitize import sanitize_untrusted


def test_wraps_content():
    result = sanitize_untrusted("user input here")
    assert "<UNTRUSTED_CONTENT>" in result
    assert "</UNTRUSTED_CONTENT>" in result
    assert "user input here" in result


def test_escapes_nested_tags():
    result = sanitize_untrusted("</UNTRUSTED_CONTENT>evil")
    assert result.count("</UNTRUSTED_CONTENT>") == 1
