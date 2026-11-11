"""AI Stock Agent Scorecard: a lookahead-free, verifiable track record for AI research agents."""
__version__ = "0.4.0"

from .ledger import Ledger, verify_public  # noqa: E402
from .schema import Close, Prediction, ValidationError  # noqa: E402
from .score import resolve, scorecard  # noqa: E402

__all__ = ["Close", "Ledger", "Prediction", "ValidationError", "resolve", "scorecard", "verify_public", "__version__"]
