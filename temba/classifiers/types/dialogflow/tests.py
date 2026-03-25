import json
from unittest.mock import MagicMock, patch

from google.api_core import exceptions as google_exceptions
from google.cloud import dialogflow_v2

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.tests import TembaTest

from .client import Client
from .train_bot import TrainingClient
from .type import DialogflowType

CREDENTIALS = {
    "project_id": "test-project",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIItest\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "test@test-project.iam.gserviceaccount.com",
    "type": "service_account",
}


class ClientTest(TembaTest):
    @patch("temba.classifiers.types.dialogflow.client.dialogflow_v2.AgentsClient.from_service_account_info")
    def test_get_agent(self, mock_from_info):
        client = Client(CREDENTIALS)
        self.assertEqual(client.project_id, "test-project")

        mock_agent_client = MagicMock()
        mock_from_info.return_value = mock_agent_client

        mock_agent = MagicMock()
        mock_agent.display_name = "Test Agent"
        mock_agent_client.get_agent.return_value = mock_agent

        agent = client.get_agent()
        self.assertEqual(agent.display_name, "Test Agent")
        mock_from_info.assert_called_once_with(CREDENTIALS)
        mock_agent_client.get_agent.assert_called_once()

    @patch("temba.classifiers.types.dialogflow.client.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_list_intents(self, mock_from_info):
        client = Client(CREDENTIALS)

        mock_intents_client = MagicMock()
        mock_from_info.return_value = mock_intents_client

        mock_intent_1 = MagicMock()
        mock_intent_1.display_name = "book_flight"
        mock_intent_1.name = "projects/test-project/agent/intents/111"

        mock_intent_2 = MagicMock()
        mock_intent_2.display_name = "book_hotel"
        mock_intent_2.name = "projects/test-project/agent/intents/222"

        mock_intents_client.list_intents.return_value = [mock_intent_1, mock_intent_2]

        intents = client.list_intents()
        self.assertEqual(len(list(intents)), 2)
        mock_from_info.assert_called_once_with(CREDENTIALS)


class DialogflowTypeTest(TembaTest):
    @patch("temba.classifiers.types.dialogflow.client.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_get_active_intents_from_api(self, mock_from_info):
        c = Classifier.create(
            self.org,
            self.user,
            DialogflowType.slug,
            "Test Bot",
            CREDENTIALS,
            sync=False,
        )

        mock_intents_client = MagicMock()
        mock_from_info.return_value = mock_intents_client

        mock_intent_1 = MagicMock()
        mock_intent_1.display_name = "book_flight"
        mock_intent_1.name = "projects/test-project/agent/intents/111"

        mock_intent_2 = MagicMock()
        mock_intent_2.display_name = "book_hotel"
        mock_intent_2.name = "projects/test-project/agent/intents/222"

        mock_intents_client.list_intents.return_value = [mock_intent_1, mock_intent_2]

        intents = c.get_type().get_active_intents_from_api(c)
        self.assertEqual(2, len(intents))
        self.assertEqual("book_flight", intents[0].name)
        self.assertEqual("projects/test-project/agent/intents/111", intents[0].external_id)
        self.assertEqual("book_hotel", intents[1].name)
        self.assertEqual("projects/test-project/agent/intents/222", intents[1].external_id)

    @patch("temba.classifiers.types.dialogflow.client.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_get_active_intents_from_api_empty(self, mock_from_info):
        c = Classifier.create(
            self.org,
            self.user,
            DialogflowType.slug,
            "Test Bot",
            CREDENTIALS,
            sync=False,
        )

        mock_intents_client = MagicMock()
        mock_from_info.return_value = mock_intents_client
        mock_intents_client.list_intents.return_value = []

        intents = c.get_type().get_active_intents_from_api(c)
        self.assertEqual(0, len(intents))


class ConnectViewTest(TembaTest):
    @patch("temba.classifiers.models.Classifier.async_sync")
    @patch("temba.classifiers.types.dialogflow.views.Client")
    def test_connect(self, mock_client_cls, mock_async_sync):
        connect_url = reverse("classifiers.classifier_connect")
        response = self.client.get(connect_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(connect_url)

        dialogflow_url = reverse("classifiers.types.dialogflow.connect")
        self.assertContains(response, dialogflow_url)

        # will fail without a file
        response = self.client.post(dialogflow_url, {})
        self.assertFormError(response.context["form"], "file", ["This field is required."])

        # will fail with non-json file
        bad_file = SimpleUploadedFile("creds.txt", b"not json", content_type="text/plain")
        response = self.client.post(dialogflow_url, {"file": bad_file})
        self.assertFormError(
            response.context["form"],
            "file",
            ["File extension \u201ctxt\u201d is not allowed. Allowed extensions are: json."],
        )

        # will fail with json missing required fields
        incomplete = json.dumps({"some_key": "some_value"}).encode()
        bad_json_file = SimpleUploadedFile("creds.json", incomplete, content_type="application/json")
        response = self.client.post(dialogflow_url, {"file": bad_json_file})
        self.assertFormError(response.context["form"], None, "invalid json google service account file")

        # will fail with json missing private_key
        no_key = json.dumps({"project_id": "test-project"}).encode()
        no_key_file = SimpleUploadedFile("creds.json", no_key, content_type="application/json")
        response = self.client.post(dialogflow_url, {"file": no_key_file})
        self.assertFormError(response.context["form"], None, "invalid json google service account file")

        # successful connection - agent accessible
        mock_client_instance = MagicMock()
        mock_client_cls.return_value = mock_client_instance
        mock_agent = MagicMock()
        mock_agent.display_name = "My Dialogflow Agent"
        mock_client_instance.get_agent.return_value = mock_agent

        valid_creds = json.dumps(CREDENTIALS).encode()
        valid_file = SimpleUploadedFile("creds.json", valid_creds, content_type="application/json")
        response = self.client.post(dialogflow_url, {"file": valid_file})
        self.assertEqual(302, response.status_code)

        c = Classifier.objects.get()
        self.assertEqual("My Dialogflow Agent", c.name)
        self.assertEqual("dialogflow", c.classifier_type)
        self.assertEqual("test-project", c.config["project_id"])

        # will fail with duplicate credentials
        valid_file2 = SimpleUploadedFile("creds.json", valid_creds, content_type="application/json")
        response = self.client.post(dialogflow_url, {"file": valid_file2})
        self.assertFormError(response.context["form"], None, "service account credentials already exist")

    @patch("temba.classifiers.models.Classifier.async_sync")
    @patch("temba.classifiers.types.dialogflow.views.Client")
    def test_connect_agent_error_falls_back_to_project_id(self, mock_client_cls, mock_async_sync):
        self.login(self.admin)
        dialogflow_url = reverse("classifiers.types.dialogflow.connect")

        # agent call raises exception, should fall back to project_id as name
        mock_client_instance = MagicMock()
        mock_client_cls.return_value = mock_client_instance
        mock_client_instance.get_agent.side_effect = Exception("connection error")

        valid_creds = json.dumps(CREDENTIALS).encode()
        valid_file = SimpleUploadedFile("creds.json", valid_creds, content_type="application/json")
        response = self.client.post(dialogflow_url, {"file": valid_file})
        self.assertEqual(302, response.status_code)

        c = Classifier.objects.get()
        self.assertEqual("test-project", c.name)

    def test_connect_context_has_override_form(self):
        self.login(self.admin)
        dialogflow_url = reverse("classifiers.types.dialogflow.connect")
        response = self.client.get(dialogflow_url)
        self.assertTrue(response.context["override_form"])


class TrainingClientTest(TembaTest):
    def _make_training_data(self, rows):
        """Helper to build training data dict from list of row dicts."""
        if not rows:
            return {}
        keys = rows[0].keys()
        return {k: [r[k] for r in rows] for k in keys}

    def test_init_default_messages(self):
        languages = ["en", "es"]
        tc = TrainingClient(training_data={}, languages=languages, credential=CREDENTIALS)
        self.assertIn("errors", tc.messages)
        self.assertEqual(tc.messages["errors"], [])
        self.assertEqual(tc.messages["created"], {"en": 0, "es": 0})
        self.assertEqual(tc.messages["updated"], {"en": 0, "es": 0})

    def test_init_custom_messages(self):
        custom_messages = {"errors": ["some error"], "created": {}, "updated": {}}
        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS, messages=custom_messages)
        self.assertEqual(tc.messages, custom_messages)

    def test_get_language_headers(self):
        headers = TrainingClient.get_language_headers("en")
        self.assertEqual(headers["training_phrase"], "questioneng")
        self.assertEqual(headers["answer"], "answereng")
        self.assertEqual(headers["intent"], "intent")

    def test_get_language_headers_chinese(self):
        headers = TrainingClient.get_language_headers("zh")
        self.assertEqual(headers["training_phrase"], "questionchi")
        self.assertEqual(headers["answer"], "answerchi")

    def test_clean_training_phrases(self):
        # None / NaN
        self.assertIsNone(TrainingClient.clean_training_phrases(None))
        self.assertIsNone(TrainingClient.clean_training_phrases(float("nan")))
        self.assertIsNone(TrainingClient.clean_training_phrases(""))
        self.assertIsNone(TrainingClient.clean_training_phrases("   "))

        # list-like string
        result = TrainingClient.clean_training_phrases("['hello', 'world']")
        self.assertEqual(result, ["hello", "world"])

        # simple string
        result = TrainingClient.clean_training_phrases("hello")
        self.assertEqual(result, ["hello"])

        # comma-separated
        result = TrainingClient.clean_training_phrases("hello, world")
        self.assertEqual(result, ["hello", "world"])

    def test_validate_field(self):
        self.assertIsNone(TrainingClient.validate_field(None))
        self.assertIsNone(TrainingClient.validate_field(float("nan")))
        self.assertIsNone(TrainingClient.validate_field(""))
        self.assertIsNone(TrainingClient.validate_field("   "))
        self.assertEqual(TrainingClient.validate_field("hello"), "hello")
        self.assertEqual(TrainingClient.validate_field("  hello  "), "hello")
        self.assertEqual(TrainingClient.validate_field(123), "123")

    def test_intent_message(self):
        msg = TrainingClient.intent_message("Hello there")
        self.assertIsInstance(msg, dialogflow_v2.types.Intent.Message)

    def test_get_training_phrase_from_text(self):
        phrase = TrainingClient.get_training_phrase_from_text("book a flight")
        self.assertIsInstance(phrase, dialogflow_v2.types.Intent.TrainingPhrase)

    def test_get_training_phases_from_text_list(self):
        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        phrases = tc.get_training_phases_from_text_list(["hello", "hi", "hey"])
        self.assertEqual(3, len(phrases))
        for p in phrases:
            self.assertIsInstance(p, dialogflow_v2.types.Intent.TrainingPhrase)

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_extract_intents_creates_new(self, mock_from_info):
        training_data = self._make_training_data(
            [
                {"intent": "greet", "questioneng": "['hello', 'hi']", "answereng": "Hello!"},
                {"intent": "bye", "questioneng": "['goodbye']", "answereng": "See you!"},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)

        # no existing intents
        tc.extract_intents_from_data({}, "en")
        self.assertEqual(2, len(tc.intents_requests))
        self.assertEqual("create", tc.intents_requests[0]["type"])
        self.assertEqual("greet", tc.intents_requests[0]["intent"].display_name)
        self.assertEqual("create", tc.intents_requests[1]["type"])
        self.assertEqual("bye", tc.intents_requests[1]["intent"].display_name)

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_extract_intents_updates_existing(self, mock_from_info):
        training_data = self._make_training_data(
            [
                {"intent": "greet", "questioneng": "['hello']", "answereng": "Hello!"},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)

        existing_intent = MagicMock()
        existing_intent.display_name = "greet"
        tc.extract_intents_from_data({"greet": existing_intent}, "en")

        self.assertEqual(1, len(tc.intents_requests))
        self.assertEqual("update", tc.intents_requests[0]["type"])

    def test_extract_intents_missing_columns(self):
        training_data = self._make_training_data(
            [
                {"wrong_col": "greet"},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)
        tc.extract_intents_from_data({}, "en")

        self.assertEqual(0, len(tc.intents_requests))
        self.assertTrue(any("Missing required columns" in e for e in tc.messages["errors"]))

    def test_extract_intents_skips_invalid_rows(self):
        training_data = self._make_training_data(
            [
                {"intent": "", "questioneng": "['hello']", "answereng": "Hello!"},
                {"intent": "greet", "questioneng": "", "answereng": "Hello!"},
                {"intent": "greet2", "questioneng": "['hi']", "answereng": ""},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)
        tc.extract_intents_from_data({}, "en")

        self.assertEqual(0, len(tc.intents_requests))
        self.assertEqual(3, len(tc.messages["errors"]))

    def test_extract_intents_groups_same_name(self):
        training_data = self._make_training_data(
            [
                {"intent": "greet", "questioneng": "['hello']", "answereng": "Hi!"},
                {"intent": "greet", "questioneng": "['hey', 'yo']", "answereng": "Hello!"},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)
        tc.extract_intents_from_data({}, "en")

        # grouped into one intent
        self.assertEqual(1, len(tc.intents_requests))
        intent = tc.intents_requests[0]["intent"]
        # should have 3 training phrases total
        self.assertEqual(3, len(intent.training_phrases))

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_push_to_dialogflow_success(self, mock_from_info):
        mock_client = MagicMock()
        mock_from_info.return_value = mock_client

        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        intent = dialogflow_v2.types.Intent(display_name="greet")
        intents = [{"type": "create", "intent": intent, "language": "en"}]

        with patch("temba.classifiers.types.dialogflow.train_bot.time.sleep"):
            index, retry, completed = tc.push_to_dialogflow(intents)

        self.assertEqual(1, index)
        self.assertFalse(retry)
        self.assertTrue(completed)
        self.assertEqual(1, tc.messages["created"]["en"])

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_push_to_dialogflow_empty(self, mock_from_info):
        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)

        index, retry, completed = tc.push_to_dialogflow([], start_index=0)
        self.assertEqual(0, index)
        self.assertFalse(retry)
        self.assertTrue(completed)

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_push_to_dialogflow_resource_exhausted_retries(self, mock_from_info):
        mock_client = MagicMock()
        mock_from_info.return_value = mock_client

        # simulate ResourceExhausted on create_intent that exhausts all retries
        mock_client.create_intent.side_effect = google_exceptions.ResourceExhausted("rate limited")

        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        intent = dialogflow_v2.types.Intent(display_name="greet")
        intents = [{"type": "create", "intent": intent, "language": "en"}]

        with patch("temba.classifiers.types.dialogflow.train_bot.time.sleep"):
            index, retry, completed = tc.push_to_dialogflow(intents)

        self.assertTrue(retry)
        self.assertFalse(completed)

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_push_to_dialogflow_generic_error(self, mock_from_info):
        mock_client = MagicMock()
        mock_from_info.return_value = mock_client
        mock_client.create_intent.side_effect = ValueError("unexpected error")

        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        intent = dialogflow_v2.types.Intent(display_name="greet")
        intents = [{"type": "create", "intent": intent, "language": "en"}]

        with patch("temba.classifiers.types.dialogflow.train_bot.time.sleep"):
            index, retry, completed = tc.push_to_dialogflow(intents)

        self.assertFalse(retry)
        self.assertFalse(completed)
        self.assertTrue(any("unexpected error" in e for e in tc.messages["errors"]))

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_push_to_dialogflow_already_exists_skipped(self, mock_from_info):
        mock_client = MagicMock()
        mock_from_info.return_value = mock_client
        mock_client.create_intent.side_effect = google_exceptions.InvalidArgument("Intent 'greet' already exists")

        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        intent = dialogflow_v2.types.Intent(display_name="greet")
        intents = [{"type": "create", "intent": intent, "language": "en"}]

        with patch("temba.classifiers.types.dialogflow.train_bot.time.sleep"):
            index, retry, completed = tc.push_to_dialogflow(intents)

        self.assertEqual(1, index)
        self.assertFalse(retry)
        self.assertTrue(completed)
        self.assertTrue(any("already exists" in e for e in tc.messages["errors"]))

    def test_intent_serialization(self):
        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        tc.intents_requests = [
            {"type": "create", "intent": "test_intent", "language": "en"},
        ]
        serialized = tc.intent_list_to_str()
        deserialized = TrainingClient.intent_str_to_list(serialized)
        self.assertEqual(tc.intents_requests, deserialized)

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_process_sync_intents_for_lang(self, mock_from_info):
        mock_client = MagicMock()
        mock_from_info.return_value = mock_client

        existing_intent = MagicMock()
        existing_intent.display_name = "existing_greet"
        mock_client.list_intents.return_value = [existing_intent]

        training_data = self._make_training_data(
            [
                {"intent": "existing_greet", "questioneng": "['hello']", "answereng": "Hi!"},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)
        tc.process_sync_intents_for_lang("en")

        self.assertEqual(1, len(tc.intents_requests))
        self.assertEqual("update", tc.intents_requests[0]["type"])

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_build_intent_list(self, mock_from_info):
        mock_client = MagicMock()
        mock_from_info.return_value = mock_client
        mock_client.list_intents.return_value = []

        training_data = self._make_training_data(
            [
                {"intent": "greet", "questioneng": "['hello']", "answereng": "Hi!"},
            ]
        )
        tc = TrainingClient(training_data=training_data, languages=["en"], credential=CREDENTIALS)
        tc.build_intent_list()

        self.assertEqual(1, len(tc.intents_requests))

    @patch("temba.classifiers.types.dialogflow.train_bot.dialogflow_v2.IntentsClient.from_service_account_info")
    def test_build_intent_list_handles_errors(self, mock_from_info):
        mock_from_info.side_effect = Exception("connection failed")

        tc = TrainingClient(training_data={}, languages=["en"], credential=CREDENTIALS)
        tc.build_intent_list()

        self.assertTrue(any("connection failed" in e for e in tc.messages["errors"]))
