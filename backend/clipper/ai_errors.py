"""Errors that mean the AI service cannot be used right now (as opposed to a bad request).

No key, a key rejected, no credit left, rate limited, the service down or out of
reach: the work is not wrong, the AI is just unavailable. Features catch
`AiUnavailable` to fall back on rules (clip moments, brief checks) or to wait
(campaign listings), and the status page shows a banner until a call succeeds.
"""


class AiUnavailable(RuntimeError):
    pass
