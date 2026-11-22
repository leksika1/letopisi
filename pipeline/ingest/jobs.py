"""Background-task boundary for uploads. The extraction CLI handles fixture IDs."""

import logging


def run(document_id: int) -> None:
    """Leave the persisted document queued for the extraction worker."""
    logging.getLogger(__name__).info("Документ %s поставлен в очередь", document_id)
