
def sanitize_untrusted(content: str) -> str:
    escaped = content.replace("</UNTRUSTED_CONTENT>", "&lt;/UNTRUSTED_CONTENT&gt;")
    return f"<UNTRUSTED_CONTENT>\n{escaped}\n</UNTRUSTED_CONTENT>"
