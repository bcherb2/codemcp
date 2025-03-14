#!/usr/bin/env python3

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager

from expecttest import TestCase
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPEndToEndTestCase(TestCase, unittest.IsolatedAsyncioTestCase):
    """Base class for end-to-end tests of codemcp using MCP client."""

    async def asyncSetUp(self):
        """Async setup method to prepare the test environment."""
        # Create a temporary directory for testing
        self.temp_dir = tempfile.TemporaryDirectory()
        self.testing_time = "1112911993"  # Fixed timestamp for git

        # Initialize environment variables for git
        self.env = os.environ.copy()
        # Set environment variables for reproducible git behavior
        self.env.setdefault("GIT_TERMINAL_PROMPT", "0")
        self.env.setdefault("EDITOR", ":")
        self.env.setdefault("GIT_MERGE_AUTOEDIT", "no")
        self.env.setdefault("LANG", "C")
        self.env.setdefault("LC_ALL", "C")
        self.env.setdefault("PAGER", "cat")
        self.env.setdefault("TZ", "UTC")
        self.env.setdefault("TERM", "dumb")
        # For deterministic commit times
        self.env.setdefault("GIT_AUTHOR_EMAIL", "author@example.com")
        self.env.setdefault("GIT_AUTHOR_NAME", "A U Thor")
        self.env.setdefault("GIT_COMMITTER_EMAIL", "committer@example.com")
        self.env.setdefault("GIT_COMMITTER_NAME", "C O Mitter")
        self.env.setdefault("GIT_COMMITTER_DATE", f"{self.testing_time} -0700")
        self.env.setdefault("GIT_AUTHOR_DATE", f"{self.testing_time} -0700")

        # Initialize a git repository in the temp directory
        self.init_git_repo()

    async def asyncTearDown(self):
        """Async teardown to clean up after the test."""
        self.temp_dir.cleanup()

    def init_git_repo(self):
        """Initialize a git repository for testing."""
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.temp_dir.name,
            env=self.env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Create initial commit
        readme_path = os.path.join(self.temp_dir.name, "README.md")
        with open(readme_path, "w") as f:
            f.write("# Test Repository\n")

        # Create a codemcp.toml file in the repo root (required for permission checks)
        codemcp_toml_path = os.path.join(self.temp_dir.name, "codemcp.toml")
        with open(codemcp_toml_path, "w") as f:
            f.write("")

        subprocess.run(
            ["git", "add", "README.md", "codemcp.toml"],
            cwd=self.temp_dir.name,
            env=self.env,
            check=True,
        )

        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=self.temp_dir.name,
            env=self.env,
            check=True,
        )

    def normalize_path(self, text):
        """Normalize temporary directory paths in output text."""
        if self.temp_dir and self.temp_dir.name:
            # Handle CallToolResult objects by converting to string first
            if hasattr(text, "content"):
                # This is a CallToolResult object, extract the content
                text = text.content

            # Handle lists of TextContent objects
            if isinstance(text, list) and len(text) > 0 and hasattr(text[0], "text"):
                # For list of TextContent objects, we'll preserve the list structure
                # but normalize the path in each TextContent's text attribute
                return text

            # Replace the actual temp dir path with a fixed placeholder
            if isinstance(text, str):
                return text.replace(self.temp_dir.name, "/tmp/test_dir")
        return text

    def extract_text_from_result(self, result):
        """Extract text content from various result formats for assertions.

        Args:
            result: The result object (could be string, list of TextContent, etc.)

        Returns:
            str: The extracted text content

        """
        if isinstance(result, list) and len(result) > 0 and hasattr(result[0], "text"):
            return result[0].text
        if isinstance(result, str):
            return result
        return str(result)

    async def call_tool_assert_success(self, session, tool_name, tool_params):
        """Call a tool and assert that it succeeds (isError=False).

        This is a helper method for the happy path of tool calls, which:
        1. Calls the specified tool with the given parameters
        2. Asserts that the result is not an error
        3. Returns the extracted text result

        Args:
            session: The client session to use
            tool_name: The name of the tool to call
            tool_params: Dictionary of parameters to pass to the tool

        Returns:
            str: The extracted text content from the result

        Raises:
            AssertionError: If the tool call results in an error
        """
        result = await session.call_tool(tool_name, tool_params)

        # Check that the result is not an error
        self.assertFalse(
            getattr(result, "isError", False),
            f"Tool call to {tool_name} failed with error: {self.extract_text_from_result(result)}",
        )

        # Return the normalized, extracted text result
        normalized_result = self.normalize_path(result)
        return self.extract_text_from_result(normalized_result)

    async def get_chat_id(self, session):
        """Initialize project and get chat_id.

        Args:
            session: The client session to use

        Returns:
            str: The chat_id
        """
        # First initialize project to get chat_id
        init_result = await session.call_tool(
            "codemcp",
            {
                "subtool": "InitProject",
                "path": self.temp_dir.name,
                "user_prompt": "Test initialization for get_chat_id",
                "subject_line": "test: initialize for e2e testing",
                "reuse_head_chat_id": False,
            },
        )
        init_result_text = self.extract_text_from_result(init_result)

        # Extract chat_id from the init result
        import re

        chat_id_match = re.search(r"chat ID: ([^\n]+)", init_result_text)
        chat_id = chat_id_match.group(1)
        assert chat_id is not None

        return chat_id

    @asynccontextmanager
    async def _unwrap_exception_groups(self):
        """Context manager that unwraps ExceptionGroups with single exceptions.
        Only unwraps if there's exactly one exception at each level.
        """
        try:
            yield
        except ExceptionGroup as eg:
            if len(eg.exceptions) == 1:
                exc = eg.exceptions[0]
                # Recursively unwrap if it's another ExceptionGroup with a single exception
                while isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
                    exc = exc.exceptions[0]
                raise exc from None
            else:
                # Multiple exceptions - don't unwrap
                raise

    @asynccontextmanager
    async def create_client_session(self):
        """Create an MCP client session connected to codemcp server."""
        # Set up server parameters for the codemcp MCP server
        server_params = StdioServerParameters(
            command=sys.executable,  # Current Python executable
            args=["-m", "codemcp"],  # Module path to codemcp
            env=self.env,
            cwd=self.temp_dir.name,  # Set the working directory to our test directory
        )

        async with self._unwrap_exception_groups():
            async with stdio_client(server_params) as (read, write):
                async with self._unwrap_exception_groups():
                    async with ClientSession(read, write) as session:
                        # Initialize the connection
                        await session.initialize()
                        yield session
