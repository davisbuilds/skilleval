"""Author solver-visible synthetic sessions, separately from hidden check inputs.

This helper runs only standard-library Python and an existing zstd executable;
it imports no AgentMonitor source. Copy only its output into the task snapshot.
"""

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def transcript(session_id, cwd, hour, completed):
    """A small ordinary CLI conversation, with a completed final turn when asked."""

    def at(second):
        return f"2026-10-07T{hour:02d}:00:{second:02d}Z"

    def message(role, text, second):
        return {
            "type": "response_item",
            "timestamp": at(second),
            "payload": {
                "type": "message",
                "role": role,
                "content": [
                    {"type": "input_text" if role == "user" else "output_text", "text": text}
                ],
            },
        }

    def usage(second, response, own, total):
        fields = ("input_tokens", "cached_input_tokens", "output_tokens")
        current = dict(zip(fields, own, strict=True))
        thread = dict(zip(fields, total, strict=True))
        return [
            {
                "type": "token_usage_record",
                "timestamp": at(second),
                "payload": {
                    "thread_id": session_id,
                    "response_id": response,
                    "usage": current,
                    "thread_token_usage": thread,
                },
            },
            {
                "type": "event_msg",
                "timestamp": at(second),
                "payload": {"type": "token_count", "info": {"total_token_usage": thread}},
            },
        ]

    lines = [
        {
            "type": "session_meta",
            "timestamp": at(0),
            "payload": {
                "id": session_id,
                "timestamp": at(0),
                "cwd": cwd,
                "source": "cli",
                "originator": "codex_cli_rs",
            },
        },
        {
            "type": "turn_context",
            "timestamp": at(1),
            "payload": {"model": "gpt-5.6-terra", "cwd": cwd},
        },
        message("user", "Inspect the invoice export for duplicate line items.", 2),
        message("assistant", "The draft export has two repeated line items.", 8),
        *usage(9, f"demo-{hour}-a", (800, 200, 40), (800, 200, 40)),
    ]
    if completed:
        lines.extend(
            [
                message("user", "Remove the duplicates and report the retained total.", 15),
                message("assistant", "The export retains twelve unique line items.", 24),
                *usage(25, f"demo-{hour}-b", (350, 100, 30), (1150, 300, 70)),
                {"type": "event_msg", "timestamp": at(26), "payload": {"type": "task_complete"}},
            ]
        )
    return lines


def prepare(destination, zstd):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    sessions = destination / "codex-home/sessions/2026/10/07"
    sessions.mkdir(parents=True)
    for hour, completed, project in (
        (9, True, "invoice-tools"),
        (10, True, "invoice-tools"),
        (11, False, "release-notes"),
    ):
        session_id = f"019f2000-abcd-7000-8000-{hour:012d}"
        rows = transcript(session_id, f"/workspaces/{project}", hour, completed)
        content = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()
        name = f"rollout-2026-10-07T{hour:02d}-00-00-{session_id}.jsonl"
        target = sessions / name
        if completed:
            result = subprocess.run(
                [zstd, "-q", "-c"], input=content, capture_output=True, check=True
            )
            target.with_suffix(".jsonl.zst").write_bytes(result.stdout)
        else:
            target.write_bytes(content)
    (destination / "README.md").write_text(
        "# Synthetic session fixture\n\n"
        "`codex-home/` is an isolated Codex home with ordinary active and completed\n"
        "session artifacts. All conversation text, identities and usage are authored\n"
        "examples. Point application reads at this directory and use a temporary\n"
        "application database. No real session store is needed.\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--zstd", default=shutil.which("zstd"))
    args = parser.parse_args()
    if args.zstd is None:
        parser.error("provide an existing zstd executable with --zstd")
    prepare(args.destination, args.zstd)


if __name__ == "__main__":
    main()
