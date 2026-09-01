"""Minimal web chat UI for the note-taking agent.

    streamlit run streamlit_app.py

Same NoteAgent as the CLI — this file is only presentation.
"""

from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

from note_agent.agent import NoteAgent
from note_agent.llm import GroqLLM
from note_agent.storage import NoteStore

load_dotenv()
st.set_page_config(page_title="Note-Taking Agent", page_icon="🗒️")

with st.sidebar:
    st.header("Settings")
    user_id = st.text_input("User id", value="default")
    db_path = st.text_input("Database file", value="notes.db")
    if st.button("Reset conversation"):
        st.session_state.pop("agent", None)
        st.session_state.pop("history", None)
        st.rerun()


@st.cache_resource
def get_store(path: str) -> NoteStore:
    return NoteStore(path)


store = get_store(db_path)

if "agent" not in st.session_state or st.session_state.get("user_id") != user_id:
    try:
        st.session_state.agent = NoteAgent(store, llm=GroqLLM(), user_id=user_id)
        st.session_state.user_id = user_id
        st.session_state.history = []
    except RuntimeError as exc:
        st.error(str(exc))
        st.stop()

st.title("🗒️ Note-Taking Agent")

# Notes are kept only for today and the future; sweep out any that have passed.
for _n in store.purge_past_notes():
    st.toast(f"Auto-removed past note: {_n.title} ({_n.event_date})")

with st.sidebar:
    st.subheader("Your notes")
    for n in store.all_notes(user_id):
        when = f"📅 {n.event_date} · " if n.event_date else ""
        st.caption(f"[{n.id}] {when}**{n.title}** " + " ".join(f"`#{t}`" for t in n.tags))

for role, content in st.session_state.history:
    with st.chat_message(role):
        st.markdown(content)

if prompt := st.chat_input("Message your notes…"):
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            turn = st.session_state.agent.send(prompt)
        if turn.tool_calls:
            with st.expander(f"{len(turn.tool_calls)} tool call(s)"):
                for tc in turn.tool_calls:
                    st.code(f"{tc.name}({tc.arguments}) -> {tc.result.get('status')}", language="python")
        st.markdown(turn.reply)
    st.session_state.history.append(("assistant", turn.reply))
    st.rerun()
