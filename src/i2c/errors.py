class ManualReview(Exception):
    """Stop the flow and hand over to a human. Raised on any ambiguity or failed check."""

    def __init__(self, step: str, reason: str):
        super().__init__(f"[{step}] {reason}")
        self.step = step
        self.reason = reason
