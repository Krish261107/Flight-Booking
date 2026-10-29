import streamlit as st


def go(page):
    """Jump to another page. app.py applies the move at the start of the next run, because a sidebar widget's
    value can't be changed after the widget has been drawn."""
    st.session_state["_goto"] = page
    st.rerun()
