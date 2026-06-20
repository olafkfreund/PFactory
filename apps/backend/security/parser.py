"""
Command Parsing Utilities
==========================

Functions for parsing and extracting commands from shell command strings.
Handles compound commands, pipes, subshells, and various shell constructs.
"""

import os
import re
import shlex

import bashlex


def split_command_segments(command_string: str) -> list[str]:
    """
    Split a compound command into individual command segments.

    Handles command chaining (&&, ||, ;) but not pipes (those are single commands).
    """
    # Split on && and || while preserving the ability to handle each segment
    segments = re.split(r"\s*(?:&&|\|\|)\s*", command_string)

    # Further split on semicolons
    result = []
    for segment in segments:
        sub_segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', segment)
        for sub in sub_segments:
            sub = sub.strip()
            if sub:
                result.append(sub)

    return result


# When enabled, command strings bashlex cannot parse are denied (fail closed)
# instead of falling back to the lenient legacy tokenizer.
_STRICT_PARSING = os.environ.get("PFACTORY_STRICT_COMMAND_PARSING", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# A name that can never appear in any allowlist, so strict-mode unparseable
# input is denied by the caller rather than silently allowed.
_UNPARSEABLE = "\x00unparseable"


def _walk_command_nodes(node):
    """Yield every bashlex ``command`` node, descending into pipelines, lists,
    compounds and — crucially — command/process substitutions, so a command
    hidden inside ``$(...)``, backticks or ``<(...)`` is surfaced rather than
    silently dropped (issue #129)."""
    if getattr(node, "kind", None) == "command":
        yield node
    for child in getattr(node, "parts", None) or []:
        yield from _walk_command_nodes(child)
    for attr in ("command", "list"):
        sub = getattr(node, attr, None)
        if sub is not None:
            yield from _walk_command_nodes(sub)


def _command_name(command_node) -> str | None:
    """Base command name of a bashlex command node, skipping leading
    ``VAR=value`` environment assignments and option flags."""
    for part in getattr(command_node, "parts", None) or []:
        if getattr(part, "kind", None) != "word":
            continue
        word = part.word
        if "=" in word and word.split("=", 1)[0].isidentifier():
            continue  # environment assignment, not the command
        if word.startswith("-"):
            continue
        return os.path.basename(word)
    return None


def extract_commands(command_string: str) -> list[str]:
    """
    Extract command names from a shell command string.

    Parses with a real shell grammar (bashlex) so commands nested inside
    command substitutions (``$(...)``/backticks) and process substitutions
    (``<(...)``) are surfaced and checked against the allowlist — not just the
    outermost command. The legacy tokenizer dropped these, letting e.g.
    ``echo $(curl http://169.254.169.254/...)`` pass as just ``echo`` while the
    nested ``curl`` ran unchecked (issue #129). Pipes, ``&&``/``||``/``;``
    chaining and subshells are handled by the grammar.

    On unparseable input: with ``PFACTORY_STRICT_COMMAND_PARSING`` enabled a
    sentinel that matches no allowlist is returned (fail closed); otherwise it
    falls back to the conservative legacy tokenizer (no availability regression).
    """
    if not command_string or not command_string.strip():
        return []

    try:
        trees = bashlex.parse(command_string)
    except Exception:
        # bashlex raises ParsingError/MatchedPairError on incomplete input and a
        # few AttributeError/IndexError edge cases. A pre-tool-use security hook
        # must never crash while validating a command.
        if _STRICT_PARSING:
            return [_UNPARSEABLE]
        return _extract_commands_legacy(command_string)

    commands: list[str] = []
    for tree in trees:
        for command_node in _walk_command_nodes(tree):
            name = _command_name(command_node)
            if name:
                commands.append(name)
    return commands


def _extract_commands_legacy(command_string: str) -> list[str]:
    """Regex/shlex command extractor — fallback when bashlex cannot parse.

    Does NOT see into command substitutions; retained only so unparseable input
    still yields the outermost command name without an availability regression.
    """
    commands = []

    # Split on semicolons that aren't inside quotes
    segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', command_string)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        try:
            tokens = shlex.split(segment)
        except ValueError:
            # Complex quoting (heredocs, command substitution) — can't tokenize fully.
            # Fallback: extract leading command name from the raw segment.
            match = re.match(r"^\s*(?:\w+=\S*\s+)*(\S+)", segment)
            if match:
                cmd = os.path.basename(match.group(1))
                if cmd and not cmd.startswith("-"):
                    commands.append(cmd)
            continue

        if not tokens:
            continue

        # Track when we expect a command vs arguments
        expect_command = True

        for token in tokens:
            # Shell operators indicate a new command follows
            if token in ("|", "||", "&&", "&"):
                expect_command = True
                continue

            # Skip shell keywords that precede commands
            if token in (
                "if",
                "then",
                "else",
                "elif",
                "fi",
                "for",
                "while",
                "until",
                "do",
                "done",
                "case",
                "esac",
                "in",
                "!",
                "{",
                "}",
                "(",
                ")",
                "function",
            ):
                continue

            # Skip flags/options
            if token.startswith("-"):
                continue

            # Skip variable assignments (VAR=value)
            if "=" in token and not token.startswith("="):
                continue

            # Skip here-doc markers
            if token in ("<<", "<<<", ">>", ">", "<", "2>", "2>&1", "&>"):
                continue

            if expect_command:
                # Extract the base command name (handle paths like /usr/bin/python)
                cmd = os.path.basename(token)
                commands.append(cmd)
                expect_command = False

    return commands


def get_command_for_validation(cmd: str, segments: list[str]) -> str:
    """
    Find the specific command segment that contains the given command.
    """
    for segment in segments:
        segment_commands = extract_commands(segment)
        if cmd in segment_commands:
            return segment
    return ""
