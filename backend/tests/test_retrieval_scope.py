"""Retrieval only ever searches one client's chunks."""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage

from app.agents.public.nodes import retrieve as retrieve_node_module
from app.retrieval import vector_retriever


def test_retrieve_chunks_requires_a_client():
    with pytest.raises(ValueError, match="client_id is required"):
        vector_retriever.retrieve_chunks("hours?", client_id="")


def test_retrieve_chunks_filters_on_the_client(monkeypatch):
    index = MagicMock()
    index.as_retriever.return_value.retrieve.return_value = []
    monkeypatch.setattr(vector_retriever, "get_vector_index", lambda _cfg: index)

    vector_retriever.retrieve_chunks("hours?", client_id="client-123")

    filters = index.as_retriever.call_args.kwargs["filters"]
    assert [(f.key, f.value) for f in filters.filters] == [("client_id", "client-123")]


def test_the_retrieve_node_passes_the_states_client(monkeypatch):
    seen = {}

    def fake_retrieve(query, client_id):
        seen.update(query=query, client_id=client_id)
        return []

    monkeypatch.setattr(retrieve_node_module, "retrieve_chunks", fake_retrieve)
    retrieve_node_module.retrieve_node(
        {"messages": [HumanMessage(content="Opening hours?")], "client_id": "client-9"}
    )
    assert seen == {"query": "Opening hours?", "client_id": "client-9"}
