from controller.integrations.thread_id import derive_thread_id


def test_slack_thread_id():
    tid = derive_thread_id("slack", channel_id="C123", thread_ts="1234567890.123456")
    assert len(tid) == 64
    assert tid == derive_thread_id("slack", channel_id="C123", thread_ts="1234567890.123456")


def test_linear_thread_id():
    tid = derive_thread_id("linear", issue_id="LIN-123")
    assert len(tid) == 64


def test_github_issue_thread_id():
    tid = derive_thread_id("github_issue", repo_owner="org", repo_name="repo", issue_number=42)
    assert len(tid) == 64


def test_github_pr_thread_id():
    tid = derive_thread_id("github_pr", repo_owner="org", repo_name="repo", pr_number=99)
    assert len(tid) == 64


def test_different_sources_different_ids():
    slack_id = derive_thread_id("slack", channel_id="C123", thread_ts="123")
    linear_id = derive_thread_id("linear", issue_id="C123")
    assert slack_id != linear_id
