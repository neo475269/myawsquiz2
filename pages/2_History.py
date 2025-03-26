# pages/2_History.py
import streamlit as st
import pandas as pd
from azure.cosmos import CosmosClient, exceptions
import datetime

# --- Configuration (Using Streamlit Secrets) ---
try:
    COSMOS_ENDPOINT = st.secrets["COSMOS_ENDPOINT"]
    COSMOS_KEY = st.secrets["COSMOS_KEY"]
    COSMOS_DATABASE_NAME = st.secrets["COSMOS_DATABASE"]
    COSMOS_CONTAINER_NAME = st.secrets["COSMOS_CONTAINER"]
except KeyError as e:
    st.error(f"Missing secret: {e}. Please configure .streamlit/secrets.toml")
    st.stop()

# --- Cosmos DB Client Initialization (Cached) ---
@st.cache_resource
def get_cosmos_client():
    """Initializes and returns the Cosmos DB container client. Returns None on failure."""
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.get_database_client(COSMOS_DATABASE_NAME)
        container = database.get_container_client(COSMOS_CONTAINER_NAME)
        return container
    except exceptions.CosmosResourceNotFoundError:
         st.warning(f"Cosmos DB Database '{COSMOS_DATABASE_NAME}' or Container '{COSMOS_CONTAINER_NAME}' not found. Please ensure they exist.")
         return None
    except Exception as e:
        st.error(f"Failed to connect to Cosmos DB for history: {e}")
        return None

# --- Function to fetch history ---
@st.cache_data(ttl=60)
def get_session_history(_container):
    """Fetches all practice session records from Cosmos DB, ordered by date descending."""
    all_sessions = []
    if _container is None:
        st.error("Cannot fetch history: Cosmos DB container not available.")
        return all_sessions

    try:
        # Select only necessary fields to potentially improve performance if documents are large
        # query = "SELECT c.id, c.quiz_set_name, c.session_datetime_utc, c.attempt_number, c.correct_percentage, c.questions_attempted, c.total_questions_in_set, c.questions_correct, c.duration_seconds, c.partitionKey, c.attempt_details FROM c ORDER BY c.session_datetime_utc DESC"
        query = "SELECT * FROM c ORDER BY c.session_datetime_utc DESC" # Keep selecting all for now
        items = list(_container.query_items(query=query, enable_cross_partition_query=True))
        all_sessions = items
    except exceptions.CosmosHttpResponseError as e:
         st.error(f"Error fetching session history from Cosmos DB (HTTP {e.status_code}): {e.message}")
    except Exception as e:
        st.error(f"An unexpected error occurred while fetching session history: {e}")

    return all_sessions

# --- Define UTC+8 Timezone ---
UTC8_OFFSET = datetime.timedelta(hours=8)
UTC8_TZ = datetime.timezone(UTC8_OFFSET, name='UTC+8') # Give it a name for clarity

# --- Display History Page ---
st.title("📜 Practice Session History")
st.markdown("Review your past quiz attempts below (Times shown in UTC+8).") # Update description

# --- Get Cosmos DB Container ---
cosmos_container = get_cosmos_client()

if st.button("🔄 Refresh History"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

# --- Fetch and Display Sessions ---
if cosmos_container:
    sessions = get_session_history(cosmos_container)

    if not sessions:
        st.info("No practice sessions recorded yet. Go to the 'Quiz' page to start!")
    else:
        st.write(f"Found {len(sessions)} session(s). Most recent first.")
        st.markdown("---")

        for i, session in enumerate(sessions):
            # --- Prepare data for display (including for the title) ---
            session_id = session.get("id", f"session_{i}")
            quiz_name = session.get('quiz_set_name', 'Unknown Quiz')
            session_time_utc_str = session.get('session_datetime_utc')
            attempt_num = session.get('attempt_number')

            score_percentage = session.get('correct_percentage', 0)
            questions_attempted = session.get('questions_attempted', 0)
            total_questions_in_set = session.get('total_questions_in_set', 0)

            # Format time display in UTC+8
            session_time_display = "Unknown Time"
            if session_time_utc_str:
                 try:
                    # Parse ISO string (ensure it's TZ aware, default is UTC if 'Z')
                    dt_utc = datetime.datetime.fromisoformat(session_time_utc_str.replace('Z', '+00:00'))
                    # Convert to UTC+8 timezone
                    dt_utc8 = dt_utc.astimezone(UTC8_TZ)
                    # Format the UTC+8 time
                    session_time_display = dt_utc8.strftime('%Y-%m-%d %H:%M %Z') # Use %Z to show 'UTC+8'
                 except ValueError:
                    session_time_display = session_time_utc_str + " (Parse Error)" # Indicate fallback

            attempt_num_str = f" (Attempt #{attempt_num})" if attempt_num is not None and attempt_num > 0 else ""
            score_str = f"{score_percentage:.1f}%"
            attempted_str = f"{questions_attempted} Att."
            if isinstance(total_questions_in_set, (int, float)) and total_questions_in_set > 0:
                 attempted_str = f"{questions_attempted}/{int(total_questions_in_set)} Qs"

            # --- Construct the Expander Title ---
            expander_title = (
                f"**{quiz_name}{attempt_num_str}** | "
                f"Score: {score_str} | "
                f"Qs: {attempted_str} | "
                f"{session_time_display}" # Now shows UTC+8 time
            )

            # --- Display Expander ---
            with st.expander(expander_title, expanded=(i == 0)):

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Correct", f"{session.get('questions_correct', 0)} / {questions_attempted}")
                duration = session.get('duration_seconds', 0)
                col2.metric("Time Taken", f"{duration // 60}m {duration % 60}s")
                if isinstance(total_questions_in_set, (int, float)) and total_questions_in_set > 0:
                    col3.metric("Total Qs in Set", int(total_questions_in_set))
                if attempt_num is not None and attempt_num > 0:
                     col4.metric("Attempt No.", attempt_num)


                st.caption(f"Session ID: {session_id}")
                st.caption(f"Partition Key Used: {session.get('partitionKey', 'N/A')}")

                st.subheader("Attempt Details")
                details = session.get('attempt_details', [])

                if details:
                    df_details = pd.DataFrame(details)
                    cols_order = ['question_number', 'user_answer', 'correct_answer', 'result', 'question_text_summary']
                    available_cols = [col for col in cols_order if col in df_details.columns]
                    df_details = df_details[available_cols]

                    if 'question_number' in df_details.columns:
                        df_details['question_number'] = pd.to_numeric(df_details['question_number'], errors='coerce')
                        df_details = df_details.sort_values(by='question_number').reset_index(drop=True)

                    def highlight_results(s):
                        return ['color: green' if v == 'Correct' else ('color: red' if v == 'Incorrect' else '') for v in s]

                    style_args = {}
                    if 'result' in df_details.columns: style_args['subset'] = ['result']

                    st.dataframe(
                         df_details.style.apply(highlight_results, **style_args),
                         use_container_width=True,
                         hide_index=True
                    )
                else:
                    st.write("No detailed attempt data was recorded for this session.")

else:
    st.error("Could not connect to the database to retrieve history. Please check secrets and Azure service status.")