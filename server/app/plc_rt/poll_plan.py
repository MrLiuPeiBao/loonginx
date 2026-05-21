from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class PollStep:
    name: str
    priority: int = 30


POLL_STEP_1 = PollStep("poll_step_1")
POLL_STEP_2 = PollStep("poll_step_2")
POLL_STEP_3 = PollStep("poll_step_3")
POLL_STEP_4 = PollStep("poll_step_4")


def build_default_poll_steps() -> List[PollStep]:
    return [POLL_STEP_1, POLL_STEP_2, POLL_STEP_3]
