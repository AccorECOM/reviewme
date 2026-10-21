"""Scrub tests: the last barrier before posting must catch the common shapes of secrets.

Run: `pytest tests/`.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reviewme.scrub import contains_secret, scrub_text

SECRET = "[REDACTED-SECRET]"
PATH = "[local-path-redacted]"


@pytest.mark.parametrize("text", [
    "ghp_abcdefghijklmnopqrstuvwxyz1234",
    "github_pat_11ABCDEFG0123456789abcdefghij",
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
    "token=abcdefgh1234567890",
    "ANTHROPIC_AUTH_TOKEN=abcdefgh1234567890",   # prefixed name (no word boundary before)
    "X-Api-Key: abcdefgh12345",
])
def test_secret_shapes_are_redacted(text):
    assert scrub_text(text) == SECRET
    assert contains_secret(text)


def test_bearer_header_keeps_name_and_hides_token():
    assert scrub_text("Authorization: Bearer abcdefgh12345678") == f"Authorization: {SECRET}"
    assert scrub_text("bearer authentication is used") == "bearer authentication is used"


def test_pem_block_is_redacted_whole():
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabc\n-----END PRIVATE KEY-----\nafter"
    assert scrub_text(pem) == f"{SECRET}\nafter"


def test_truncated_pem_block_hides_its_body_too():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAabcdefgh\nabcdefghijklmnopqrstuv\n"
    assert scrub_text(pem) == f"{SECRET}\n"


@pytest.mark.parametrize("text", [
    "`refreshToken = computeToken()` is never awaited",
    "let accessToken = keychain.read()",
    "csrfToken: string;",
    "clientSecret: process.env.CLIENT_SECRET",
])
def test_code_identifiers_are_not_secrets(text):
    assert scrub_text(text) == text


@pytest.mark.parametrize("text", [
    "src/private/Helpers.swift",
    "app/src/main/java/com/acme/root/MainActivity.kt",
    "https://example.com/opt/docs",
    're.compile(r"key:\\s*(\\w+)")',
    "a:\n",
])
def test_repo_paths_urls_and_regexes_are_not_local_paths(text):
    assert scrub_text(text) == text


def test_scrub_is_linear_on_hostile_input():
    import time
    for hostile in ("-" * 20000, "x_" * 10000, "A_" * 10000):
        start = time.perf_counter()
        scrub_text(hostile)
        assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize("text", [
    "/Users/alice/dev/app/x.swift",
    "/home/bob/x.py",
    "/tmp/build/out.log",
    r"C:\Users\bob\x.txt",
])
def test_local_paths_are_redacted(text):
    assert scrub_text(text) == PATH


@pytest.mark.parametrize("text", [
    "the tokenizer: works fine",
    "secret sauce",
    "src/orders/service.py",
    "",
])
def test_plain_text_is_untouched(text):
    assert scrub_text(text) == text
    assert not contains_secret(text)
