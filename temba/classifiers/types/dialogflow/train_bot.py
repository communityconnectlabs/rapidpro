import base64
import csv
import logging
import pickle
from typing import Any, Union

from google.api_core import exceptions
from google.cloud import dialogflow_v2
from google.cloud.dialogflow_v2.services.intents import pagers

from temba.utils.languages import alpha2_to_alpha3

logger = logging.getLogger(__name__)


class TrainingClient:
    def __init__(self, csv_data: list, languages: list, credential: dict, messages: dict = None) -> None:
        self.credential = credential
        self.csv_data = csv_data
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

    def merge_intents(self, csv_reader: csv.DictReader, language_code: str) -> list:
        lang_headers = self.get_language_headers(language_code)
        grouped_by_intents = {}
        for row in csv_reader:
            intent_header = lang_headers["intent"]
            question_header = lang_headers["training_phrase"]
            answer_header = lang_headers["answer"]
            intent_name = row.get(intent_header)
            if not intent_name:
                intent_name = row.get("intents")

            training_phrases = self.clean_training_phrases(row.get(question_header))
            answers = row.get(answer_header)

            intent_row = grouped_by_intents.get(intent_name)
            if intent_row:
                questions = intent_row.get(question_header)
                if questions and training_phrases:
                    intent_row[question_header] = training_phrases + questions
            else:
                grouped_by_intents[intent_name] = {
                    "intent": intent_name,
                    question_header: training_phrases,
                    answer_header: answers,
                }

        return list(grouped_by_intents.values())

    def csv_to_dict(self, language_code: str) -> list:
        csv_data = self.csv_data.copy()
        header_list = csv_data.pop(0)
        header_list = header_list.split(",")
        header = [str(column).lower() for column in header_list]
        reader = csv.DictReader(csv_data, fieldnames=header)

        return self.merge_intents(reader, language_code)

    @classmethod
    def get_intents_from_dict(cls, dict_list: list) -> list:
        intents = []
        for intent_dict in dict_list:
            intent = intent_dict["Intents"]
            intents.append(intent)
        return intents

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

    def extract_intents_from_csv(self, csv_data: list, lang_headers: dict, intent_dict: dict, language_code: str):
        counter = 0
        for intent in csv_data:
            counter += 1  # count rows regardless of skipping
            training_key = lang_headers["training_phrase"]
            training_phrases = intent.get(training_key)

            if not training_phrases or len(training_phrases) == 0:
                error_msg = f"No training phrases found, skipping row {counter}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue

            training_phrases = self.get_training_phases_from_text_list(training_phrases)
            answer_header = lang_headers["answer"]
            message = self.intent_message(intent[answer_header])

            if not message:
                error_msg = f"No intent answer found, skipping row {counter}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue

            intent_name = intent[lang_headers["intent"]]

            if not intent_name or len(intent_name) == 0:
                error_msg = f"No intent found here, skipping row {counter}"
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
        csv_data = self.csv_to_dict(language_code)
        lang_headers = self.get_language_headers(language_code)

        intents = self.get_intents(language_code)
        intent_dict = dict()

        logger.info("transforming existing intents ...")
        for row in intents:
            intent_dict[row.display_name] = row

        self.extract_intents_from_csv(csv_data, lang_headers, intent_dict, language_code)

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
