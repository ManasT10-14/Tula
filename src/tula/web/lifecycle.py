"""ASGI startup recovery and shutdown for the persistent inspection queue."""
from __future__ import annotations

import threading
from contextlib import ExitStack, asynccontextmanager

from starlette.concurrency import run_in_threadpool

from ..services.jobs import InspectionJobs
from ..storage.backup import runtime_lease

_FACTORY_LOCK = threading.RLock()


def _finish_shutdown(worker, leases):
    try:
        worker.stop(timeout=None)
    finally:
        leases.close()


def get_jobs(web):
    """Use one worker instance per app, including concurrent first requests."""
    with _FACTORY_LOCK:
        instance = getattr(web.app.state, "jobs", None)
        security = getattr(web.app.state, "security", None)
        if instance is not None and (instance.repo.path != web.repo.path or instance.security is not security):
            if instance.stop() is False:
                raise ValueError("The previous inspection worker is still shutting down. Retry shortly.")
            instance = None
        if instance is None:
            instance = InspectionJobs(web.repo, web.rules, security)
            web.app.state.jobs = instance
        if instance.start() is False:
            raise ValueError("The inspection worker is shutting down. Retry shortly.")
        return instance


def install_job_lifecycle(web):
    """Wrap the existing lifespan once; call before the app starts serving.

    FastAPI runs startup before yield and shutdown afterwards:
    https://fastapi.tiangolo.com/advanced/events/
    """
    app = web.app
    if getattr(app.state, "job_lifecycle_installed", False):
        return
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        root = getattr(web, "ROOT", web.repo.path.parent.parent)
        with ExitStack() as leases:
            leases.enter_context(runtime_lease(root))
            async with previous(application) as state:
                worker = await run_in_threadpool(get_jobs, web)
                try:
                    yield state
                finally:
                    if await run_in_threadpool(worker.stop, timeout=30) is False:
                        # Shutdown may time out while OCR is still writing. Keep
                        # maintenance excluded until that worker actually exits.
                        thread = threading.Thread(target=_finish_shutdown,
                            args=(worker, leases.pop_all()), name="tula-shutdown-lease", daemon=True)
                        application.state.shutdown_lease_thread = thread
                        thread.start()

    app.router.lifespan_context = lifespan
    app.state.job_lifecycle_installed = True
