from controller.models import TaskRequest, AgentResult, Thread, ThreadStatus


def test_task_request_defaults():
    tr = TaskRequest(
        thread_id="abc", source="slack", source_ref={},
        repo_owner="org", repo_name="repo", task="fix bug"
    )
    assert tr.conversation == []
    assert tr.images == []


def test_agent_result():
    ar = AgentResult(branch="df/abc/123", exit_code=0, commit_count=3)
    assert ar.pr_url is None


def test_thread_default_status():
    t = Thread(id="abc", source="slack", source_ref={}, repo_owner="org", repo_name="repo")
    assert t.status == ThreadStatus.IDLE
