import tempfile
import unittest
from pathlib import Path

from flakiscan.detection.dockerfile_parser import parse_dockerfile

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestDockerfileParser(unittest.TestCase):
    def test_skips_comments_and_blank_lines(self):
        with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
            f.write("# a comment\n\nFROM python:3.11\n\n# another\nRUN echo hi\n")
            path = f.name

        instructions = parse_dockerfile(path)
        self.assertEqual([i.instruction for i in instructions], ["FROM", "RUN"])
        self.assertEqual(instructions[0].args, "python:3.11")

    def test_line_continuation_reports_starting_line_number(self):
        with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
            f.write("RUN apt-get update && \\\n    apt-get install -y curl\n")
            path = f.name

        instructions = parse_dockerfile(path)
        self.assertEqual(len(instructions), 1)
        self.assertEqual(instructions[0].line_number, 1)
        self.assertIn("apt-get install -y curl", instructions[0].args)

    def test_parses_flaky_fixture(self):
        instructions = parse_dockerfile(str(FIXTURES / "flaky.Dockerfile"))
        instruction_types = [i.instruction for i in instructions]
        self.assertIn("FROM", instruction_types)
        self.assertIn("ARG", instruction_types)
        self.assertGreaterEqual(instruction_types.count("RUN"), 4)


if __name__ == "__main__":
    unittest.main()
