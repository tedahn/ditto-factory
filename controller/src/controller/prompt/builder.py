from controller.integrations.sanitize import sanitize_untrusted

def build_system_prompt(
    repo_owner: str,
    repo_name: str,
    task: str,
    claude_md: str = "",
    conversation: list[str] | None = None,
    is_retry: bool = False,
) -> str:
    sections = []

    sections.append(f"# Working Environment\nYou are working in the repository {repo_owner}/{repo_name}.")
    sections.append("The repository has been cloned to /workspace. You are on a feature branch.")

    if claude_md:
        sections.append(f"# Repository Rules\n{claude_md}")

    sections.append(
        "# Task Execution Rules\n"
        "- You must make concrete changes to the codebase.\n"
        "- Do not exit without committing at least one change or explicitly explaining why no changes are needed.\n"
        "- Run any available linters, formatters, or tests before committing.\n"
        "- Create small, focused commits with clear messages.\n"
        "- Push your branch when done."
    )

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
