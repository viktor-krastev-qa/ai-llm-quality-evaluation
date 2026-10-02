def mentions_return_deadline(response: str) -> bool:
    """Learning example: presence of a phrase does not prove correctness."""
    return "30 days" in response
