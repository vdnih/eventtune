from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class JobState:
    status: str  # "processing" | "completed" | "error"
    total: int
    done: int = 0
    result_df: Optional[pd.DataFrame] = field(default=None, repr=False)
    error: Optional[str] = None


jobs: dict[str, JobState] = {}
