"""Agents package.

Rebuilds CTFState after both schemas.py and event_config.py are loaded so that
the forward reference to EventConfig in CTFState.event can be resolved without
triggering a circular import at schemas.py module load time.

NOTE: schemas.py and event_config.py have a circular dependency (schemas exports
Category which event_config imports, while schemas forward-references EventConfig).
The resolution relies on importing both modules here before calling model_rebuild().
Any new module added to agents/ that imports from either should be imported *after*
the model_rebuild() call below, or use TYPE_CHECKING guards to avoid load-order issues.
"""

from agents.schemas import CTFState
from agents.event_config import EventConfig  # noqa: F401  ensures EventConfig is loaded

CTFState.model_rebuild()
