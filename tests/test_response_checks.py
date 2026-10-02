from response_checks import mentions_return_deadline


def test_accepts_response_with_expected_deadline():
    assert mentions_return_deadline("Return within 30 days.") is True


def test_rejects_response_with_wrong_deadline():
    assert mentions_return_deadline("Return within 90 days.") is False


def test_rejects_response_without_deadline():
    assert mentions_return_deadline("You can return it.") is False


def test_detects_phrase_even_in_incorrect_response():
    assert mentions_return_deadline("Returns within 30 days are not allowed.") is True
