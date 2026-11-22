"""Run Uvicorn and exit normally after the test harness requests shutdown."""

import os
import signal
import sys

import uvicorn


def main() -> None:
    # Uvicorn handles a graceful stop, then re-raises the signal for its caller.
    # A normal return lets Python flush subprocess coverage, including on Windows.
    stop = signal.SIGBREAK if os.name == "nt" else signal.SIGTERM
    signal.signal(stop, lambda *_: None)
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=int(sys.argv[1]))


if __name__ == "__main__":
    main()
