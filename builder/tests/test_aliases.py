from builder import aliases


def test_word_lists_have_no_duplicates():
    assert len(set(aliases.ADJECTIVES)) == len(aliases.ADJECTIVES)
    assert len(set(aliases.NOUNS)) == len(aliases.NOUNS)
    assert len(aliases.ADJECTIVES) >= 90 and len(aliases.NOUNS) >= 90


def test_alias_is_deterministic_and_case_insensitive():
    assert aliases.alias("salt", "CedricHLD") == aliases.alias("salt", "cedrichld ")
    assert aliases.alias("salt", "cedrichld") != aliases.alias("other", "cedrichld")


def test_alias_shape():
    a = aliases.alias("salt", "someone")
    adjective, noun, number = a.split(" ")
    assert adjective in aliases.ADJECTIVES and noun in aliases.NOUNS
    assert 10 <= int(number) <= 99


def test_resolve_alias_keeps_owner_and_resolves_collisions():
    salt = "salt"
    first = aliases.alias(salt, "alice")
    players = {first: {"owner": aliases.owner_fingerprint(salt, "alice")}}
    assert aliases.resolve_alias(salt, "alice", players) == first
    # Someone else already holds alice's natural alias: alice gets variant 2.
    players = {first: {"owner": "someone-else"}}
    second = aliases.resolve_alias(salt, "alice", players)
    assert second == aliases.alias(salt, "alice", 2) and second != first
