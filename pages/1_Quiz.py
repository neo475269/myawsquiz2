# pages/1_Quiz.py
import streamlit as st
import csv
import re
import random
import pandas as pd
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from io import StringIO
import datetime
import uuid # To generate unique session IDs
import time # To get timestamps

# --- Configuration (Using Streamlit Secrets) ---
try:
    # Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = st.secrets["AZURE_STORAGE_CONNECTION_STRING"]
    CONTAINER_NAME = st.secrets["AZURE_STORAGE_CONTAINER_NAME"]

    # Azure Cosmos DB
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
        # database.read() # Optional check
        return container
    except exceptions.CosmosResourceNotFoundError:
         st.warning(f"Cosmos DB Database '{COSMOS_DATABASE_NAME}' or Container '{COSMOS_CONTAINER_NAME}' not found. Please ensure they exist.")
         return None
    except Exception as e:
        st.error(f"Failed to connect to Cosmos DB. Please check configuration and network.")
        return None

# --- Function to save session data ---
def save_session_to_db(session_data):
    """Saves the completed quiz session data to Cosmos DB."""
    container = get_cosmos_client()
    if container is None:
         st.error("Cannot save session: Failed to get Cosmos DB container.")
         return False # Indicate failure

    try:
        container.create_item(body=session_data)
        st.success("Practice session saved successfully!")
        return True # Indicate success
    except exceptions.CosmosHttpResponseError as e:
        st.error(f"Error saving session to Cosmos DB (HTTP {e.status_code}): {e.message}")
        return False # Indicate failure
    except Exception as e:
        st.error(f"An unexpected error occurred while saving the session: {e}")
        return False # Indicate failure

# --- Function to get attempt count ---
def get_attempt_count_for_quiz(container, quiz_name):
    """Queries Cosmos DB to count existing sessions for a given quiz name. Assumes quiz_name is the partition key."""
    if container is None:
        st.warning("Cannot get attempt count: Cosmos DB container not available.")
        return 0

    try:
        query = "SELECT VALUE COUNT(1) FROM c WHERE c.partitionKey = @quiz_name"
        parameters = [{"name": "@quiz_name", "value": quiz_name}]
        result = list(container.query_items(
            query=query,
            parameters=parameters,
            partition_key=quiz_name
        ))
        return result[0] if result else 0
    except exceptions.CosmosHttpResponseError as e:
        st.warning(f"Could not accurately count previous attempts (Code: {e.status_code}). Assuming 0.")
        return 0
    except Exception as e:
        st.error(f"An unexpected error occurred while counting attempts: {e}")
        return 0

# --- Azure Blob Storage Client (Cached) ---
@st.cache_resource
def get_container_client():
    """Gets the Azure Blob Storage container client."""
    try:
        return ContainerClient.from_connection_string(
            conn_str=AZURE_STORAGE_CONNECTION_STRING,
            container_name=CONTAINER_NAME
        )
    except Exception as e:
        st.error(f"Error connecting to Azure Blob Storage: {e}")
        return None

# --- CSV Parsing Function (Cached per blob_name) ---
@st.cache_data
def parse_questions_csv(blob_name):
    """Parses the CSV from Azure Blob Storage, handles multi-answer, images, and returns question data."""
    questions = {}
    current_question = None
    try:
         blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
         blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
         blob_data = blob_client.download_blob()
         csv_content = blob_data.content_as_text(encoding='utf-8')
    except Exception as e:
        st.error(f"Error accessing blob '{blob_name}': {e}")
        return []

    try:
        csv_file = StringIO(csv_content)
        reader = csv.reader(csv_file)
        next(reader, None) # Skip header row

        for row in reader:
            if not row or not row[0].strip(): continue
            row_content = row[0].strip()

            if row_content.startswith("#") and row_content[1:].isdigit():
                if current_question is not None:
                    questions[current_question['question_number']] = current_question
                current_question = {
                    'question_number': int(row_content[1:]),
                    'question_text': [], 'answer_choices': [], 'correct_answer': "",
                    'community_vote': [], 'user_answer': None
                }
            elif current_question is not None:
                if re.match(r"^[A-F]\.\s", row_content):
                    current_question['answer_choices'].append((row_content, None))
                elif row_content.startswith("https://i.postimg.cc") and current_question['answer_choices']:
                    last_choice_text, _ = current_question['answer_choices'][-1]
                    current_question['answer_choices'][-1] = (last_choice_text, row_content)
                elif row_content.startswith(("Correct Answer:", "Suggested Answer:")):
                    answer_string = row_content.split(":", 1)[1].strip()
                    current_question['correct_answer'] = "".join(filter(str.isalpha, answer_string)).upper()
                elif row_content.startswith("Community vote distribution"):
                    continue
                elif row_content:
                    if not current_question['answer_choices']:
                        if row_content.startswith("https://i.postimg.cc"):
                            current_question['question_text'].append(("", row_content))
                        else:
                            current_question['question_text'].append((row_content, None))
                    else:
                        current_question['community_vote'].append(row_content)

        if current_question is not None:
            questions[current_question['question_number']] = current_question

    except Exception as e:
        st.error(f"Error parsing CSV content from '{blob_name}': {e}")
        return []

    if not questions:
        st.warning(f"No questions parsed from '{blob_name}'. Check file format and content.")
    return list(questions.values())

# --- Main Quiz Logic ---
st.title("🧠 AWS Solutions Architect Quiz Practice")

# --- Sidebar ---
with st.sidebar:
    st.header("Quiz Setup")
    blob_container_client = get_container_client()
    selected_file = None
    if blob_container_client:
        try:
            blob_list = [blob.name for blob in blob_container_client.list_blobs() if blob.name.lower().endswith('.csv')]
            if not blob_list:
                st.warning("No CSV files found in the container.")
            else:
                selected_file = st.selectbox("Choose a Quiz Set (CSV):", sorted(blob_list), key="file_select", index=None, placeholder="Select quiz set...")
        except Exception as e:
            st.error(f"Error listing blobs: {e}")
    else:
        st.error("Could not connect to Azure Blob Storage to list files.")

    question_order = st.radio("Question Order:", ("Random", "Original"), index=0, key="order_select")
    start_quiz = st.button("Start Quiz", type="primary", disabled=not selected_file)

# Initialize session state variables safely
default_state = {
    'questions': [], 'current_question_index': 0, 'score': 0,
    'total_attempted': 0, 'quiz_started': False, 'answer_submitted': False,
    'user_answers_details': [], 'quiz_start_time': None,
    'selected_quiz_file': None, 'total_questions_in_set': 0, 'quiz_complete': False,
    'session_saved': False # Flag to prevent duplicate saves
}
for key, value in default_state.items():
    if key not in st.session_state:
        st.session_state[key] = value

# --- Quiz Flow ---

# Start Quiz Logic
if start_quiz and selected_file:
    # Reset state for a new quiz attempt
    st.session_state['current_question_index'] = 0
    st.session_state['score'] = 0
    st.session_state['total_attempted'] = 0
    st.session_state['answer_submitted'] = False
    st.session_state['user_answers_details'] = []
    st.session_state['quiz_start_time'] = None
    st.session_state['selected_quiz_file'] = selected_file
    st.session_state['total_questions_in_set'] = 0
    st.session_state['quiz_complete'] = False
    st.session_state['quiz_started'] = True
    st.session_state['session_saved'] = False # <<< RESET SAVE FLAG HERE
    # parse_questions_csv.clear() # Optional: Clear cache if needed

    try:
        st.session_state['questions'] = parse_questions_csv(selected_file)
        st.session_state['total_questions_in_set'] = len(st.session_state['questions'])
        if not st.session_state['questions']:
             st.error(f"Failed to load questions from '{selected_file}'. Cannot start quiz.")
             st.session_state['quiz_started'] = False
        else:
            if question_order == "Random":
                random.shuffle(st.session_state['questions'])
            st.session_state['quiz_start_time'] = time.time()
            st.rerun()
    except Exception as e:
        st.error(f"An error occurred while preparing the quiz: {e}")
        st.session_state['quiz_started'] = False

# Display Quiz Questions
if st.session_state['quiz_started'] and not st.session_state['quiz_complete']:
    if st.session_state['current_question_index'] < len(st.session_state['questions']):
        question_data = st.session_state['questions'][st.session_state['current_question_index']]
        q_num = question_data.get('question_number', 'N/A') # Get question number safely

        st.info(f"Question {st.session_state['current_question_index'] + 1} of {len(st.session_state['questions'])}")
        st.subheader(f"Question #{q_num}:")

        for text, image_url in question_data.get('question_text', []):
            if image_url: st.image(image_url, width=600)
            elif text: st.write(text)
        st.markdown("---")

        user_answer_selection = []
        correct_answer_str = question_data.get('correct_answer', '')
        is_multiple_choice = len(correct_answer_str) > 1
        options_with_images = question_data.get('answer_choices', [])

        if not is_multiple_choice:
            option_texts = [opt[0] for opt in options_with_images]
            selected_option_text = st.radio(
                "Select one answer:", option_texts,
                key=f"radio_{q_num}_{st.session_state['current_question_index']}",
                disabled=st.session_state['answer_submitted'], index=None
            )
            if selected_option_text:
                user_answer_selection.append(selected_option_text[0])
            for choice_text, image_url in options_with_images:
                 if image_url:
                      st.write(f"*{choice_text}*")
                      st.image(image_url, width=400)
        else:
            st.write("Select all that apply:")
            for i, (choice_text, image_url) in enumerate(options_with_images):
                if st.checkbox(choice_text,
                               key=f"checkbox_{q_num}_{i}_{st.session_state['current_question_index']}",
                               disabled=st.session_state['answer_submitted']):
                    user_answer_selection.append(choice_text[0])
                if image_url:
                    st.image(image_url, width=400)

        st.markdown("---")

        feedback_placeholder = st.empty()
        if st.session_state['answer_submitted']:
             with feedback_placeholder.container(border=True):
                 correct_answer_sorted = "".join(sorted(correct_answer_str))
                 user_answer_sorted = st.session_state['questions'][st.session_state['current_question_index']].get('user_answer', '')
                 if user_answer_sorted == correct_answer_sorted: st.success("✅ Correct!")
                 else: st.error(f"❌ Incorrect. Your answer: {user_answer_sorted or 'None Selected'}")
                 st.info(f"💡 Correct Answer: {correct_answer_sorted}")
                 community_votes = question_data.get('community_vote', [])
                 if community_votes:
                     st.write("**Community Vote Distribution:**")
                     for vote in community_votes: st.caption(f"  {vote}")

        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            if not st.session_state['answer_submitted']:
                submit_pressed = st.button("Submit Answer", key=f"submit_{q_num}", type="primary", use_container_width=True)
                if submit_pressed:
                    if not user_answer_selection: st.warning("Please select an answer.")
                    else:
                        st.session_state['answer_submitted'] = True
                        st.session_state['total_attempted'] += 1
                        user_answer_str_sorted = "".join(sorted(user_answer_selection))
                        correct_answer_str_sorted = "".join(sorted(correct_answer_str))
                        st.session_state['questions'][st.session_state['current_question_index']]['user_answer'] = user_answer_str_sorted
                        is_correct = user_answer_str_sorted == correct_answer_str_sorted
                        if is_correct: st.session_state['score'] += 1

                        question_text_summary = "N/A"
                        if question_data.get('question_text'):
                             first_line = question_data['question_text'][0][0]
                             question_text_summary = (first_line[:100] + "...") if len(first_line) > 100 else first_line

                        st.session_state['user_answers_details'].append({
                            'question_number': q_num,
                            'question_text_summary': question_text_summary,
                            'correct_answer': correct_answer_str_sorted,
                            'user_answer': user_answer_str_sorted,
                            'result': 'Correct' if is_correct else 'Incorrect'
                        })
                        st.rerun()
        with col2:
            if st.session_state['answer_submitted']:
                is_last_question = st.session_state['current_question_index'] >= len(st.session_state['questions']) - 1
                button_text = "🏁 Finish Quiz" if is_last_question else "Next Question ➡️"
                next_pressed = st.button(button_text, key="next_finish", use_container_width=True)
                if next_pressed:
                    if not is_last_question:
                        st.session_state['current_question_index'] += 1
                        st.session_state['answer_submitted'] = False
                        st.rerun()
                    else:
                        st.session_state['quiz_complete'] = True
                        st.session_state['quiz_started'] = False
                        st.rerun()
        with col3:
             if st.session_state['quiz_started'] and not st.session_state['quiz_complete']:
                 if st.button("End Quiz Now", key="end_early", use_container_width=True):
                     st.session_state['quiz_complete'] = True
                     st.session_state['quiz_started'] = False
                     st.rerun()
    else:
        st.warning("Reached end of questions unexpectedly.")
        st.session_state['quiz_complete'] = True
        st.session_state['quiz_started'] = False
        st.rerun()

# --- Quiz Summary Display and Saving ---
if st.session_state['quiz_complete']:
    st.header("🏁 Quiz Results")

    total_attempted = st.session_state['total_attempted']
    score = st.session_state['score']
    percentage = (score / total_attempted * 100) if total_attempted > 0 else 0
    end_time = time.time()
    start_time = st.session_state.get('quiz_start_time', end_time)
    duration_seconds = max(0, int(end_time - start_time))

    st.metric("Total Questions Attempted", total_attempted)
    st.metric("Correct Answers", score)
    st.metric("Score", f"{percentage:.2f}%")
    st.metric("Time Taken", f"{duration_seconds // 60}m {duration_seconds % 60}s")

    st.subheader("Attempt Details:")
    if st.session_state['user_answers_details']:
        df = pd.DataFrame(st.session_state['user_answers_details'])
        cols_order = ['question_number', 'user_answer', 'correct_answer', 'result', 'question_text_summary']
        available_cols = [col for col in cols_order if col in df.columns]
        df = df[available_cols]
        if 'question_number' in df.columns:
            df['question_number'] = pd.to_numeric(df['question_number'], errors='coerce')
            df = df.sort_values(by='question_number').reset_index(drop=True)

        def highlight_results(s):
            return ['color: green' if v == 'Correct' else ('color: red' if v == 'Incorrect' else '') for v in s]
        style_args = {}
        if 'result' in df.columns: style_args['subset'] = ['result']
        st.dataframe(df.style.apply(highlight_results, **style_args), use_container_width=True, hide_index=True)

        # --- Save Session (Only if not already saved for this completion) ---
        # Check the flag!
        if not st.session_state.get('session_saved', False):
             if total_attempted > 0:
                 session_id = str(uuid.uuid4())
                 current_quiz_name = st.session_state.get('selected_quiz_file', 'Unknown Quiz')
                 current_attempt_number = 0
                 try:
                     cosmos_container = get_cosmos_client()
                     if cosmos_container:
                         previous_attempts_count = get_attempt_count_for_quiz(cosmos_container, current_quiz_name)
                         current_attempt_number = previous_attempts_count + 1
                     else: st.warning("Could not determine attempt number because DB connection failed.")
                 except Exception as e:
                     st.warning(f"Could not determine attempt number. Setting to 0. Error: {e}")
                     current_attempt_number = 0

                 session_data = {
                     'id': session_id, 'partitionKey': current_quiz_name,
                     'quiz_set_name': current_quiz_name, 'attempt_number': current_attempt_number,
                     'session_datetime_utc': datetime.datetime.utcnow().isoformat() + "Z",
                     'total_questions_in_set': st.session_state.get('total_questions_in_set', 0),
                     'questions_attempted': total_attempted, 'questions_correct': score,
                     'correct_percentage': round(percentage, 2), 'duration_seconds': duration_seconds,
                     'attempt_details': st.session_state.get('user_answers_details', [])
                 }

                 # Save and set the flag
                 if save_session_to_db(session_data):
                      st.session_state['session_saved'] = True # <<< SET FLAG AFTER SUCCESSFUL SAVE
                      st.rerun() # Rerun to lock in state
                 # If saving failed, session_saved remains False, allowing retry on next rerun if applicable
             else:
                 st.info("No questions were attempted, session not saved.")
                 # Set flag even if not saved, to prevent repeated checks/messages
                 st.session_state['session_saved'] = True
                 st.rerun()
        # --- End of Saving Block ---

    else:
        st.write("No questions were attempted in this session.")

    # --- Display "Take Another Quiz" Button (Always show if complete) ---
    if st.button("🔄 Take Another Quiz"):
        # Reset state for a completely new run
        st.session_state['current_question_index'] = 0
        st.session_state['score'] = 0
        st.session_state['total_attempted'] = 0
        st.session_state['answer_submitted'] = False
        st.session_state['questions'] = []
        st.session_state['user_answers_details'] = []
        st.session_state['quiz_start_time'] = None
        # st.session_state['selected_quiz_file'] = None # Optional: force re-selection
        st.session_state['total_questions_in_set'] = 0
        st.session_state['quiz_complete'] = False
        st.session_state['quiz_started'] = False
        st.session_state['session_saved'] = False # <<< RESET SAVE FLAG HERE
        st.rerun()

# Initial state message
elif not st.session_state['quiz_started'] and not st.session_state['quiz_complete']:
     st.info("Select a quiz set from the sidebar and click 'Start Quiz' to begin.")

# Safety check for failed loading
elif st.session_state['quiz_started'] and not st.session_state['questions']:
    st.error("Quiz cannot proceed as no questions were loaded.")
    st.session_state['quiz_started'] = False
    if st.button("Go Back"): st.rerun()