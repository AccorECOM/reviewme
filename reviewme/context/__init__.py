"""External context providers (ADR v3 D9).

A provider enriches a reviewer's prompt with data the diff does not contain (ticket,
spec, incident...). Non-negotiable rule: it is ALWAYS optional. Source unavailable ->
the reviewer that requires it is skipped, never an error, and the technical review
still goes through. The core depends on no external service.
"""
