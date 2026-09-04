"""FlakiScan -- static analysis for flakiness-prone patterns in Dockerfiles.

This package currently implements detection and scoring: running Hadolint, Docker
Parfum, and a custom rule engine against a Dockerfile, then classifying and weighting
the combined findings. Automated repair and build-verified patch validation are not
yet implemented.
"""
