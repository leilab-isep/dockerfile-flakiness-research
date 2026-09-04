"""Automated repair of flakiness findings detected in a Dockerfile.

`engine.repair_dockerfile` is the entry point: it runs detection, groups findings by
the instruction they belong to, and rewrites each instruction using the applicable
rules in `rules.py`. A finding that cannot be fixed automatically (because a required
external lookup fails, or because the rule has no automated fix at all) gets a `# TODO`
comment instead of being silently dropped.
"""
