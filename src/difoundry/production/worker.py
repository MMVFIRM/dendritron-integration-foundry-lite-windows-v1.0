from __future__ import annotations

import argparse
import os
import signal
import socket
import threading
import time
from contextlib import suppress

from .queue import JobLeaseLostError


def _context():
    # Import lazily so the process installs SIGTERM/SIGINT handlers before
    # database and application initialization begins.
    from .api import context
    return context()


def _audit_lease_loss(job, worker: str, stage: str, error: Exception | None = None) -> None:
    details = {"kind": job.kind, "worker": worker, "stage": stage}
    if error is not None:
        details["error"] = str(error)
    with suppress(Exception):
        _context().platform.audit.append(
            job.tenant_id, None, "job.lease_lost", "job", job.job_id, details
        )


def run_once(
    worker_id: str | None = None,
    *,
    lease_seconds: int = 60,
    heartbeat_seconds: float | None = None,
) -> bool:
    ctx = _context()
    worker = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    claimed = ctx.platform.queue.claim(worker, lease_seconds=lease_seconds)
    if claimed is None:
        return False
    job, payload, lease_token = claimed

    stop_heartbeat = threading.Event()
    lease_lost = threading.Event()
    interval = heartbeat_seconds if heartbeat_seconds is not None else max(1.0, lease_seconds / 3)

    def heartbeat() -> None:
        while not stop_heartbeat.wait(interval):
            try:
                ctx.platform.queue.renew(job.job_id, worker, lease_token, lease_seconds=lease_seconds)
            except JobLeaseLostError:
                lease_lost.set()
                return
            except Exception:
                # A transient database error must not manufacture ownership. Stop
                # completion and let the lease expire/requeue safely.
                lease_lost.set()
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name=f"difoundry-lease-{job.job_id}",
        daemon=True,
    )
    heartbeat_thread.start()

    execution_error: Exception | None = None
    detail = None
    try:
        detail = ctx.platform.execute_job(job.kind, job.tenant_id, payload)
    except Exception as exc:  # execution failure is handled after heartbeat stops
        execution_error = exc
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=max(1.0, interval + 0.5))

    if lease_lost.is_set():
        _audit_lease_loss(job, worker, "heartbeat", execution_error)
        return True

    if execution_error is None:
        try:
            ctx.platform.queue.complete(job.job_id, worker, lease_token)
        except JobLeaseLostError as exc:
            _audit_lease_loss(job, worker, "complete", exc)
            return True
        ctx.platform.audit.append(
            job.tenant_id, None, "job.succeeded", "job", job.job_id,
            {"kind": job.kind, "detail": detail},
        )
        return True

    try:
        status = ctx.platform.queue.fail(job.job_id, worker, lease_token, str(execution_error))
    except JobLeaseLostError as exc:
        _audit_lease_loss(job, worker, "fail", exc)
        return True
    ctx.platform.audit.append(
        job.tenant_id, None, "job.failed", "job", job.job_id,
        {"kind": job.kind, "status": status, "error": str(execution_error)},
    )
    return True


def install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dendritron Integration Foundry durable worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--heartbeat-seconds", type=float)
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    if args.lease_seconds < 2:
        parser.error("--lease-seconds must be at least 2")
    if args.heartbeat_seconds is not None and not 0 < args.heartbeat_seconds < args.lease_seconds:
        parser.error("--heartbeat-seconds must be positive and shorter than the lease")

    if args.once:
        run_once(
            args.worker_id,
            lease_seconds=args.lease_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        return

    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    print("difoundry-worker ready", flush=True)
    while not stop_event.is_set():
        worked = run_once(
            args.worker_id,
            lease_seconds=args.lease_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
        )
        if not worked:
            stop_event.wait(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()
