"""THE ENTRY WORKER: an entry's timed callables, set up, called, reset and dropped for a session
(`fsla_devtools.timing.session`) or the timing server.

    python -m fsla_devtools.timing.entry_worker

The protocol is JSON lines on the process's original stdout; what an executor prints goes to stderr.

    {"op": "setup", "id", "executor", "cell", "params", "binds"}
                                     ->  {"ready": {"built", "instrument", "draw", "loaded"}} | {"failed": WHY}
    {"op": "call", "id"}                                  ->  {"ms": MS} | {"failed": WHY}
    {"op": "drop", "id"}                                  ->  {"dropped": true}
    {"op": "memory", "executor", "cell", "params", "calls"} ->  {"memory": {...}} | {"failed": WHY}
    {"op": "clock", "executor"}                           ->  {"ghz": GHZ | null}
    {"op": "exit"}

A request's `env` holds variables set for that request alone (the lock-held markers). `call` runs the entry's reset
before timing, untimed. `draw` is the digest of the cell code this worker draws with (`fsla_devtools.cells.draw_digest`);
`loaded` is the file each module of `binds` was imported from, after the set-up, for the session to check it is the
entry's bundle's. A setup that raises is the entry's failure; any other exception replies {"error": ...}.
"""
from __future__ import annotations

import gc
import importlib
import json
import os
import sys
import traceback

from ..build.worker import load
from ..cells import draw_digest


def _loaded(binds) -> dict[str, str]:
    """Where each bound module was loaded from, as this process imported it: its directory resolved, the file itself
    not (a bundle's runfiles are real directories of links into the trees that made them, and the check is that the
    import went through the bundle)."""
    out = {}
    for name in binds:
        path = importlib.import_module(name).__file__
        out[name] = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    return out


def _cell(record):
    from ..cells import build

    return build(record)


def _release() -> None:
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available() and torch.cuda.is_initialized():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()


def _memory(timed, calls: int, before: int) -> dict:
    import torch

    if timed.reset:
        timed.reset()
    timed.call()
    torch.cuda.synchronize()
    #: THE LAUNCH'S RESERVATION, NOT THE DRAW'S: the caching allocator keeps every block the entry's build touched
    #: (a cell drawn in fp64 on the device is gigabytes it never needs again), and a reset alone starts the peak at
    #: that high-water. Releasing the cache first makes `peak_reserved` what the calls themselves reserve.
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    for _ in range(calls):
        if timed.reset:
            timed.reset()
        timed.call()
    torch.cuda.synchronize()
    return {"peak_allocated_bytes": torch.cuda.max_memory_allocated(), "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "allocated_after_bytes": torch.cuda.memory_allocated(), "allocated_before_build_bytes": before,
            "outside_allocator_bytes": int(timed.outside_allocator()) if timed.outside_allocator else 0,
            "calls": calls, "built": timed.built}


def serve() -> None:
    replies = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    live: dict[str, object] = {}
    for line in sys.stdin:
        request = json.loads(line)
        op = request["op"]
        if op == "exit":
            return
        saved = {name: os.environ.get(name) for name in request.get("env", {})}
        os.environ.update(request.get("env", {}))
        try:
            if op == "setup":
                try:
                    timed = load(request["executor"])(_cell(request["cell"]), request["params"])
                except Exception:  # noqa: BLE001 -- the entry's domain failure
                    reply = {"failed": traceback.format_exc()[-3000:]}
                else:
                    live[request["id"]] = timed
                    #: the build's own blocks back to the device (a cell drawn in fp64 is gigabytes the entry never
                    #: reads again): a session holds every member set up at once, and only what each keeps is live
                    _release()
                    reply = {"ready": {"built": timed.built, "instrument": timed.instrument, "draw": draw_digest(),
                                       "loaded": _loaded(request.get("binds", ()))}}
            elif op == "call":
                timed = live[request["id"]]
                try:
                    if timed.reset:
                        timed.reset()
                    reply = {"ms": float(timed.call())}
                except Exception:  # noqa: BLE001 -- the entry's domain failure: this entry stops, the session goes on
                    reply = {"failed": traceback.format_exc()[-3000:]}
            elif op == "drop":
                live.pop(request["id"], None)
                _release()
                reply = {"dropped": True}
            elif op == "memory":
                import torch

                _release()
                before = torch.cuda.memory_allocated()
                try:
                    timed = load(request["executor"])(_cell(request["cell"]), request["params"])
                    reply = {"memory": _memory(timed, request["calls"], before)}
                except Exception:  # noqa: BLE001 -- the entry's domain failure
                    reply = {"failed": traceback.format_exc()[-3000:]}
                finally:
                    timed = None
                    _release()
            elif op == "clock":
                reply = {"ghz": load(request["executor"])()}
            else:
                raise ValueError(f"unknown op {op!r}")
        except Exception:  # noqa: BLE001 -- the timing system names the entry and fails the session
            reply = {"error": traceback.format_exc()[-3000:]}
        finally:
            for name, value in saved.items():
                os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
        replies.write(json.dumps(reply) + "\n")


if __name__ == "__main__":
    serve()
