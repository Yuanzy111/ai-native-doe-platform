"""HTTP API layer: a thin FastAPI surface over the application service (§6).

The API exposes the existing domain, persistence, application service, and
optimizer adapter as a callable vertical slice. It owns no business rules of its
own: routers map request DTOs to domain objects, delegate writes to the
application service and reads to the query service, and translate every backend
exception into the uniform error contract.
"""

from backend.api.app import create_app

__all__ = ["create_app"]
