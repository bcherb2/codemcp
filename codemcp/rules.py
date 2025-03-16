#!/usr/bin/env python3

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

__all__ = [
    "Rule",
    "find_applicable_rules",
    "load_rule_from_file",
    "match_file_with_glob",
    "get_applicable_rules_content",
]


@dataclass
class Rule:
    """Represents a cursor rule loaded from an MDC file."""

    description: Optional[str]  # Description of when the rule is useful
    globs: List[str]  # List of glob patterns to match files
    always_apply: bool  # Whether the rule should always be applied
    payload: str  # The markdown content of the rule
    file_path: str  # Path to the MDC file


def load_rule_from_file(file_path: str) -> Optional[Rule]:
    """Load a rule from an MDC file.

    Args:
        file_path: Path to the MDC file

    Returns:
        A Rule object if the file is valid, None otherwise
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse the frontmatter and content
        frontmatter_match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
        if not frontmatter_match:
            return None

        frontmatter_text = frontmatter_match.group(1)
        payload = frontmatter_match.group(2).strip()

        # We need to manually parse the frontmatter to handle unquoted glob patterns
        frontmatter = {}
        for line in frontmatter_text.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()
                frontmatter[key] = value

        # Extract rule properties
        description = frontmatter.get("description")

        # Handle globs - can be comma-separated string or a list
        globs: List[str] = []
        globs_value = frontmatter.get("globs")
        if globs_value:
            globs = [g.strip() for g in globs_value.split(",")]

        # Convert alwaysApply string to boolean
        always_apply_value = frontmatter.get("alwaysApply", "false")
        always_apply = always_apply_value.lower() == "true"

        return Rule(
            description=description,
            globs=globs,
            always_apply=always_apply,
            payload=payload,
            file_path=file_path,
        )
    except Exception as e:
        # If there's any error parsing the file, return None
        print(f"Error loading rule from {file_path}: {e}")
        return None


def match_file_with_glob(file_path: str, glob_pattern: str) -> bool:
    """Check if a file path matches a glob pattern.

    Args:
        file_path: Path to check
        glob_pattern: Glob pattern to match against

    Returns:
        True if the file matches the pattern, False otherwise
    """
    # Convert to Path object for consistent handling
    path = Path(file_path)
    file_name = path.name

    # Simple case: direct file extension matching (*.js)
    if glob_pattern.startswith("*."):
        return file_name.endswith(glob_pattern[1:])

    # Handle "**/*.js" pattern (match any file with .js extension in any directory)
    if glob_pattern == "**/*.js" or glob_pattern == "**/*.jsx":
        ext = glob_pattern.split(".")[-1]
        return file_name.endswith("." + ext)

    # Handle patterns like "src/**/*.jsx" (match files in src directory or subdirectories)
    if "/" in glob_pattern and "**" in glob_pattern:
        dir_part, file_part = glob_pattern.split("/**/")

        # Check if the file has the right extension
        if file_part.startswith("*"):
            ext = file_part[1:]  # *.jsx -> .jsx
            if not file_name.endswith(ext):
                return False

        # Check if it's in the right directory
        return dir_part in str(path)

    # Default to fnmatch for other patterns
    return fnmatch.fnmatch(file_name, glob_pattern)


def find_applicable_rules(
    repo_root: str, file_path: Optional[str] = None
) -> Tuple[List[Rule], List[Tuple[str, str]]]:
    """Find all applicable rules for the given file path.

    Walks up the directory tree from the file path to the repo root,
    looking for .cursor/rules directories and loading MDC files.

    Args:
        repo_root: Root of the repository
        file_path: Optional path to a file to match against rules

    Returns:
        A tuple containing (applicable_rules, suggested_rules)
        - applicable_rules: List of Rule objects that match the file
        - suggested_rules: List of (description, file_path) tuples for rules with descriptions
    """
    applicable_rules: List[Rule] = []
    suggested_rules: List[Tuple[str, str]] = []
    processed_rule_files: Set[str] = set()

    # Normalize paths
    repo_root = os.path.abspath(repo_root)

    # If file_path is provided, walk up from its directory to repo_root
    # Otherwise, just check repo_root
    start_dir = os.path.dirname(os.path.abspath(file_path)) if file_path else repo_root
    current_dir = start_dir

    # Ensure we don't go beyond repo_root
    while current_dir.startswith(repo_root):
        # Look for .cursor/rules directory
        rules_dir = os.path.join(current_dir, ".cursor", "rules")
        if os.path.isdir(rules_dir):
            # Find all MDC files in this directory
            for root, _, files in os.walk(rules_dir):
                for filename in files:
                    if filename.endswith(".mdc"):
                        rule_file_path = os.path.join(root, filename)

                        # Skip if we've already processed this file
                        if rule_file_path in processed_rule_files:
                            continue
                        processed_rule_files.add(rule_file_path)

                        # Load the rule
                        rule = load_rule_from_file(rule_file_path)
                        if rule is None:
                            continue

                        # Check if this rule applies
                        if rule.always_apply:
                            applicable_rules.append(rule)
                        elif file_path and rule.globs:
                            # Check if any glob pattern matches the file
                            for glob_pattern in rule.globs:
                                if match_file_with_glob(file_path, glob_pattern):
                                    applicable_rules.append(rule)
                                    break
                        elif rule.description:
                            # Add to suggested rules if it has a description
                            suggested_rules.append((rule.description, rule_file_path))

        # Move up one directory
        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir:  # We've reached the root
            break
        current_dir = parent_dir

    return applicable_rules, suggested_rules


def get_applicable_rules_content(
    repo_root: str, file_path: Optional[str] = None
) -> str:
    """Generate a string with all applicable rules for a file or the current directory.

    This is a helper function used by multiple tools to format rule content
    in a consistent way.

    Args:
        repo_root: Root of the repository
        file_path: Optional path to a file to match against rules

    Returns:
        A formatted string containing all applicable rules, or an empty string if no rules apply
    """
    try:
        result = ""

        # Find applicable rules
        applicable_rules, suggested_rules = find_applicable_rules(repo_root, file_path)

        # If we have applicable rules, add them to the output
        if applicable_rules or suggested_rules:
            result += "\n\n// .cursor/rules results:"

            # Add directly applicable rules
            for rule in applicable_rules:
                rule_content = f"\n\n// Rule from {os.path.relpath(rule.file_path, repo_root)}:\n{rule.payload}"
                result += rule_content

            # Add suggestions for rules with descriptions
            for description, rule_path in suggested_rules:
                rel_path = os.path.relpath(rule_path, repo_root)
                result += f"\n\n// If {description} applies, load {rel_path}"

        return result
    except Exception:
        # Don't propagate exceptions from rule processing
        return ""
