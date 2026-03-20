import hashlib


def derive_thread_id(source: str, **kwargs) -> str:
    if source == "slack":
        raw = f"slack:{kwargs['channel_id']}:{kwargs['thread_ts']}"
    elif source == "linear":
        raw = f"linear:{kwargs['issue_id']}"
    elif source == "github_issue":
        raw = f"github:issue:{kwargs['repo_owner']}/{kwargs['repo_name']}:{kwargs['issue_number']}"
    elif source == "github_pr":
        raw = f"github:pr:{kwargs['repo_owner']}/{kwargs['repo_name']}:{kwargs['pr_number']}"
    else:
        msg = f"Unknown source: {source}"
        raise ValueError(msg)
    return hashlib.sha256(raw.encode()).hexdigest()
