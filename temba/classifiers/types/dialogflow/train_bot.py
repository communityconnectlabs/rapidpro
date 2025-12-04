import base64
import logging
import pickle
from typing import Any, Union

import pandas as pd
from google.api_core import exceptions
from google.cloud import dialogflow_v2
from google.cloud.dialogflow_v2.services.intents import pagers

from temba.utils.languages import alpha2_to_alpha3

logger = logging.getLogger(__name__)


class TrainingClient:
    def __init__(self, training_data: list, languages: list, credential: dict, messages: dict = None) -> None:
        self.credential = credential
        self.training_data = training_data
        self.languages = languages
        self.intents_requests = []

        if messages and len(messages) > 0:
            self.messages = messages
        else:
            fill_list = [[]] * len(languages)
            fill_zero = [0] * len(languages)
            errors_per_lang = dict(zip(languages, fill_list))
            count_per_lang = dict(zip(languages, fill_zero))
            self.messages = dict(
                errors=[],
                update_errors=errors_per_lang.copy(),
                create_errors=errors_per_lang.copy(),
                created=count_per_lang.copy(),
                updated=count_per_lang.copy(),
            )

    def get_intents(self, language_code: str) -> pagers.ListIntentsPager:
        client = dialogflow_v2.IntentsClient.from_service_account_info(self.credential)
        parent = dialogflow_v2.AgentsClient.agent_path(self.credential["project_id"])
        request = dialogflow_v2.ListIntentsRequest(parent=parent, language_code=language_code)
        intents = client.list_intents(request=request)
        return intents

    @classmethod
    def intent_message(cls, message_texts):
        text = dialogflow_v2.types.Intent.Message.Text(text=[message_texts])
        return dialogflow_v2.types.Intent.Message(text=text)

    @classmethod
    def get_training_phrase_from_text(cls, training_phrases_part):
        part = dialogflow_v2.types.Intent.TrainingPhrase.Part(text=training_phrases_part)
        return dialogflow_v2.types.Intent.TrainingPhrase(parts=[part])

    def get_training_phases_from_text_list(self, training_phrases_list: list) -> list:
        phrases = []
        for training_phrases_part in training_phrases_list:
            phrases.append(self.get_training_phrase_from_text(training_phrases_part))
        return phrases

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

    @classmethod
    def clean_training_phrases(cls, data: Any) -> Union[list, None]:
        if data and isinstance(data, str) and len(data) > 0:
            data_list = data.rstrip("]").lstrip("[").split(",")
            return [element.strip().strip("'") for element in data_list]
        logger.info(f"can not use training data provided ({data})")
        return None

    def extract_intents_from_data(self, intent_dict: dict, language_code: str):
        df = pd.DataFrame(self.training_data)
        lang_headers = self.get_language_headers(language_code)
        name_header = lang_headers["intent"]
        training_header = lang_headers["training_phrase"]
        answer_header = lang_headers["answer"]

        for index, intent in df.iterrows():
            training_phrases = self.clean_training_phrases(intent[training_header])

            if not training_phrases or len(training_phrases) == 0:
                error_msg = f"No training phrases found, skipping row {index + 1}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue

            training_phrases = self.get_training_phases_from_text_list(training_phrases)
            message = self.intent_message(intent[answer_header])
            if not message:
                error_msg = f"No intent answer found, skipping row {index + 1}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue

            intent_name = intent[name_header]
            if not intent_name or len(intent_name) == 0:
                error_msg = f"No intent found here, skipping row {index + 1}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue

            if intent_dict.get(intent_name) is None:
                intent_details = dialogflow_v2.types.Intent(
                    display_name=intent_name,
                    training_phrases=training_phrases,
                    messages=[message],
                )
                self.intents_requests.append(dict(type="create", intent=intent_details, language=language_code))
            else:
                intent_details = intent_dict[intent_name]
                intent_details.training_phrases = training_phrases
                intent_details.messages = [message]
                self.intents_requests.append(dict(type="update", intent=intent_details, language=language_code))

    def process_sync_intents_for_lang(self, language_code):
        intents = self.get_intents(language_code)
        intent_dict = dict()

        logger.info("transforming existing intents ...")
        for row in intents:
            intent_dict[row.display_name] = row

        self.extract_intents_from_data(intent_dict, language_code)

    def push_to_dialogflow(self, intents, start_index=0):
        index = start_index
        retry = False
        completed = False
        intent_list = intents[start_index:]

        if len(intent_list) > 0:
            client = dialogflow_v2.IntentsClient.from_service_account_info(self.credential)
            try:
                for intent_detail in intent_list:
                    language_code = intent_detail["language"]
                    intent = intent_detail["intent"]
                    if intent_detail["type"] == "create":
                        self.create_intent(intent, language_code, client)
                        self.messages["created"][language_code] += 1
                    else:
                        self.update_intent(intent, language_code, client)
                        self.messages["updated"][language_code] += 1

                    index += 1
                completed = True
            except exceptions.ResourceExhausted:
                logger.error("Resource Exhausted error")
                retry = True
            except Exception as e:
                logger.error(e, exc_info=True)
                self.messages["errors"].append(str(e))
        else:
            completed = True

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
