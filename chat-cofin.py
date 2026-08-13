import datetime
import re

import streamlit as st
from mistralai.client import Mistral

from htbuilder.units import rem
from htbuilder import div, styles

# -----------------------------------------------------------------------------
# Configure Streamlit

st.set_page_config(
    page_title="Chatbot lifelong-learning.lu", 
    page_icon="🤖",
    initial_sidebar_state="collapsed"
)

# -----------------------------------------------------------------------------
# CONSTANTS

MODEL = "mistral-small-latest"

SUGGESTIONS_FR = {
    "q1": (
        ":blue[:material/local_library:] Quels salariés sont éligibles au cofinancement ?"
    ),
    "q2": (
        ":green[:material/database:] Les frais de déplacement sont-ils éligibles ?"
    ),
    "q3": (
        ":orange[:material/multiline_chart:] Où trouver les informations sur la masse salariale/ ?"
    ),
    "q4": (
        ":violet[:material/apparel:] Quelles pièces justificatives sont obligatoires ?"
    ),
    "q5": (
        ":red[:material/deployed_code:] C'est quoi l'adapatation au poste de travail ?"
    ),
}

SUGGESTIONS_EN = {
    "q1": (
        ":blue[:material/local_library:] Which employees are eligible for co-financing?"
    ),
    "q2": (
        ":green[:material/database:] Are travel expenses eligible?"
    ),
    "q3": (
        ":orange[:material/multiline_chart:] Where can I find information on payroll?"
    ),
    "q4": (
        ":violet[:material/apparel:] Which supporting documents are mandatory?"
    ),
    "q5": (
        ":red[:material/deployed_code:] What is workplace adaptation?"
    ),
}

# -----------------------------------------------------------------------------
# Translations
translations = {
    "fr": {
        "title": "Discutez avec un expert en cofinancement", 
        "ask": "Posez votre question",
        "new": "Nouveau Chat",
        "loading": "Veuillez patienter...",
        "searching": "Je recherche dans mes connaissances...",
        "download": "Télécharger le Chat",
        "disclaimer": "&nbsp;:small[:gray[:material/balance: Avertissement sur l'IA]]",
        "warning": "Avertissement",
        "language": "Choix de la langue"
    },
    "en": {
        "title": "Ask a cofunding expert", 
        "ask": "Ask your question",
        "new": "New Chat",
        "loading": "Please wait...",
        "searching": "Searching in my knowledge base...",
        "download": "Download Chat",        
        "disclaimer": "&nbsp;:small[:gray[:material/balance: AI Disclaimer]]",
        "warning": "Warning",
        "language": "Language choice",     
    }
}
if "lang" not in st.session_state:
    st.session_state.lang = "fr" 


suggestions = {}

# -----------------------------------------------------------------------------
# Create a new MISTRAL client

mistral_api_key = st.secrets["MISTRAL_API_KEY"]

client = Mistral(api_key=mistral_api_key)

# List all libraries in workspace for debug
libraries_list = client.beta.libraries.list()

for library in libraries_list.data:
    print(f"Nom: {library.name} | ID: {library.id} ({library.nb_documents} documents)")

# -----------------------------------------------------------------------------
# Show a disclaimer popup

@st.dialog(" ")
def show_disclaimer_dialog():
    if st.session_state.lang=="fr":
        st.caption("""
                # ⚠️ Avertissement sur l'IA
                Ce chatbot est propulsé par un modèle de langage basé 
                sur l'intelligence artificielle (IA). 
                Il génère des réponses automatiquement à partir d’un vaste 
                corpus de textes et d’exemples préalablement intégrés lors 
                de son entraînement. 
                Bien que conçu pour fournir des informations utiles, ce 
                système ne garantit pas l’exactitude, l’exhaustivité ni 
                l’actualité des réponses fournies.
                \n\n
                L'INFPC décline toute responsabilité quant à l’usage qui 
                pourrait être fait des réponses fournies. Il vous incombe 
                de vérifier, croiser ou faire valider toute information 
                critique par des sources fiables ou des experts compétents.
                \n\n
                Pour plus d'information, visitez notre site
                https://www.infpc.lu
            """)
    if st.session_state.lang=="en":
        st.caption("""
                # AI Disclaimer
                This chatbot is powered by an artificial intelligence (AI)-based language model. 
                It automatically generates responses based on a vast corpus of texts and examples 
                incorporated during its training. 
                While designed to provide useful information, this system does not guarantee 
                the accuracy, completeness, or currency of the responses provided.
                \n\n
                The INFPC accepts no liability for how the provided responses are used. 
                It is your responsibility to verify, cross-check, or have any critical 
                information validated by reliable sources or qualified experts.
                \n\n
                For more information, visit our website:
                https://www.infpc.lu
            """)

# ----------------------------------------------------------------------------
# Remove Streamlit Markdown
def remove_streamlit_markdown(text: str) -> str:

    # Supprime les icônes Material : :material/xxx:
    text = re.sub(r":material/[^:]+:", "", text)

    # Supprime les couleurs Streamlit : :blue[...], :red[...], etc.
    text = re.sub(
        r":(?:red|blue|green|orange|yellow|violet|gray|grey)\[([^\]]*)\]",
        r"\1",
        text,
    )

    return text.strip()

# -----------------------------------------------------------------------------
# Clear the current conversation
def clear_conversation():
    st.session_state.messages = []
    st.session_state.initial_question = None
    st.session_state.selected_suggestion = None
    st.session_state.conversation_export_text = ""

def translate_suggestions():
    global suggestions

    if st.session_state.lang=="fr":
        suggestions = SUGGESTIONS_FR.copy()

    if st.session_state.lang=="en":
        suggestions = SUGGESTIONS_EN.copy()

# -----------------------------------------------------------------------------
# Export the current conversation into a string

def build_conversation_text():
    messages = st.session_state.get("messages", [])
    lines = []

    for message in messages:
        role = message.get("role", "")
        content = (message.get("content") or "").strip()

        if not content:
            continue

        if role == "user":
            lines.append(f"Question: {content}")
        elif role == "assistant":
            lines.append(f"Réponse: {content}")
        else:
            lines.append(f"{role.capitalize()}: {content}")

    return "\n\n".join(lines)


def refresh_conversation_export():
    st.session_state.conversation_export_text = build_conversation_text()


# -----------------------------------------------------------------------------
# Draw the UI

if "conversation_export_text" not in st.session_state:
    st.session_state.conversation_export_text = ""

sidebar = st.sidebar

with sidebar:

    st.image("./images/infpc.svg", width=200)

    selected_lang = st.sidebar.selectbox(" ", 
        options=["fr", "en"],
        format_func=lambda x: {"en": "English", "fr": "Français"}[x]
    )
    st.session_state.lang = selected_lang

    translate_suggestions()
    t = translations[st.session_state.lang]

    st.button(
        t["new"],
        icon=":material/add_circle:",
        on_click=clear_conversation,
        width="stretch",
    ) 


st.image("./images/chatlib.svg", width=128, link="https://www.infpc.lu")

st.title(
    t["title"],
    anchor=False
)

user_just_asked_initial_question = (
    "initial_question" in st.session_state and st.session_state.initial_question
)

user_just_clicked_suggestion = (
    "selected_suggestion" in st.session_state and st.session_state.selected_suggestion
)

user_first_interaction = (
    user_just_asked_initial_question or user_just_clicked_suggestion
)

has_message_history = (
    "messages" in st.session_state and len(st.session_state.messages) > 0
)

# Show a different home page if no question has been asked
if not user_first_interaction and not has_message_history:
    st.session_state.messages = []

    with st.container():
        st.chat_input(t["ask"], key="initial_question")

        selected_suggestion = st.pills(
            label="Exemples",
            label_visibility="collapsed",
            options=suggestions.keys(),
            format_func=lambda x: suggestions[x],
            key="selected_suggestion",
        )

    st.button(
        t["disclaimer"],
        type="tertiary",
        on_click=show_disclaimer_dialog,
    )
    st.stop()

# Show chat input at the bottom when a question has been asked.
user_message = st.chat_input(t["ask"])

if not user_message:
    if user_just_asked_initial_question:
        user_message = st.session_state.initial_question
    if user_just_clicked_suggestion:
        user_message = remove_streamlit_markdown(suggestions[st.session_state.selected_suggestion])

if "prev_question_timestamp" not in st.session_state:
    st.session_state.prev_question_timestamp = datetime.datetime.fromtimestamp(0)

# Display chat messages from history as speech bubbles.
for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            st.container()  # Fix ghost message bug.

        st.markdown(message["content"])

if user_message:
    # When the user posts a message...
    st.session_state.messages.append(
        {"role": "user", "content": user_message}
    )

    # Display message as a speech bubble.
    with st.chat_message("user"):
        st.text(user_message)

    refresh_conversation_export()

    # Display assistant response as a speech bubble.
    with st.chat_message("assistant"):
        # Create and execute agent
        with st.spinner(t["searching"]):
            agent = client.beta.agents.create(
                model="mistral-large-latest",
                name="Expert Cofinancement",
                instructions="Réponds aux questions en te basant uniquement sur la librairie fournie.",
                tools=[
                    {
                    "type": "document_library",
                    "library_ids": ["019fa84f-323a-7250-b211-ab0283ec1362"]
                    }
                ],
            )
            print(f"Agent configuré avec succès ! ID de l'agent : {agent.id}")

            try:
                response = client.agents.complete(
                    agent_id=agent.id,
                    # Send only the last question   
                    messages=[
                        {
                            "role": "user", 
                            "content": user_message
                        }
                    ],     
                    # Send all previous questions               
                    #messages=[m for m in st.session_state.messages if m.get("role") == "user"],
                )

                # 4. Afficher la réponse générée à partir de votre librairie
                # 4. Parcourir les messages pour extraire le contenu textuel final
                # L'index 2 contient généralement la réponse textuelle générée après la recherche
                final_message = response.choices[0].messages[-1]

                if final_message.content:
                # Reconstruire le texte à partir des TextChunks
                    full_text = ""
                    for chunk in final_message.content:
                        # On ne prend que les morceaux de type texte pur
                        if chunk.type == "text":
                            full_text += chunk.text
                else:
                    print("L'agent n'a pas renvoyé de texte brut.")
            finally:
                client.beta.agents.delete(agent_id=agent.id)
                print(f"L'agent {agent.id} a été supprimé avec succès.")	

        # Put everything after the spinners
        with st.container():
            # Stream the LLM response.
            st.write(full_text)

            # Add messages to chat history.
            st.session_state.messages.append({"role": "assistant", "content": full_text})
            refresh_conversation_export()

            st.button(
                t["new"],
                icon=":material/refresh:",
                on_click=clear_conversation,
            )


with sidebar:
    st.download_button(
        label=t["download"],
        data=st.session_state.conversation_export_text,
        file_name=f"chat_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt",
        mime="text/plain",
        disabled=not st.session_state.conversation_export_text,
        width="stretch",
    )