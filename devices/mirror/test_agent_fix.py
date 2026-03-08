#!/usr/bin/env python3
"""Tests for the mirror agent done-tool parameter name fix.

The bug: line 318 of agent.py previously read args.get('summary', ...)
instead of args.get('detail', ...).

    Before (WRONG): return f"DONE: {args.get('summary', 'completed')}"
    After  (FIXED): return f"DONE: {args.get('detail', 'completed')}"

The tool schema defines the parameter as "detail" (line 195), so the
handler must read "detail" to match.

Since agent.py imports MirrorCamera, MirrorDisplay, MirrorImageGenerator,
MirrorInstructionPlanner, and PIL at module level, we mock those before
importing. For the "done" tool path, none of the hardware dependencies
are actually used.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock


def _import_agent():
    """Import agent.py with all hardware dependencies mocked out.

    Returns the agent module. We mock PIL, camera, display,
    image_generation, and planner since they may not be available
    in the test environment and are not needed for the done-tool test.
    """
    # Mock PIL.ImageOps (imported at module level)
    pil_mod = types.ModuleType("PIL")
    pil_mod.ImageOps = MagicMock()
    pil_mod.Image = MagicMock()
    sys.modules.setdefault("PIL", pil_mod)
    sys.modules.setdefault("PIL.ImageOps", pil_mod.ImageOps)
    sys.modules.setdefault("PIL.Image", pil_mod.Image)

    # Mock the sibling modules that agent.py tries to import
    # (camera, display, image_generation, planner)
    for mod_name in ("camera", "display", "image_generation", "planner"):
        full_name = f"devices.mirror.{mod_name}"
        if full_name not in sys.modules:
            sys.modules[full_name] = MagicMock()
        # Also mock the bare names for the fallback import path
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()

    # Mock openai (lazy-imported but let's be safe)
    sys.modules.setdefault("openai", MagicMock())

    # Now import the agent module
    import importlib
    # Ensure we get a fresh import
    mod_name = "devices.mirror.agent"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Try package import first, fall back to direct path import
    try:
        import devices.mirror.agent as agent_mod
        return agent_mod
    except (ImportError, ModuleNotFoundError):
        # Direct path import
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "mirror_agent",
            Path(__file__).resolve().parent / "agent.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# Import the agent module with mocks
_agent = _import_agent()
_execute_tool_call = _agent._execute_tool_call


class TestDoneToolParameterFix(unittest.TestCase):
    """Validate that the done tool reads 'detail' (not 'summary') from args."""

    def _call_done(self, args: dict) -> str:
        """Helper: call _execute_tool_call for the 'done' tool.

        The done branch only reads args and returns a string -- it never
        touches camera, planner, generator, display, or skip_camera,
        so we pass None for all of them.
        """
        return _execute_tool_call(
            name="done",
            args=args,
            camera=None,
            planner=None,
            generator=None,
            display=None,
            skip_camera=False,
        )

    def test_detail_parameter_is_used(self):
        """When args contains 'detail', the return value should include it."""
        result = self._call_done({"detail": "task finished"})
        self.assertEqual(result, "DONE: task finished")

    def test_detail_with_different_text(self):
        """Verify it works with various detail strings."""
        result = self._call_done({"detail": "displayed sunset image on mirror"})
        self.assertEqual(result, "DONE: displayed sunset image on mirror")

    def test_fallback_when_no_detail(self):
        """When args is empty, should fall back to 'completed'."""
        result = self._call_done({})
        self.assertEqual(result, "DONE: completed")

    def test_summary_key_is_ignored(self):
        """The old bug used 'summary'. Verify that passing 'summary' (without
        'detail') falls back to 'completed', proving the code reads 'detail'."""
        result = self._call_done({"summary": "this should be ignored"})
        self.assertEqual(result, "DONE: completed")

    def test_both_detail_and_summary_uses_detail(self):
        """If both keys exist, 'detail' should be used (not 'summary')."""
        result = self._call_done({
            "detail": "correct value",
            "summary": "wrong value",
        })
        self.assertEqual(result, "DONE: correct value")

    def test_detail_empty_string(self):
        """An empty detail string should be used as-is (not fall back)."""
        result = self._call_done({"detail": ""})
        self.assertEqual(result, "DONE: ")

    def test_unknown_tool_returns_error(self):
        """Non-existent tools should return an error string."""
        result = _execute_tool_call(
            name="nonexistent_tool",
            args={},
            camera=None,
            planner=None,
            generator=None,
            display=None,
            skip_camera=False,
        )
        self.assertEqual(result, "Unknown tool: nonexistent_tool")


class TestToolSchemaConsistency(unittest.TestCase):
    """Verify the tool schema and handler agree on the parameter name."""

    def test_done_tool_schema_uses_detail(self):
        """The TOOLS definition for 'done' should declare 'detail' as a property."""
        done_tool = None
        for tool in _agent.TOOLS:
            if tool["function"]["name"] == "done":
                done_tool = tool
                break
        self.assertIsNotNone(done_tool, "TOOLS should contain a 'done' tool definition")

        props = done_tool["function"]["parameters"]["properties"]
        self.assertIn("detail", props,
                      "done tool schema should have a 'detail' property")
        self.assertNotIn("summary", props,
                         "done tool schema should NOT have a 'summary' property")

    def test_done_tool_schema_requires_detail(self):
        """The 'detail' parameter should be listed as required."""
        done_tool = None
        for tool in _agent.TOOLS:
            if tool["function"]["name"] == "done":
                done_tool = tool
                break
        required = done_tool["function"]["parameters"].get("required", [])
        self.assertIn("detail", required,
                      "'detail' should be in the required list for the done tool")


class TestSourceCodeConsistency(unittest.TestCase):
    """Verify the actual agent.py source contains the fixed parameter name."""

    def test_agent_py_reads_detail_not_summary(self):
        """Read agent.py and confirm the done branch uses args.get('detail', ...)."""
        from pathlib import Path
        agent_path = Path(__file__).resolve().parent / "agent.py"
        source = agent_path.read_text(encoding="utf-8")

        # The fixed line should be present
        self.assertIn("args.get('detail', 'completed')", source,
                      "agent.py should use args.get('detail', 'completed') in the done handler")

        # The buggy line should NOT be present
        self.assertNotIn("args.get('summary', 'completed')", source,
                         "agent.py should NOT use args.get('summary', 'completed') in the done handler")


if __name__ == "__main__":
    unittest.main()
