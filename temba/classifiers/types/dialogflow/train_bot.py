import base64
import logging
import pickle
import time
from typing import Any, Union

import pandas as pd
from google.api_core import exceptions
from google.cloud import dialogflow_v2
from google.cloud.dialogflow_v2.services.intents import pagers

from temba.utils.languages import alpha2_to_alpha3

logger = logging.getLogger(__name__)


class TrainingClient:
    """Client for training Dialogflow intents from Excel/CSV data.

    Handles:
    - Grouping intents by name
    - Consolidating training phrases
    - Creating/updating intents in Dialogflow
    - Rate limiting and retry logic
    """

    def __init__(self, training_data: dict, languages: list, credential: dict, messages: dict = None) -> None:
        """Initialize training client.

        Args:
            training_data: Dictionary with column names as keys and lists as values
            languages: List of language codes
            credential: Dialogflow service account credentials
            messages: Optional existing messages dict for tracking
        """
        self.credential = credential
        self.training_data = training_data
        self.languages = languages
        self.intents_requests = []

        if messages:
            self.messages = messages
        else:
            errors_per_lang = {lang: [] for lang in languages}
            count_per_lang = {lang: 0 for lang in languages}
            self.messages = {
                "errors": [],
                "update_errors": errors_per_lang.copy(),
                "create_errors": errors_per_lang.copy(),
                "created": count_per_lang.copy(),
                "updated": count_per_lang.copy(),
            }

    def get_intents(self, language_code: str) -> pagers.ListIntentsPager:
        client = dialogflow_v2.IntentsClient.from_service_account_info(self.credential)
        parent = dialogflow_v2.AgentsClient.agent_path(self.credential["project_id"])
        request = dialogflow_v2.ListIntentsRequest(parent=parent, language_code=language_code)
        intents = client.list_intents(request=request)
        return intents

    @staticmethod
    def intent_message(message_texts: str) -> dialogflow_v2.types.Intent.Message:
        """Create a Dialogflow intent message from text."""
        text = dialogflow_v2.types.Intent.Message.Text(text=[message_texts])
        return dialogflow_v2.types.Intent.Message(text=text)

    @staticmethod
    def get_training_phrase_from_text(text: str) -> dialogflow_v2.types.Intent.TrainingPhrase:
        """Create a Dialogflow training phrase from text."""
        part = dialogflow_v2.types.Intent.TrainingPhrase.Part(text=text)
        return dialogflow_v2.types.Intent.TrainingPhrase(parts=[part])

    def get_training_phases_from_text_list(self, training_phrases_list: list) -> list:
        """Convert list of text phrases to Dialogflow training phrases."""
        return [self.get_training_phrase_from_text(phrase) for phrase in training_phrases_list]

    @classmethod
    def get_language_headers(cls, language):
        language_code = str(alpha2_to_alpha3(language))
        if language_code == "zho":  # Chinese traditional ISO code-3 is replaced by CHI
            language_code = "chi"
        training_phrase = f"question{language_code}"
        answer = f"answer{language_code}"

        return dict(training_phrase=training_phrase, answer=answer, intent="intent")

    def create_intent(self, intent, language_code, client):
        request = dialogflow_v2.CreateIntentRequest(
            parent=dialogflow_v2.AgentsClient.agent_path(self.credential["project_id"]),
            intent=intent,
            language_code=language_code,
        )
        client.create_intent(request=request)

    @classmethod
    def update_intent(cls, intent, language_code, client):
        request = dialogflow_v2.UpdateIntentRequest(
            intent=intent,
            intent_view=dialogflow_v2.IntentView.INTENT_VIEW_FULL,
            language_code=language_code,
        )
        client.update_intent(request=request)

    @staticmethod
    def clean_training_phrases(data: Any) -> Union[list, None]:
        """Parse training phrases from string or list format."""
        if pd.isna(data) or data is None:
            return None

        # Convert to string if not already
        data_str = str(data).strip()
        if not data_str:
            return None

        # Parse string representation of list: "['phrase1', 'phrase2']"
        data_list = data_str.rstrip("]").lstrip("[").split(",")
        phrases = [phrase.strip().strip("'").strip('"') for phrase in data_list]
        return [p for p in phrases if p]  # Filter out empty strings

    @staticmethod
    def validate_field(value: Any) -> Union[str, None]:
        """Validate and normalize a field value."""
        if pd.isna(value) or (isinstance(value, str) and not value.strip()):
            return None
        return str(value).strip()

    def extract_intents_from_data(self, intent_dict: dict, language_code: str):
        """Extract and group intents from training data.

        Groups multiple rows with the same intent name and consolidates their
        training phrases and answers into a single intent.

        Args:
            intent_dict: Dictionary of existing intents from Dialogflow
            language_code: Language code for headers
        """
        df = pd.DataFrame(self.training_data)
        lang_headers = self.get_language_headers(language_code)
        name_header = lang_headers["intent"]
        training_header = lang_headers["training_phrase"]
        answer_header = lang_headers["answer"]

        # Validate required columns exist
        missing_columns = [col for col in [name_header, training_header, answer_header] if col not in df.columns]
        if missing_columns:
            error_msg = f"Missing required columns: {', '.join(missing_columns)}"
            logger.error(error_msg)
            self.messages["errors"].append(error_msg)
            return

        # Group rows by intent name and consolidate training phrases and answers
        grouped_intents = {}

        for index, row in df.iterrows():
            # Validate intent name
            intent_name = self.validate_field(row[name_header])
            if not intent_name:
                self.messages["errors"].append(f"No intent name found, skipping row {index + 1}")
                continue

            # Validate training phrases
            training_phrases = self.clean_training_phrases(row[training_header])
            if not training_phrases:
                self.messages["errors"].append(f"No training phrases found, skipping row {index + 1}")
                continue

            # Validate answer field
            answer_text = self.validate_field(row[answer_header])
            if not answer_text:
                self.messages["errors"].append(f"No answer found, skipping row {index + 1}")
                continue

            # Group by intent name
            if intent_name not in grouped_intents:
                grouped_intents[intent_name] = {"training_phrases": [], "answers": []}

            # Add training phrases and answers to the group
            grouped_intents[intent_name]["training_phrases"].extend(training_phrases)
            grouped_intents[intent_name]["answers"].append(answer_text)

        # Now create or update intents with all consolidated data
        for intent_name, data in grouped_intents.items():
            # Convert training phrases to Dialogflow format
            all_training_phrases = self.get_training_phases_from_text_list(data["training_phrases"])

            # Use the first answer as the primary message
            answer_text = data["answers"][0]
            message = self.intent_message(answer_text)

            # Check if intent exists in Dialogflow
            existing_intent = intent_dict.get(intent_name)

            if existing_intent is None:
                logger.info(f"Creating new intent: {intent_name}")
                intent_details = dialogflow_v2.types.Intent(
                    display_name=intent_name,
                    training_phrases=all_training_phrases,
                    messages=[message],
                )
                self.intents_requests.append(dict(type="create", intent=intent_details, language=language_code))
            else:
                logger.info(f"Updating existing intent: {intent_name}")
                intent_details = existing_intent
                intent_details.training_phrases = all_training_phrases
                intent_details.messages = [message]
                self.intents_requests.append(dict(type="update", intent=intent_details, language=language_code))

    def process_sync_intents_for_lang(self, language_code):
        intents = self.get_intents(language_code)
        intent_dict = dict()

        logger.info("transforming existing intents ...")
        for row in intents:
            # Normalize intent name to string and strip whitespace for consistent lookup
            normalized_name = str(row.display_name).strip()
            intent_dict[normalized_name] = row

        logger.info(f"Loaded {len(intent_dict)} existing intents for language {language_code}")
        if len(intent_dict) > 0:
            logger.info(f"Sample intent names: {list(intent_dict.keys())[:5]}")

        self.extract_intents_from_data(intent_dict, language_code)

    def _execute_intent_operation(self, intent_detail: dict, client, max_retries: int = 3) -> bool:
        """Execute a single intent operation with retry logic.

        Returns:
            bool: True if successful, False if should skip
        """
        language_code = intent_detail["language"]
        intent = intent_detail["intent"]
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                if intent_detail["type"] == "create":
                    self.create_intent(intent, language_code, client)
                    self.messages["created"][language_code] += 1
                else:
                    self.update_intent(intent, language_code, client)
                    self.messages["updated"][language_code] += 1

                time.sleep(0.5)  # Rate limiting delay
                return True

            except exceptions.ResourceExhausted:
                if attempt < max_retries - 1:
                    logger.warning(f"Rate limit hit, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    raise

            except exceptions.InvalidArgument as e:
                if "already exists" in str(e) and intent_detail["type"] == "create":
                    logger.warning(f"Intent '{intent.display_name}' already exists, skipping")
                    self.messages["errors"].append(f"Intent '{intent.display_name}' already exists")
                    return False
                raise

        return False

    def push_to_dialogflow(self, intents, start_index=0):
        """Push intents to Dialogflow with rate limiting and error handling."""
        index = start_index
        retry = False
        completed = False
        intent_list = intents[start_index:]

        if not intent_list:
            return index, retry, True

        client = dialogflow_v2.IntentsClient.from_service_account_info(self.credential)

        try:
            for intent_detail in intent_list:
                self._execute_intent_operation(intent_detail, client)
                index += 1
            completed = True

        except exceptions.ResourceExhausted:
            logger.error("Resource Exhausted - will retry task")
            retry = True
        except Exception as e:
            logger.error(e, exc_info=True)
            self.messages["errors"].append(str(e))

        return index, retry, completed

    def intent_list_to_str(self):
        return base64.b64encode(pickle.dumps(self.intents_requests)).decode()

    @classmethod
    def intent_str_to_list(cls, pickled_doc):
        value = pickled_doc.encode()  # encode str to bytes
        value = base64.b64decode(value)
        return pickle.loads(value)

    def build_intent_list(self):
        for lang in self.languages:
            try:
                self.process_sync_intents_for_lang(lang)
            except Exception as e:
                self.messages["errors"].append(str(e))
                logger.error(e, exc_info=True)
