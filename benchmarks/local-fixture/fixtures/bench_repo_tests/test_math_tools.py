from math_tools import average


def test_average_empty_returns_zero():
    assert average([]) == 0


def test_average_numbers():
    assert average([2, 4, 6]) == 4
