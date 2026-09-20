"""greet — a very small CLI, and the product repo for the M8 scenario.

Starting state: English only, no --upper, no --lang. `eng-001` adds --upper and
`eng-002` adds --lang on top of it. Standard library only, so the `### Checks`
blocks of both orders run anywhere python3 does.
"""

import argparse
import sys

GREETING = "Hello, {name}!"


def greet(name: str) -> str:
    return GREETING.format(name=name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="greet", description="Greet someone.")
    parser.add_argument("name")
    args = parser.parse_args(argv)
    print(greet(args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
