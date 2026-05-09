"""HTTP API + local persistence for Say.

The desktop UI talks to the running engine over a loopback HTTP socket.
This package owns the SQLite transcript store and the FastAPI app that
serves it. The dictation engine itself (audio, model, injector) lives
in the parent package and is not modified beyond a single post-injection
write into `store`.
"""
