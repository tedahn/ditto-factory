from controller.integrations.sanitize import sanitize_untrusted
from controller.models import TaskType


_CODE_CHANGE_RULES = (
    "# Task Execution Rules\n"
    "- You must make concrete changes to the codebase.\n"
    "- Do not exit without committing at least one change or explicitly explaining why no changes are needed.\n"
    "- Run any available linters, formatters, or tests before committing.\n"
    "- Create small, focused commits with clear messages.\n"
    "- Push your branch when done."
)

_ANALYSIS_RULES = (
    "# Task Execution Rules\n"
    "- You are performing an analysis task. Your result is a structured report, not code changes.\n"
    "- Investigate the codebase, data, or system as described in the task.\n"
    "- Produce your findings as a clear, structured result with actionable insights.\n"
    "- Include relevant data, metrics, or evidence to support your findings.\n"
    "- If you produce file artifacts, describe them clearly."
)

_TASK_TYPE_RULES = {
    TaskType.CODE_CHANGE: _CODE_CHANGE_RULES,
    TaskType.ANALYSIS: _ANALYSIS_RULES,
}


def build_system_prompt(
    repo_owner: str,
    repo_name: str,
    task: str,
    claude_md: str = "",
    conversation: list[str] | None = None,
    is_retry: bool = False,
    task_type: TaskType = TaskType.CODE_CHANGE,
) -> str:
    sections = []

    sections.append(f"# Working Environment\nYou are working in the repository {repo_owner}/{repo_name}.")
    sections.append("The repository has been cloned to /workspace. You are on a feature branch.")

    if claude_md:
        sections.append(f"# Repository Rules\n{claude_md}")

    rules = _TASK_TYPE_RULES.get(task_type, _CODE_CHANGE_RULES)
    sections.append(rules)

    if conversation:
        sections.append("# Conversation History\n" + "\n".join(conversation))

    task_content = sanitize_untrusted(task)
    if is_retry:
        sections.append(
            "# Task (RETRY)\n"
            "Your previous attempt produced no changes. Review the task again and make the required changes.\n\n"
            + task_content
        )
    else:
        sections.append(f"# Task\n{task_content}")

    return "\n\n".join(sections)
