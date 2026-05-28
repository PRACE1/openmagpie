"""Feed services package.

The Feed model has two distinct concerns: the Feed row itself (CRUD +
poll-state) and the items log it accumulates (record, prune, query).
Each gets its own service class so neither file grows past
comprehension. `FeedService.Global` is the cross-tenant escape hatch
(scheduler, debug).
"""

from ._global import FeedGlobal
from ._items import FeedItemService
from ._service import FeedService

__all__ = ["FeedGlobal", "FeedItemService", "FeedService"]
