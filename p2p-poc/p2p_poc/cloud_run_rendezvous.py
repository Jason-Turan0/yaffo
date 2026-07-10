"""Cloud Run entrypoint. `gcloud run deploy` points at this module's `app`;

state lives in Firestore instead of process memory (see rendezvous_store.py
for why that matters on Cloud Run specifically). Local dev/tests use
rendezvous.main()/create_app() directly and never import this file.
"""

from __future__ import annotations

from .rendezvous import create_app
from .rendezvous_store import FirestoreRegistryStore

app = create_app(store=FirestoreRegistryStore())
