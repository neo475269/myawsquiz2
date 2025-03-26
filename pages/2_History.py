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
# Reuse the robust function definition from 1_Quiz.py
@st.cache_resource
def get_cosmos_client():
    """Initializes and returns the Cosmos DB container client. Returns None on failure."""
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.get_database_client(COSMOS_DATABASE_NAME)
        container = database.get_container_client(COSMOS_CONTAINER_NAME)
        # Optionally, try a lightweight operation like reading database properties to check connection
        # database.read()
        # st.info("Cosmos DB client initialized for history page.") # Optional feedback
        return container
    except exceptions.CosmosResourceNotFoundError:
         st.warning(f"Cosmos DB Database '{COSMOS_DATABASE_NAME}' or Container '{COSMOS_CONTAINER_NAME}' not found. Please ensure they exist.")
         return None # Return None to indicate failure
    except Exception as e:
        st.error(f"Failed to connect to Cosmos DB for history: {e}")
        return None # Return None to indicate failure

# --- Function to fetch history ---
# Cache data for a short time (e.g., 1 minute) to avoid constant DB calls
@st.cache_data(ttl=60)
def get_session_history(_container): # Pass container explicitly to ensure it's checked
    """Fetches all practice session records from Cosmos DB, ordered by date descending."""
    all_sessions = []
    # Check if the container object is valid before querying
    if _container is None:
        st.error("Cannot fetch history: Cosmos DB container not available.")
        return all_sessions # Return empty list

    try:
        # Query to get all items, ordered by session date descending
        # Select specific fields if performance becomes an issue with many large documents
        query = "SELECT * FROM c ORDER BY c.session_datetime_utc DESC"

        # Execute the query. Enable cross-partition query as we order across all partitions.
        items = list(_container.query_items(
            query=query,
            enable_cross_partition_query=True # Necessary if not filtering by partition key
        ))
        all_sessions = items
    except exceptions.CosmosHttpResponseError as e:
         st.error(f"Error fetching session history from Cosmos DB (HTTP {e.status_code}): {e.message}")
    except Exception as e:
        st.error(f"An unexpected error occurred while fetching session history: {e}")

    return all_sessions

# --- Display History Page ---
st.title("📜 Practice Session History")
st.markdown("Review your past quiz attempts below.")

# --- Get Cosmos DB Container ---
# Call the function to get the container; it might return None
cosmos_container = get_cosmos_client()

# Add a button to refresh data manually
if st.button("🔄 Refresh History"):
    # Clear the cache for get_session_history AND the resource cache for the client
    st.cache_data.clear()
    st.cache_resource.clear() # Clear client cache too, in case connection needs retry
    st.rerun()

# --- Fetch and Display Sessions ---
# Only fetch history if the container connection was successful
if cosmos_container:
    sessions = get_session_history(cosmos_container) # Pass the container object

    if not sessions:
        st.info("No practice sessions recorded yet. Go to the 'Quiz' page to start!")
    else:
        st.write(f"Found {len(sessions)} session(s). Most recent first.")
        st.markdown("---")

        # Display each session using an expander
        for i, session in enumerate(sessions):
            # --- Prepare data for display ---
            session_id = session.get("id", f"session_{i}") # Unique identifier
            quiz_name = session.get('quiz_set_name', 'Unknown Quiz')
            session_time_utc_str = session.get('session_datetime_utc')
            session_time_display = "Unknown Time"
            if session_time_utc_str:
                 try:
                    # Parse ISO 8601 UTC time
                    session_dt_utc = datetime.datetime.fromisoformat(session_time_utc_str.replace('Z', '+00:00'))
                    # Convert to local time for display (optional, requires timezone info)
                    # session_dt_local = session_dt_utc.astimezone(datetime.timezone.utc).astimezone() # Example local conversion
                    session_time_display = session_dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC') # Display UTC
                 except ValueError:
                    session_time_display = session_time_utc_str # Fallback to raw string if parsing fails

            attempt_num = session.get('attempt_number') # Get the stored attempt number
            attempt_num_str = f" (Attempt #{attempt_num})" if attempt_num is not None and attempt_num > 0 else ""

            expander_title = f"**{quiz_name}{attempt_num_str} - {session_time_display}**"

            # --- Display Expander ---
            # Expand the first (most recent) session by default
            with st.expander(expander_title, expanded=(i == 0)):

                # Display Summary Metrics
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Score", f"{session.get('correct_percentage', 0):.1f}%")
                col2.metric("Correct", f"{session.get('questions_correct', 0)} / {session.get('questions_attempted', 0)}")
                duration = session.get('duration_seconds', 0)
                col3.metric("Time", f"{duration // 60}m {duration % 60}s")
                # Display Attempt Number or Total Qs in 4th column
                if attempt_num is not None and attempt_num > 0:
                     col4.metric("Attempt No.", attempt_num)
                else:
                     # Fallback to total questions if attempt number isn't stored/valid
                     col4.metric("Total Qs in Set", session.get('total_questions_in_set', 'N/A'))

                # Optional: Display technical details
                st.caption(f"Session ID: {session_id}")
                st.caption(f"Partition Key Used: {session.get('partitionKey', 'N/A')}") # Useful for debugging partitioning

                st.subheader("Attempt Details")
                details = session.get('attempt_details', []) # Get the embedded list

                if details:
                    df_details = pd.DataFrame(details)

                    # Define desired column order and filter available columns
                    cols_order = ['question_number', 'user_answer', 'correct_answer', 'result', 'question_text_summary']
                    available_cols = [col for col in cols_order if col in df_details.columns]
                    df_details = df_details[available_cols] # Keep only available columns in desired order

                    # Sort by question number if available
                    if 'question_number' in df_details.columns:
                        # Ensure question_number is numeric before sorting if necessary
                        df_details['question_number'] = pd.to_numeric(df_details['question_number'], errors='coerce')
                        df_details = df_details.sort_values(by='question_number').reset_index(drop=True)

                    # --- Display Styled DataFrame ---
                    def highlight_results(s):
                        '''Applies color styling to the 'result' column.'''
                        return ['color: green' if v == 'Correct' else ('color: red' if v == 'Incorrect' else '') for v in s]

                    # Apply styling only if 'result' column exists
                    style_args = {}
                    if 'result' in df_details.columns:
                        style_args['subset'] = ['result']

                    st.dataframe(
                         df_details.style.apply(highlight_results, **style_args),
                         use_container_width=True,
                         hide_index=True # Hide the default pandas index column
                    )
                else:
                    st.write("No detailed attempt data was recorded for this session.")

                # Optional: Add separator between expanded sessions
                # st.markdown("---") # Consider if needed, might add too much space

else:
    # Message if connection to Cosmos DB failed initially
    st.error("Could not connect to the database to retrieve history. Please check secrets and Azure service status.")