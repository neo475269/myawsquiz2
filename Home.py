# Home.py
import streamlit as st

st.set_page_config(
    page_title="Quiz Revision App",
    layout="wide"
)

st.title("Welcome to the Quiz Revision App!")
st.markdown("""
Use the sidebar to navigate:
- **Quiz:** Take a new practice quiz.
- **History:** Review your past practice sessions.

Select a quiz set and start practicing!
""")

st.sidebar.success("Select a page above.")