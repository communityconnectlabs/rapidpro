from ...models import ClassifierType, Intent
from .views import ConnectView


class DialogflowType(ClassifierType):
    """
    Type for classifiers from Luis.ai
    """

    name = "Dialogflow"
    slug = "dialogflow"
    icon = "icon-google-plus"
    connect_view = ConnectView
    connect_blurb = """
    <a href="https://dialogflow.com">Dialogflow</a> is a natural language understanding platform that makes it easy to
     design and integrate a conversational user interface into your mobile app, web application, device, bot,
     interactive voice response system, and so on.
    """

    form_blurb = """
    You can find the json credentials to connect your bot under the credentials page at Google's console. You can also
     use this link https://consle.cloud.google.com/apis/credentials?project=REPLACE_PROJECT_ID.
    """

    def get_active_intents_from_api(self, classifier):
        """
        Gets the current intents defined by this app, in LUIS that's an attribute of the app version
        """
        from temba.classifiers.types.dialogflow.client import Client

        intents = []
        client = Client(classifier.config)
        intent_data = client.list_intents()
        for intent in intent_data:
            intents.append(Intent(name=intent.display_name, external_id=intent.name))

        return intents
