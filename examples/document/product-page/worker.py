"""Mechanical document observations; meaning is assessed separately. Guest only."""

import json
import re
import sys
from pathlib import Path


def words(text: str) -> int:
    return len(re.findall(r"\b\w+(?:[\u2019'-]\w+)*\b", text))


def observe(root: Path) -> dict[str, bool]:
    page = root / "page.md"
    result = dict.fromkeys(("structure", "headline", "subheadline", "introduction", "cta"), False)
    if not page.is_file() or page.stat().st_size > 65536:
        return result
    text = page.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", text.strip())
    if len(blocks) < 5:
        return result
    headline, subheadline, cta, heading = blocks[:4]
    introduction = "\n\n".join(blocks[4:])
    result["structure"] = (
        headline.startswith("# ")
        and "\n" not in headline
        and heading.startswith("## ")
        and "\n" not in heading
        and "```" not in text
        and not re.search(r"(?m)^#", subheadline + introduction)
    )
    result["headline"] = 1 <= words(headline.removeprefix("# ")) <= 12
    result["subheadline"] = 25 <= words(subheadline) <= 45
    result["introduction"] = 100 <= words(introduction) <= 160
    result["cta"] = (
        re.fullmatch(
            r"\[[^\]\n]+\]\(https://github\.com/davisbuilds/agentmonitor#quick-start\)", cta
        )
        is not None
    )
    return result


def main() -> None:
    requests = json.load(sys.stdin)["requests"]
    values = observe(Path(sys.argv[1]))
    print(json.dumps({"observations": [values[request["check"]] for request in requests]}))


if __name__ == "__main__":
    main()
