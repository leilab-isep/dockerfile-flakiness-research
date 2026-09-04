/**
 * Thin bridge to Docker Parfum's programmatic API.
 *
 * The `docker-parfum` CLI only prints human-readable text (no JSON output option), so
 * this script calls the library's own `parseAndMatch` function directly and prints the
 * violations as JSON on stdout, for the Python adapter to parse.
 *
 * Usage: node parfum_runner.js <path-to-Dockerfile>
 */
const fs = require("fs");
const { parseAndMatch } = require("@tdurieux/docker-parfum");

const path = process.argv[2];
if (!path) {
  console.error("usage: node parfum_runner.js <Dockerfile>");
  process.exit(2);
}

const content = fs.readFileSync(path, "utf8");
const violations = parseAndMatch(content);

const out = violations.map((v) => ({
  rule_id: v.rule && v.rule.name,
  line_number: v.node && v.node.position ? v.node.position.lineStart + 1 : null,
  message: v.rule && v.rule.description,
}));

process.stdout.write(JSON.stringify(out));
