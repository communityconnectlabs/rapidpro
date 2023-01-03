import csv
import logging

from google.cloud import dialogflow_v2


logger = logging.getLogger(__name__)


class TrainingClient:
    def __init__(self, csv_data: list, languages: list, credential: dict):
        self.credential = credential
        self.csv_data = csv_data
        self.languages = languages
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

    def csv_to_dict(self):
        header_list = self.csv_data.pop(0)
        header_list = header_list.split(",")
        header = [str(column).lower() for column in header_list]
        reader = csv.DictReader(self.csv_data, fieldnames=header)

        return reader

    @classmethod
    def get_intents_from_dict(cls, dict_list):
        intents = []
        for intent_dict in dict_list:
            intent = intent_dict["Intents"]
            intents.append(intent)
        return intents

    def get_intents(self, language_code):
        client = dialogflow_v2.IntentsClient.from_service_account_info(self.credential)
        parent = dialogflow_v2.AgentsClient.agent_path(self.credential["project_id"])
        request = dialogflow_v2.ListIntentsRequest(parent=parent, language_code=language_code)
        intents = client.list_intents(request=request)
        return intents

    @classmethod
    def get_training_phase_from_text(cls, training_phrases_part):
        part = dialogflow_v2.types.Intent.TrainingPhrase.Part(text=training_phrases_part)
        return dialogflow_v2.types.Intent.TrainingPhrase(parts=[part])

    @classmethod
    def intent_message(cls, message_texts):
        text = dialogflow_v2.types.Intent.Message.Text(text=[message_texts])
        return dialogflow_v2.types.Intent.Message(text=text)

    def get_training_phases_from_text_list(self, training_phrases_list):
        phrases = []
        for training_phrases_part in training_phrases_list:
            phrases.append(self.get_training_phase_from_text(training_phrases_part))
        return phrases

    @classmethod
    def get_language_headers(cls, language):
        substitutes = dict(en="eng", es="spa")
        substitute = substitutes.get(language, language)
        training_phrase = f"question{substitute}"
        answer = f"answer{substitute}"

        return dict(training_phrase=training_phrase, answer=answer, intent="intent")

    def create_new_intents(self, to_be_created, language_code, client):
        if len(to_be_created) > 0:
            logger.info("Creating intents")
            for intent in to_be_created:
                try:
                    request = dialogflow_v2.CreateIntentRequest(
                        parent=dialogflow_v2.AgentsClient.agent_path(self.credential["project_id"]),
                        intent=intent,
                        language_code=language_code,
                    )
                    client.create_intent(request=request)
                    self.messages["created"][language_code] += 1
                except Exception as e:
                    logger.error(f"{str(e)} {intent.display_name}", exc_info=True)
                    self.messages["create_errors"][language_code].append(str(e))

            logger.info("Intents created")

    def update_existing_intents(self, to_be_updated, language_code, client):
        if len(to_be_updated) > 0:
            logger.info("Updating intents")
            for intent in to_be_updated:
                try:
                    request = dialogflow_v2.UpdateIntentRequest(
                        intent=intent,
                        intent_view=dialogflow_v2.IntentView.INTENT_VIEW_FULL,
                        language_code=language_code,
                    )
                    client.update_intent(request=request)
                    self.messages["updated"][language_code] += 1
                except Exception as e:
                    logger.error(f"{str(e)}, {intent.display_name}", exc_info=True)
                    self.messages["update_errors"][language_code].append(str(e))

            logger.info("Intents updated")

    @classmethod
    def clean_training_phrases(cls, data):
        if data:
            data_list = data.rstrip("]").lstrip("[").split(",")
            return [element.strip().strip("'") for element in data_list]
        return data

    def extract_intents_from_csv(self, csv_data, lang_headers, intent_dict):
        to_be_created = []
        to_be_updated = []
        counter = 0
        for intent in csv_data:
            counter += 1  # count rows regardless of skipping
            training_key = lang_headers["training_phrase"]
            training_phrases = intent.get(training_key)
            training_phrases = self.clean_training_phrases(training_phrases)

            if not training_phrases or len(training_phrases) == 0:
                error_msg = f"No training phrases found here, skipping row {counter}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue
            training_phrases = self.get_training_phases_from_text_list(training_phrases)
            message = self.intent_message(intent[lang_headers["answer"]])

            if not message:
                error_msg = f"No intent answer found, skipping row {counter}"
                logger.warning(error_msg)
                self.messages["errors"].append(error_msg)
                continue

            try:
                intent_name = intent[lang_headers["intent"]]
            except KeyError:
                intent_name = intent["intents"]

            if len(intent_name) == 0:
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

                to_be_created.append(intent_details)
            else:
                intent_details = intent_dict[intent_name]
                intent_details.training_phrases = training_phrases
                intent_details.messages = [message]
                to_be_updated.append(intent_details)

        return to_be_created, to_be_updated

    def process_sync_intents_for_lang(self, language_code):
        csv_data = self.csv_to_dict()
        lang_headers = self.get_language_headers(language_code)

        intents = self.get_intents(language_code)
        intent_dict = dict()

        logger.info("transforming existing intents ...")
        for row in intents:
            intent_dict[row.display_name] = row

        to_be_created, to_be_updated = self.extract_intents_from_csv(csv_data, lang_headers, intent_dict)

        client = dialogflow_v2.IntentsClient.from_service_account_info(self.credential)
        self.create_new_intents(to_be_created, language_code, client)
        self.update_existing_intents(to_be_updated, language_code, client)

    def train_bot(self):
        for lang in self.languages:
            try:
                self.process_sync_intents_for_lang(lang)
            except Exception as e:
                self.messages["errors"].append(str(e))
