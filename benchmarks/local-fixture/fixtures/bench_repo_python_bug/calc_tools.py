def normalize_slug(text):
    return str(text).replace(" ", "-")


def clamp(value, minimum, maximum):
    return min(value, maximum)


def parse_bool(value):
    return bool(value)
