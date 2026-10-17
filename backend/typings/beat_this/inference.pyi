# Exact used API inspected at upstream v1.1.0 inference.py and postprocessor.py.
from collections.abc import Callable
import numpy as np
import torch
from numpy.typing import NDArray
load_checkpoint: Callable[[str, str | torch.device], dict[str, object]]
class Audio2Beats:
    def __init__(self, checkpoint_path: str = ..., device: str = ..., float16: bool = ..., dbn: bool = ...) -> None: ...
    def __call__(self, signal: NDArray[np.float32], sr: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]: ...
