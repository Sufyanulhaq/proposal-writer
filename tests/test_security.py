from app.security import DUMMY, hash_password, new_token, token_hash, verify_password


def test_a_password_hash_is_salted_and_never_contains_the_password():
    a, b = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert a != b and a.startswith("scrypt$") and "correct" not in a


def test_the_right_password_verifies_and_wrong_ones_do_not():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("Correct horse battery", h) and not verify_password("", h)


def test_a_malformed_or_foreign_hash_is_just_a_failure():
    for bad in ["", "garbage", "bcrypt$1$2$3$4$5", "scrypt$x$y$z$!!$!!", "scrypt$16384$8$1$AAAA"]:
        assert not verify_password("anything", bad)


def test_the_hash_remembers_its_own_cost_settings():
    h = hash_password("pw", n=2**10)
    assert "$1024$" in h and verify_password("pw", h)


def test_tokens_are_long_unique_and_stored_only_as_a_hash():
    t1, t2 = new_token(), new_token()
    assert t1 != t2 and len(t1) >= 40
    assert token_hash(t1) != t1 and len(token_hash(t1)) == 64 and token_hash(t1) == token_hash(t1)


def test_the_dummy_hash_is_valid_so_unknown_emails_cost_the_same_time():
    assert DUMMY.startswith("scrypt$") and not verify_password("guess", DUMMY)
